#!/usr/bin/env python3
"""
Demo script for AI system integrated with xApp over TCP using Membus architecture.

This matches the system design:
- KPI Stream → Minirocket → Reasoner
- SLO Intents → Reasoner
- Reasoner ↔ Knowledge Base ↔ Proposer
- KPI Stream → Loop_Observer → Learner (DQN)
- Proposer ↔ Predictor ↔ Learner (DQN)
- Proposer → Actor → Control Actions
"""

from __future__ import annotations
import argparse
import asyncio
import csv
import json
import signal
import struct
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

# Add parent directory to path
THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR.parent.parent.parent))

from ain.loop.observer_rl import Intent, RLObserver
from ain.loop.predictor import SlateDQNPredictor
from ain.loop.proposer import ActionSpace, ProposerSampler, CacheLibrary, PLAYBOOK_K, CANDIDATE_N, COOLDOWN_STEPS, cooldown_key
from ain.loop.actor import Actor
from ain.bus.mem import MemBus
from ain.agents.utils import make_msg

# Import agents
from ain.agents.minirocket_agent import MinirocketAgent
from ain.agents.reasoner_agent_enhanced import EnhancedReasonerAgent
from ain.agents.proposer_agent import ProposerAgent
from ain.agents.predictor_agent import PredictorAgent
from ain.agents.observer_agent import RLObserverAgent
from ain.agents.actor_agent import ActorAgent

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class XAppKPIAdapter:
    """Converts xApp KPI format to internal format expected by RLObserver."""
    
    @staticmethod
    def convert_xapp_kpi_to_internal(xapp_kpi: Dict[str, Any], meid: str) -> Dict[str, Any]:
        """Convert xApp KPI format to internal format."""
        kpi_data = xapp_kpi.get("kpi", {})
        
        cell_metrics = {}
        ue_metrics = []
        
        # Extract from measurements array
        measurements = kpi_data.get("measurements", [])
        ues = kpi_data.get("ues", [])
        
        # Extract UE metrics from ues array
        # Based on ue_kpis.csv: timestamp,meid,cell_id,ue_id,UE_PDCP_Delay_DL_ms,...
        if ues:
            for ue in ues:
                ue_metric = {}
                ue_id = ue.get("ue_id") or ue.get("ueId") or ue.get("id")
                if not ue_id:
                    continue
                
                ue_metric["ue_id"] = str(ue_id)
                ue_metric["cell_id"] = ue.get("cell_id") or ue.get("cellId") or cell_metrics.get("cell_id", "unknown")
                
                # Extract UE-specific metrics - USE ACTUAL METRIC NAMES
                # UE LEVEL: UE_PDCP_Delay_DL_ms, DRB_EstabSucc_5QI_UEID, TB_TotNbrDlInitial_Qpsk_UEID, 
                #           TB_TotNbrDlInitial_64Qam_UEID, UE_PRB_Used_DL, UE_Throughput_DL_Mbps
                if "UE_PDCP_Delay_DL_ms" in ue:
                    ue_metric["UE_PDCP_Delay_DL_ms"] = float(ue["UE_PDCP_Delay_DL_ms"])
                if "DRB_EstabSucc_5QI_UEID" in ue:
                    ue_metric["DRB_EstabSucc_5QI_UEID"] = float(ue["DRB_EstabSucc_5QI_UEID"])
                if "TB_TotNbrDlInitial_Qpsk_UEID" in ue:
                    ue_metric["TB_TotNbrDlInitial_Qpsk_UEID"] = int(ue["TB_TotNbrDlInitial_Qpsk_UEID"])
                if "TB_TotNbrDlInitial_64Qam_UEID" in ue:
                    ue_metric["TB_TotNbrDlInitial_64Qam_UEID"] = int(ue["TB_TotNbrDlInitial_64Qam_UEID"])
                if "UE_PRB_Used_DL" in ue:
                    ue_metric["UE_PRB_Used_DL"] = float(ue["UE_PRB_Used_DL"])
                if "UE_Throughput_DL_Mbps" in ue:
                    ue_metric["UE_Throughput_DL_Mbps"] = float(ue["UE_Throughput_DL_Mbps"])
                if "UE_Throughput_DL_Mbps" in ue:
                    ue_metric["thr_dl_bps"] = float(ue["UE_Throughput_DL_Mbps"]) * 1e6
                if "UE_PRB_Used_DL" in ue:
                    ue_metric["prb_used_dl"] = float(ue["UE_PRB_Used_DL"])
                
                # Extract from nested measurements if available
                ue_measurements = ue.get("measurements", [])
                for m in ue_measurements:
                    name = m.get("name", "").lower()
                    value = m.get("value", 0)
                    if "delay" in name or "latency" in name:
                        ue_metric["delay_p95_ms"] = float(value)
                    elif "throughput" in name or "thr" in name:
                        if "dl" in name:
                            ue_metric["thr_dl_bps"] = float(value) * 1e6
                        elif "ul" in name:
                            ue_metric["thr_ul_bps"] = float(value) * 1e6
                    elif "prb" in name and "used" in name:
                        ue_metric["prb_used_dl"] = float(value)
                    elif "bler" in name:
                        if "dl" in name:
                            ue_metric["bler_dl"] = float(value) / 100.0
                        elif "ul" in name:
                            ue_metric["bler_ul"] = float(value) / 100.0
                    elif "cqi" in name:
                        ue_metric["cqi_avg"] = float(value)
                    elif "mcs" in name:
                        if "dl" in name:
                            ue_metric["mcs_dl_avg"] = int(value)
                        elif "ul" in name:
                            ue_metric["mcs_ul_avg"] = int(value)
                
                if ue_metric:
                    ue_metrics.append(ue_metric)
        
        if measurements:
            for m in measurements:
                name = m.get("name", "")
                value = m.get("value", 0)
                
                # Map xApp measurement names to actual metric names
                if "UE_PDCP_Delay_DL_ms" in name or ("delay" in name.lower() and "pdcp" in name.lower()):
                    cell_metrics["UE_PDCP_Delay_DL_ms"] = float(value)
                elif "throughput" in name.lower() or "thr" in name.lower():
                    if "dl" in name.lower():
                        cell_metrics["thr_dl_bps"] = float(value) * 1e6
                    elif "ul" in name.lower():
                        cell_metrics["thr_ul_bps"] = float(value) * 1e6
                elif "bler" in name.lower():
                    if "dl" in name.lower():
                        cell_metrics["bler_dl"] = float(value) / 100.0
                    elif "ul" in name.lower():
                        cell_metrics["bler_ul"] = float(value) / 100.0
                elif "cqi" in name.lower():
                    cell_metrics["cqi_avg"] = float(value)
                elif "mcs" in name.lower():
                    if "dl" in name.lower():
                        cell_metrics["mcs_dl_avg"] = int(value)
                    elif "ul" in name.lower():
                        cell_metrics["mcs_ul_avg"] = int(value)
                elif "PRB_Used_DL" in name or ("prb" in name.lower() and "used" in name.lower()):
                    cell_metrics["PRB_Used_DL"] = float(value)
                elif "PRB_Total_DL" in name or ("prb" in name.lower() and ("total" in name.lower() or "avail" in name.lower())):
                    cell_metrics["PRB_Total_DL"] = float(value)
                elif "Mean_Active_UEs_DL" in name or ("active" in name.lower() and "ue" in name.lower()):
                    cell_metrics["Mean_Active_UEs_DL"] = int(value)
                elif "DL_TB_QPSK_Count" in name:
                    cell_metrics["DL_TB_QPSK_Count"] = int(value)
                elif "DL_TB_64QAM_Count" in name:
                    cell_metrics["DL_TB_64QAM_Count"] = int(value)
        
        # Extract from raw fields if available - USE ACTUAL METRIC NAMES
        # GNB LEVEL: UE_PDCP_Delay_DL_ms, DL_TB_QPSK_Count, DL_TB_64QAM_Count, PRB_Used_DL, Mean_Active_UEs_DL
        if "UE_PDCP_Delay_DL_ms" in kpi_data:
            cell_metrics["UE_PDCP_Delay_DL_ms"] = float(kpi_data["UE_PDCP_Delay_DL_ms"])
        if "PRB_Used_DL" in kpi_data:
            cell_metrics["PRB_Used_DL"] = float(kpi_data["PRB_Used_DL"])
        if "PRB_Total_DL" in kpi_data or "PRB_Available_DL" in kpi_data:
            cell_metrics["PRB_Total_DL"] = float(kpi_data.get("PRB_Total_DL", kpi_data.get("PRB_Available_DL", 0)))
        if "Mean_Active_UEs_DL" in kpi_data:
            cell_metrics["Mean_Active_UEs_DL"] = int(kpi_data["Mean_Active_UEs_DL"])
        if "DL_TB_QPSK_Count" in kpi_data:
            cell_metrics["DL_TB_QPSK_Count"] = int(kpi_data["DL_TB_QPSK_Count"])
        if "DL_TB_64QAM_Count" in kpi_data:
            cell_metrics["DL_TB_64QAM_Count"] = int(kpi_data["DL_TB_64QAM_Count"])
        
        # Compute PRB_Used_DL_ratio if we have both values
        if "PRB_Used_DL" in cell_metrics and "PRB_Total_DL" not in cell_metrics:
            # Default to 100 RBs if total not available (common default)
            cell_metrics["PRB_Total_DL"] = 100.0
        
        # Normalize and set cell_id
        cell_id_raw = kpi_data.get("cellObjectID") or kpi_data.get("cell_id") or "CELL_001"
        # Normalize cell_id: convert numeric strings to CELL_XXX format
        if cell_id_raw and str(cell_id_raw).isdigit():
            cell_id = f"CELL_{cell_id_raw}"
        elif cell_id_raw and cell_id_raw.startswith("CELL_"):
            cell_id = cell_id_raw
        elif cell_id_raw != "unknown":
            cell_id = f"CELL_{cell_id_raw}" if not cell_id_raw.startswith("CELL_") else cell_id_raw
        else:
            cell_id = "CELL_001"
        if "cell_id" not in cell_metrics:
            cell_metrics["cell_id"] = cell_id
        
        # Extract and store node_id (important for fragment merging)
        node_id = kpi_data.get("node_id") or kpi_data.get("nodeId") or xapp_kpi.get("node_id")
        if node_id is not None:
            cell_metrics["node_id"] = int(node_id)
        # If not in KPI data, try to infer from cell_id pattern
        elif "node_id" not in cell_metrics:
            # Infer from cell_id: CELL_1111 -> node 1, CELL_2222 -> node 2, etc.
            if cell_id.startswith("CELL_"):
                numeric_part = cell_id.replace("CELL_", "")
                if numeric_part.isdigit() and len(numeric_part) > 0:
                    cell_metrics["node_id"] = int(numeric_part[0])  # First digit
                else:
                    cell_metrics["node_id"] = 2  # Default to gNB node
            elif cell_id == "unknown":
                cell_metrics["node_id"] = 2  # Default to gNB node for unknown
            else:
                cell_metrics["node_id"] = 2  # Default to gNB node
        
        # Note: We intentionally do NOT set defaults for missing metrics.
        # The observer will handle missing features using NaN and feature completeness checks.
        
        # Build internal format
        internal_kpi = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "Header": {
                "ric_instance_id": meid,
                "function_id": "kpm_func_v1",
                "kpm_version": "2.0",
                "granularity_period_ms": 1000,
                "window_start": datetime.now(timezone.utc).isoformat(),
                "window_end": datetime.now(timezone.utc).isoformat(),
                "sequence_number": int(datetime.now().timestamp() * 1000) % 1000000,
            },
            "CellMetrics": cell_metrics,
            "UEMetrics": ue_metrics,
        }
        
        return internal_kpi


class PlaybookToCommandConverter:
    """Converts playbook actions to xApp control commands."""
    
    @staticmethod
    def playbook_to_commands(
        playbook, 
        meid: str, 
        node_id: int = 0,
        cell_to_node_map: Optional[Dict[str, int]] = None,
        ue_to_node_map: Optional[Dict[str, int]] = None,
        ue_to_cell_map: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Convert playbook actions to xApp control commands.
        
        Args:
            playbook: Playbook with actions
            meid: Management entity ID
            node_id: Default node ID
            cell_to_node_map: Optional mapping from cell_id to node_id
            ue_to_node_map: Optional mapping from ue_id to node_id
            ue_to_cell_map: Optional mapping from ue_id to cell_id
        """
        commands = []
        cell_to_node_map = cell_to_node_map or {}
        ue_to_node_map = ue_to_node_map or {}
        ue_to_cell_map = ue_to_cell_map or {}
        
        for action in playbook.actions:
            cmd = None
            
            # Determine node_id for this action:
            # 1. Check if action.params has explicit "node"
            # 2. For UE-scoped actions: check ue_to_node_map
            # 3. For CELL-scoped actions: check cell_to_node_map
            # 4. Fall back to default node_id
            action_node_id = action.params.get("node", node_id)
            
            if action.scope == "UE" and action.ue_id:
                # UE-scoped action: try to get node_id from UE mapping
                if action.ue_id in ue_to_node_map:
                    action_node_id = ue_to_node_map[action.ue_id]
                    logger.info(f"Using node_id={action_node_id} for UE {action.ue_id} from mapping")
                elif action.ue_id in ue_to_cell_map:
                    # Try via cell mapping
                    cell_id = ue_to_cell_map[action.ue_id]
                    if cell_id in cell_to_node_map:
                        action_node_id = cell_to_node_map[cell_id]
                        logger.info(f"Using node_id={action_node_id} for UE {action.ue_id} via cell {cell_id}")
                else:
                    logger.warning(f"No node mapping found for UE {action.ue_id}, using default node_id={action_node_id}")
            
            elif action.scope == "CELL" and action.cell_id:
                # CELL-scoped action: try to get node_id from cell mapping
                if action.cell_id in cell_to_node_map:
                    action_node_id = cell_to_node_map[action.cell_id]
                    logger.info(f"Using node_id={action_node_id} for cell {action.cell_id} from mapping")
                else:
                    # Fallback: if configured cell_id not found, use the first available cell from KPIs
                    # This handles the case where system is configured with CELL_001 but xApp sends CELL_1111
                    if cell_to_node_map:
                        actual_cell_id = list(cell_to_node_map.keys())[0]
                        action_node_id = cell_to_node_map[actual_cell_id]
                        logger.warning(f"No node mapping found for configured cell {action.cell_id}, using actual cell {actual_cell_id} with node_id={action_node_id}. Available cells: {list(cell_to_node_map.keys())}")
                    else:
                        logger.warning(f"No node mapping found for cell {action.cell_id}, using default node_id={action_node_id}. Available cells: {list(cell_to_node_map.keys())}")
            
            # Log the node_id being used
            logger.info(f"Action {action.type} ({action.scope}) will use node_id={action_node_id}")
            
            if action.type == "MCS_CAP":
                # Map MCS_CAP to set-mcs
                # set-mcs requires node (gNB node with MmWaveEnbNetDevice)
                if action_node_id == 0:
                    logger.warning(f"MCS_CAP requires a valid node_id (gNB node), but got 0. Skipping command.")
                    continue
                
                mcs_value = action.params.get("dl_mcs_max", 18)
                cmd = {
                    "type": "control",
                    "meid": meid,
                    "cmd": {
                        "cmd": "set-mcs",
                        "node": int(action_node_id),  # Required: gNB node
                        "mcs": int(mcs_value)
                    }
                }
                # Add UE ID if this is a UE-scoped action
                if action.scope == "UE" and action.ue_id:
                    cmd["cmd"]["ue_id"] = action.ue_id
                    logger.info(f"MCS_CAP command for UE {action.ue_id} on gNB node {action_node_id}")
                else:
                    logger.info(f"MCS_CAP command for gNB node {action_node_id}, mcs={mcs_value}")
                    
            elif action.type == "PRB_WEIGHT":
                # Map PRB_WEIGHT to set-bandwidth (approximate)
                # set-bandwidth: node is optional (if 0 or not provided, searches all nodes)
                weight = action.params.get("weight", 1.0)
                bandwidth = int(100 * weight)
                cmd = {
                    "type": "control",
                    "meid": meid,
                    "cmd": {
                        "cmd": "set-bandwidth",
                        "bandwidth": bandwidth
                    }
                }
                # Only include node if we have a valid mapping (not 0)
                # If node is 0 or not found, omit it to search all nodes
                if action_node_id != 0:
                    cmd["cmd"]["node"] = int(action_node_id)
                    logger.info(f"PRB_WEIGHT command for gNB node {action_node_id}, bandwidth={bandwidth}")
                else:
                    logger.info(f"PRB_WEIGHT command for all nodes (node not specified), bandwidth={bandwidth}")
                
                # Add UE ID if this is a UE-scoped action
                if action.scope == "UE" and action.ue_id:
                    cmd["cmd"]["ue_id"] = action.ue_id
                    logger.info(f"  → Targeting UE {action.ue_id}")
                    
            elif action.type in ("TX_POWER", "POWER_CONTROL"):
                # Map TX_POWER/POWER_CONTROL to set-enb-txpower
                # set-enb-txpower requires node (gNB node with MmWaveEnbNetDevice)
                if action_node_id == 0:
                    logger.warning(f"TX_POWER requires a valid node_id (gNB node), but got 0. Skipping command.")
                    continue
                
                # AI must provide txPowerDbm value - no default, let AI decide
                tx_power_dbm = action.params.get("txPowerDbm") or action.params.get("tx_power_dbm")
                if tx_power_dbm is None:
                    logger.warning(f"{action.type} action missing txPowerDbm parameter, skipping: {action.params}")
                    continue
                cmd = {
                    "type": "control",
                    "meid": meid,
                    "cmd": {
                        "cmd": "set-enb-txpower",
                        "node": int(action_node_id),  # Required: gNB node
                        "txPowerDbm": float(tx_power_dbm)
                    }
                }
                # Add UE ID if this is a UE-scoped action
                if action.scope == "UE" and action.ue_id:
                    cmd["cmd"]["ue_id"] = action.ue_id
                    logger.info(f"TX_POWER command for UE {action.ue_id} on gNB node {action_node_id}, txPower={tx_power_dbm}dBm")
                else:
                    logger.info(f"TX_POWER command for gNB node {action_node_id}, txPower={tx_power_dbm}dBm")
                    
            elif action.type in ("SCHEDULER_POLICY", "SLICE_QOS"):
                logger.warning(f"{action.type} action not directly supported: {action.params}")
                continue
            elif action.type == "REPORTING":
                continue
            
            if cmd:
                commands.append(cmd)
        
        return commands


class XAppTCPServer:
    """TCP server for xApp communication that publishes to membus."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 6000, bus: Optional[MemBus] = None, kpi_csv_file: Optional[str] = None, commands_enabled: bool = True):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.clients: Dict[str, asyncio.StreamWriter] = {}
        self.bus = bus
        self.meid_map: Dict[str, str] = {}
        self.kpi_adapter = XAppKPIAdapter()
        self.commands_enabled = commands_enabled
        # UE and node tracking
        self.cell_to_node_map: Dict[str, int] = {}  # cell_id -> node_id
        self.ue_to_node_map: Dict[str, int] = {}  # ue_id -> node_id
        self.ue_to_cell_map: Dict[str, str] = {}  # ue_id -> cell_id
        self.active_ues: Dict[str, Dict[str, Any]] = {}  # ue_id -> latest UE metrics
        self.client_node_map: Dict[str, int] = {}  # client_id -> default node_id
        # CSV logging - default to project root directory
        if kpi_csv_file is None:
            # Find project root (go up from src/demo/ain/RL_demo to project root)
            project_root = THIS_DIR.parent.parent.parent.parent
            self.kpi_csv_file = project_root / "kpms.csv"
        else:
            self.kpi_csv_file = Path(kpi_csv_file)
        self.kpi_csv_initialized = False
        self._init_kpi_csv()
    
    def _init_kpi_csv(self):
        """Initialize KPI CSV file with base headers if it doesn't exist."""
        if not self.kpi_csv_file.exists():
            with open(self.kpi_csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                # Only write base columns - measurement columns will be added dynamically
                writer.writerow(['timestamp', 'meid', 'cell_id', 'node_id', 'format'])
            self.kpi_csv_initialized = True
            logger.info(f"Initialized KPI CSV file: {self.kpi_csv_file}")
        else:
            self.kpi_csv_initialized = True
    
    def _write_kpi_to_csv(self, kpi_data: Dict[str, Any], meid: str, cell_id: str, node_id: Optional[int] = None, is_ue_data: bool = False):
        """Write KPI data to CSV file with dynamic column handling.
        
        Args:
            kpi_data: KPI data dictionary
            meid: Management entity ID
            cell_id: Cell ID (or UE ID if is_ue_data=True)
            node_id: Node ID
            is_ue_data: If True, prefix metrics with "UE_" to distinguish from cell-level
        """
        if not self.kpi_csv_initialized:
            self._init_kpi_csv()
        
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            measurements = kpi_data.get("measurements", [])
            
            # Extract ALL metrics from measurements array (dynamic, like relay server)
            metrics = {}
            for m in measurements:
                name = m.get("name", "")
                if not name:
                    # Try ID if name not available
                    meas_id = m.get("id")
                    if meas_id is not None:
                        name = f"id_{meas_id}"
                    else:
                        continue
                
                value = m.get("value", "")
                # Normalize name (replace dots/spaces with underscores, like relay server)
                csv_name = name.replace(".", "_").replace(" ", "_")
                # Prefix with UE_ if this is UE data
                if is_ue_data and not csv_name.startswith("UE_"):
                    csv_name = f"UE_{csv_name}"
                metrics[csv_name] = value
            
            # Check if we need to add new columns to CSV
            # Read existing file to get current fieldnames
            existing_fieldnames = []
            if self.kpi_csv_file.exists():
                with open(self.kpi_csv_file, 'r') as f:
                    reader = csv.DictReader(f)
                    existing_fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            
            # Base fieldnames (always present)
            base_fieldnames = ['timestamp', 'meid', 'cell_id', 'node_id', 'format']
            
            # Get all metric names (from existing file + new metrics)
            all_metric_names = set()
            if existing_fieldnames:
                # Get metric columns (everything except base columns)
                all_metric_names = set(existing_fieldnames) - set(base_fieldnames)
            
            # Add new metric names
            all_metric_names.update(metrics.keys())
            
            # Sort metric names for consistent column order
            sorted_metric_names = sorted(all_metric_names)
            fieldnames = base_fieldnames + sorted_metric_names
            
            # If we have new columns, rewrite the file with new header
            if set(fieldnames) != set(existing_fieldnames or base_fieldnames):
                # Read all existing rows
                existing_rows = []
                if self.kpi_csv_file.exists() and existing_fieldnames:
                    with open(self.kpi_csv_file, 'r') as f:
                        reader = csv.DictReader(f)
                        existing_rows = list(reader)
                
                # Rewrite file with new header
                with open(self.kpi_csv_file, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                    writer.writeheader()
                    for row in existing_rows:
                        writer.writerow(row)
            
            # Write new row
            row = {
                'timestamp': timestamp,
                'meid': meid,
                'cell_id': cell_id,
                'node_id': node_id if node_id is not None else '',
                'format': kpi_data.get("format", "F1"),
            }
            
            # Add all metrics
            row.update(metrics)
            
            with open(self.kpi_csv_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writerow(row)
                
        except Exception as e:
            logger.error(f"Error writing KPI to CSV: {e}", exc_info=True)
        
    async def start(self):
        """Start the TCP server."""
        self.server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        logger.info(f"xApp TCP server started on {self.host}:{self.port}")
        
    async def stop(self):
        """Stop the TCP server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            
    async def _recv_all(self, reader: asyncio.StreamReader, n: int) -> Optional[bytes]:
        """Receive exactly n bytes."""
        data = b""
        while len(data) < n:
            chunk = await reader.read(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data
            
    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming client connections."""
        client_addr = writer.get_extra_info('peername')
        client_id = f"{client_addr[0]}:{client_addr[1]}"
        
        logger.info(f"xApp client connected: {client_id}")
        self.clients[client_id] = writer
        
        try:
            while True:
                header = await self._recv_all(reader, 4)
                if header is None:
                    logger.info(f"xApp client {client_id} disconnected")
                    break
                    
                length = struct.unpack("!I", header)[0]
                
                if length == 0 or length > 1024 * 1024:
                    logger.error(f"Invalid frame length={length}, closing connection")
                    break
                
                body = await self._recv_all(reader, length)
                if body is None:
                    logger.error("Incomplete frame body")
                    break
                
                try:
                    text = body.decode("utf-8", errors="replace")
                    message = json.loads(text)
                    
                    msg_type = message.get("type", "unknown")
                    logger.info(f"Received {msg_type} message from {client_id}")
                    
                    if msg_type == "kpi":
                        meid = message.get("meid", "unknown")
                        self.meid_map[client_id] = meid
                        
                        # Extract and track UE/node information
                        kpi_data = message.get("kpi", {})
                        
                        # Debug: Log raw KPI structure (first few messages only to avoid spam)
                        if not hasattr(self, '_kpi_debug_logged'):
                            self._kpi_debug_logged = set()
                        if client_id not in self._kpi_debug_logged:
                            logger.info(f"[DEBUG] Raw KPI message structure from {client_id}:")
                            logger.info(f"  Full message keys: {list(message.keys())}")
                            logger.info(f"  KPI data keys: {list(kpi_data.keys())}")
                            logger.info(f"  KPI data sample (first 500 chars): {str(kpi_data)[:500]}")
                            if "measurements" in kpi_data:
                                logger.info(f"  Measurements array length: {len(kpi_data.get('measurements', []))}")
                                if kpi_data.get("measurements"):
                                    logger.info(f"  First measurement: {kpi_data['measurements'][0]}")
                            self._kpi_debug_logged.add(client_id)
                        cell_id_raw = kpi_data.get("cellObjectID") or kpi_data.get("cell_id") or "unknown"
                        
                        # Normalize cell_id: convert numeric strings to CELL_XXX format
                        # e.g., "1111" -> "CELL_1111", "0000" -> "CELL_0000"
                        if cell_id_raw != "unknown" and cell_id_raw and str(cell_id_raw).isdigit():
                            cell_id = f"CELL_{cell_id_raw}"
                        elif cell_id_raw and cell_id_raw.startswith("CELL_"):
                            cell_id = cell_id_raw
                        elif cell_id_raw != "unknown":
                            cell_id = f"CELL_{cell_id_raw}"  # Prefix if not already prefixed
                        else:
                            cell_id = "unknown"
                        
                        # Extract node_id if available
                        node_id = kpi_data.get("node_id") or kpi_data.get("nodeId") or message.get("node_id")
                        if node_id is not None:
                            node_id = int(node_id)
                            self.client_node_map[client_id] = node_id
                            if cell_id != "unknown":
                                self.cell_to_node_map[cell_id] = node_id
                            logger.info(f"Extracted node_id={node_id} for cell_id={cell_id}, client={client_id}")
                        else:
                            # Try to infer node_id from cell_id pattern or use default
                            # If cell_id is numeric (like "1111"), try to infer node_id
                            # Common pattern: cell_id "1111" might map to node 1, "0000" to node 0
                            inferred_node_id = None
                            if cell_id != "unknown":
                                # Try existing mapping first
                                if cell_id in self.cell_to_node_map:
                                    inferred_node_id = self.cell_to_node_map[cell_id]
                                    self.client_node_map[client_id] = inferred_node_id
                                    logger.info(f"Using existing node_id={inferred_node_id} for cell_id={cell_id} from mapping")
                                # Try to infer from cell_id pattern (heuristic: extract first digit from numeric part)
                                # Handle both "1111" and "CELL_1111" formats
                                numeric_part = None
                                if cell_id_raw and str(cell_id_raw).isdigit():
                                    numeric_part = str(cell_id_raw)
                                elif cell_id and cell_id.startswith("CELL_"):
                                    # Extract numeric part from "CELL_1111" -> "1111"
                                    numeric_part = cell_id.replace("CELL_", "")
                                    if not numeric_part.isdigit():
                                        numeric_part = None
                                
                                if numeric_part and numeric_part.isdigit():
                                    # Extract first digit: "1111" -> 1, "0000" -> 0, "2222" -> 2
                                    first_digit = int(numeric_part[0])
                                    inferred_node_id = first_digit
                                    self.cell_to_node_map[cell_id] = inferred_node_id
                                    self.client_node_map[client_id] = inferred_node_id
                                    logger.info(f"Inferred node_id={inferred_node_id} for cell_id={cell_id} (extracted from first digit of numeric part '{numeric_part}')")
                                else:
                                    logger.warning(f"No node_id found in KPI for cell_id={cell_id}, client={client_id}. Available mappings: {list(self.cell_to_node_map.keys())}")
                            else:
                                logger.warning(f"No node_id found in KPI for cell_id={cell_id}, client={client_id}. Available mappings: {list(self.cell_to_node_map.keys())}")
                        
                        # Extract and track UE information
                        ues = kpi_data.get("ues", [])
                        for ue in ues:
                            ue_id = ue.get("ue_id") or ue.get("ueId") or ue.get("id")
                            if ue_id:
                                ue_id = str(ue_id)
                                # Map UE to cell
                                ue_cell_id = ue.get("cell_id") or ue.get("cellId") or cell_id
                                if ue_cell_id != "unknown":
                                    self.ue_to_cell_map[ue_id] = ue_cell_id
                                
                                # Extract UE node_id from UE object (xApp sends node_id=3 for UEs)
                                ue_node_id = ue.get("node_id") or ue.get("nodeId")
                                if ue_node_id is not None:
                                    ue_node_id = int(ue_node_id)
                                    self.ue_to_node_map[ue_id] = ue_node_id
                                    logger.info(f"Extracted UE node_id={ue_node_id} for UE {ue_id}")
                                # Map UE to node (via cell or direct) - fallback if UE object doesn't have node_id
                                elif node_id is not None:
                                    self.ue_to_node_map[ue_id] = node_id
                                elif ue_cell_id in self.cell_to_node_map:
                                    self.ue_to_node_map[ue_id] = self.cell_to_node_map[ue_cell_id]
                                
                                # Store latest UE metrics
                                self.active_ues[ue_id] = {
                                    "ue_id": ue_id,
                                    "cell_id": ue_cell_id,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "metrics": ue
                                }
                        
                        # Write KPI to CSV file
                        # Write cell-level data first
                        inferred_node_id = self.client_node_map.get(client_id) if client_id in self.client_node_map else node_id
                        self._write_kpi_to_csv(kpi_data, meid, cell_id, inferred_node_id)
                        
                        # Write separate rows for each UE with their node_id
                        for ue in ues:
                            ue_id = ue.get("ue_id") or ue.get("ueId") or ue.get("id")
                            if ue_id:
                                ue_node_id = ue.get("node_id") or ue.get("nodeId")
                                if ue_node_id is None:
                                    # Fallback to mapping
                                    ue_node_id = self.ue_to_node_map.get(str(ue_id))
                                if ue_node_id is not None:
                                    # Create a UE-only KPI data structure for CSV writing
                                    ue_kpi_data = {
                                        "format": kpi_data.get("format", "F1"),
                                        "measurements": ue.get("measurements", []),
                                        "ues": []  # Don't include nested UEs
                                    }
                                    ue_cell_id = ue.get("cell_id") or ue.get("cellId") or cell_id
                                    # Write UE row with UE node_id (is_ue_data=True to prefix metrics with UE_)
                                    self._write_kpi_to_csv(ue_kpi_data, meid, f"UE_{ue_id}", int(ue_node_id), is_ue_data=True)
                        
                        # Extract node_id before conversion (needed for adapter)
                        node_id_for_adapter = kpi_data.get("node_id") or kpi_data.get("nodeId") or message.get("node_id")
                        if node_id_for_adapter is None:
                            # Try to infer from cell_id or use mapping
                            if cell_id in self.cell_to_node_map:
                                node_id_for_adapter = self.cell_to_node_map[cell_id]
                            elif client_id in self.client_node_map:
                                node_id_for_adapter = self.client_node_map[client_id]
                            else:
                                node_id_for_adapter = 2  # Default to gNB
                        
                        # Add node_id to message so adapter can extract it
                        if node_id_for_adapter is not None:
                            if "kpi" not in message:
                                message["kpi"] = {}
                            message["kpi"]["node_id"] = int(node_id_for_adapter)
                        
                        # Convert KPI format
                        internal_kpi = self.kpi_adapter.convert_xapp_kpi_to_internal(message, meid)
                        
                        # Publish to membus (KPI Stream → Minirocket & Observer)
                        if self.bus:
                            await self.bus.pub("kpi.raw", make_msg(
                                "kpi.raw", "KPI", "kpi.raw.v1",
                                {
                                    "kpi": internal_kpi,
                                    "client_id": client_id,
                                    "meid": meid
                                }
                            ))
                            logger.debug(f"Published KPI to membus: kpi.raw")
                    else:
                        logger.warning(f"Unknown message type: {msg_type}")
                        
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    
        except Exception as e:
            logger.error(f"Connection error with {client_id}: {e}")
        finally:
            if client_id in self.clients:
                del self.clients[client_id]
            if client_id in self.meid_map:
                del self.meid_map[client_id]
            writer.close()
            await writer.wait_closed()
            logger.info(f"xApp connection closed: {client_id}")
            
    async def send_command(self, client_id: str, command: Dict[str, Any]) -> bool:
        """Send a control command to a client."""
        if not self.commands_enabled:
            logger.info(f"[COMMANDS DISABLED] Would send command to {client_id}: {command.get('cmd', {}).get('cmd', 'unknown')}")
            return True  # Return True to indicate "success" (command was processed, just not sent)
        
        if client_id not in self.clients:
            logger.warning(f"Client {client_id} not connected")
            return False
            
        try:
            writer = self.clients[client_id]
            response_json = json.dumps(command)
            response_bytes = response_json.encode("utf-8")
            length_header = struct.pack("!I", len(response_bytes))
            
            writer.write(length_header + response_bytes)
            # Add timeout to prevent hanging
            await asyncio.wait_for(writer.drain(), timeout=2.0)
            logger.info(f"Sent command to {client_id}: {command.get('cmd', {}).get('cmd', 'unknown')}")
            return True
        except asyncio.TimeoutError:
            logger.error(f"Timeout sending command to {client_id} - connection may be broken")
            # Remove broken client
            if client_id in self.clients:
                try:
                    self.clients[client_id].close()
                except:
                    pass
                del self.clients[client_id]
            return False
        except Exception as e:
            logger.error(f"Failed to send command to {client_id}: {e}")
            # Remove broken client
            if client_id in self.clients:
                try:
                    self.clients[client_id].close()
                except:
                    pass
                del self.clients[client_id]
            return False


class XAppActorAgent:
    """Actor agent that sends commands to xApp over TCP."""
    
    def __init__(self, bus: MemBus, actor: Actor, tcp_server: XAppTCPServer, 
                 converter: PlaybookToCommandConverter, default_meid: str = "gnb:131-133-31000000", 
                 node_id: int = 0):
        self.bus = bus
        self.actor = actor
        self.tcp_server = tcp_server
        self.converter = converter
        self.default_meid = default_meid
        self.node_id = node_id
        
    async def run(self):
        """Subscribe to scored playbooks and send commands."""
        q = await self.bus.sub("predictor.scored")
        
        while True:
            msg = await q.get()
            scored = msg.payload.get("scored", [])
            if not scored:
                logger.warning("No scored playbooks received")
                continue
            
            # Get best playbook
            best_pb, best_q = max(scored, key=lambda t: t[1])
            logger.info(f"Best playbook selected with Q={best_q:.3f}")
            
            # Save playbook
            payload = self.actor.make_payload(best_pb)
            self.actor.save_payload(payload)
            logger.info(f"Saved playbook: {payload['playbook_id']}")
            
            # Convert to commands with mappings
            meid = self.default_meid
            cell_to_node_map = getattr(self.tcp_server, 'cell_to_node_map', {})
            ue_to_node_map = getattr(self.tcp_server, 'ue_to_node_map', {})
            ue_to_cell_map = getattr(self.tcp_server, 'ue_to_cell_map', {})
            default_node_id = self.node_id
            
            # Log available mappings for debugging
            logger.info(f"Available mappings - cells: {list(cell_to_node_map.keys())}, UEs: {list(ue_to_node_map.keys())[:5]}..., default_node_id: {default_node_id}")
            
            commands = self.converter.playbook_to_commands(
                best_pb, 
                meid, 
                default_node_id,
                cell_to_node_map=cell_to_node_map,
                ue_to_node_map=ue_to_node_map,
                ue_to_cell_map=ue_to_cell_map
            )
            
            logger.info(f"Converted playbook to {len(commands)} command(s)")
            
            if not self.tcp_server.commands_enabled:
                logger.info(f"[COMMANDS DISABLED] Would send {len(commands)} command(s) but commands are disabled")
                # Still publish the event for logging/tracking
                await self.bus.pub("actor.apply", make_msg(
                    "actor.apply", "APPLY", "actor.apply.v1",
                    {"playbook": best_pb, "q": best_q, "commands": commands, "commands_enabled": False}
                ))
                return
            
            if commands:
                # Send to all connected clients
                connected_clients = list(self.tcp_server.clients.keys())
                if not connected_clients:
                    logger.warning("No clients connected, cannot send commands")
                else:
                    for client_id in connected_clients:
                        # Use meid from map if available
                        meid = self.tcp_server.meid_map.get(client_id, self.default_meid)
                        # Update commands with correct meid
                        for i, cmd in enumerate(commands):
                            cmd["meid"] = meid
                            logger.info(f"Sending command {i+1}/{len(commands)}: {cmd.get('cmd', {}).get('cmd', 'unknown')} to node {cmd.get('cmd', {}).get('node', 'unknown')}")
                            success = await self.tcp_server.send_command(client_id, cmd)
                            if not success:
                                logger.error(f"Failed to send command to {client_id}")
                            # Add 2 second cooldown between commands (except for the last one)
                            if i < len(commands) - 1:
                                logger.info(f"Waiting 2 seconds before next command...")
                                await asyncio.sleep(2.0)
            else:
                logger.warning("No commands generated from playbook")
            
            # Publish actor.apply event
            await self.bus.pub("actor.apply", make_msg(
                "actor.apply", "APPLY", "actor.apply.v1",
                {"playbook": best_pb, "q": best_q, "commands": commands}
            ))


class ObserverBridge:
    """Bridge between membus and RLObserver (file-based)."""
    
    def __init__(self, bus: MemBus, observer: RLObserver, temp_file: Path):
        self.bus = bus
        self.observer = observer
        self.temp_file = temp_file
        self.last_playbook = None
        # Feature accumulation: merge KPIs from fragments using node_id as primary key
        # Key format: "node_{node_id}" for gNB-level, "cell_{cell_id}" for cell-specific, "ue_{ue_id}" for UE-specific
        self.accumulated_kpis: Dict[str, Dict[str, Any]] = {}  # accumulation_key -> accumulated KPI
        self.accumulation_timestamps: Dict[str, float] = {}  # accumulation_key -> last update time
        self.accumulation_timeout = 3.0  # seconds - process after this timeout even if incomplete (increased to allow fragment merging)
        self.last_processed_time: Dict[str, float] = {}  # Track when we last processed to avoid duplicate processing
        
    async def _listen_actor_apply(self, q):
        """Listen for applied playbooks to update last_playbook."""
        while True:
            msg = await q.get()
            playbook = msg.payload.get("playbook")
            if playbook:
                self.last_playbook = playbook
                logger.debug("Updated last_playbook from actor.apply")
        
    async def run(self):
        """Subscribe to KPIs and update observer, publish state windows."""
        q = await self.bus.sub("kpi.raw")
        q_intent = await self.bus.sub("intent.rl")  # RL intent format
        q_actor_apply = await self.bus.sub("actor.apply")  # Listen for applied playbooks
        
        # Listen for intent updates and actor apply events
        asyncio.create_task(self._listen_intent(q_intent))
        asyncio.create_task(self._listen_actor_apply(q_actor_apply))
        
        while True:
            msg = await q.get()
            kpi = msg.payload.get("kpi")
            if kpi:
                # Accumulate features from fragments using node_id as primary key
                cell_metrics = kpi.get('CellMetrics', {})
                raw_cell_id = cell_metrics.get('cell_id', 'unknown')
                current_time = datetime.now(timezone.utc).timestamp()
                
                # Extract node_id from KPI (prefer Header, then CellMetrics, then default to 2 for gNB)
                header = kpi.get('Header', {})
                node_id = cell_metrics.get('node_id') or header.get('node_id') or kpi.get('node_id')
                if node_id is None:
                    # Try to infer from cell_id or default to 2 (gNB)
                    if raw_cell_id.startswith('CELL_') or raw_cell_id == 'unknown':
                        node_id = 2  # gNB node
                    elif raw_cell_id.startswith('UE_'):
                        node_id = 3  # UE node (default)
                    else:
                        node_id = 2  # Default to gNB
                
                node_id = int(node_id)
                
                # Determine accumulation key: Only process gNB-level (node_id=2) for cell-level metrics
                # UE-level KPIs (node_id=3,4) are aggregated into gNB accumulation for global context
                if node_id == 2:  # gNB node - this is what we process for cell-level metrics
                    accumulation_key = "gNB_cell_level"  # Single accumulation for all gNB fragments
                    # Use the best cell_id we've seen (prefer known cell over unknown)
                    if raw_cell_id.startswith('CELL_'):
                        # Update cell_id in accumulated data if we see a known cell
                        if accumulation_key in self.accumulated_kpis:
                            acc_cell_metrics = self.accumulated_kpis[accumulation_key]['CellMetrics']
                            if acc_cell_metrics.get('cell_id') == 'unknown' or not acc_cell_metrics.get('cell_id'):
                                acc_cell_metrics['cell_id'] = raw_cell_id
                                logger.debug(f"Updated cell_id from 'unknown' to {raw_cell_id} for gNB accumulation")
                else:
                    # UE-level KPIs: aggregate their metrics into gNB accumulation for global context
                    # Don't create separate accumulations for UE nodes - merge into gNB
                    accumulation_key = "gNB_cell_level"  # Aggregate UE metrics into gNB view
                    logger.debug(f"Aggregating UE node_id={node_id} metrics into gNB accumulation for global context")
                
                # Initialize or update accumulated KPI for gNB-level metrics
                if accumulation_key not in self.accumulated_kpis:
                    # Start new accumulation (only for gNB node_id=2)
                    if node_id == 2:
                        self.accumulated_kpis[accumulation_key] = {
                            "timestamp": kpi.get("timestamp", datetime.now(timezone.utc).isoformat()),
                            "Header": kpi.get("Header", {}),
                            "CellMetrics": cell_metrics.copy(),
                            "UEMetrics": kpi.get("UEMetrics", [])
                        }
                        # Ensure cell_id is set (use best available)
                        if not self.accumulated_kpis[accumulation_key]["CellMetrics"].get('cell_id') or \
                           self.accumulated_kpis[accumulation_key]["CellMetrics"].get('cell_id') == 'unknown':
                            if raw_cell_id.startswith('CELL_'):
                                self.accumulated_kpis[accumulation_key]["CellMetrics"]['cell_id'] = raw_cell_id
                            else:
                                self.accumulated_kpis[accumulation_key]["CellMetrics"]['cell_id'] = raw_cell_id
                        self.accumulation_timestamps[accumulation_key] = current_time
                    else:
                        # UE node - skip if gNB accumulation doesn't exist yet
                        continue
                else:
                    # Merge new features into accumulated KPI
                    acc_cell_metrics = self.accumulated_kpis[accumulation_key]["CellMetrics"]
                    
                    if node_id == 2:
                        # gNB-level: merge cell metrics
                        for key, value in cell_metrics.items():
                            if value is not None:  # Only update with non-None values
                                # Update cell_id if we see a known cell (prefer known over unknown)
                                if key == 'cell_id':
                                    if value.startswith('CELL_') and (acc_cell_metrics.get('cell_id') == 'unknown' or not acc_cell_metrics.get('cell_id')):
                                        acc_cell_metrics[key] = value
                                        logger.debug(f"Updated cell_id to {value} for {accumulation_key}")
                                    elif not acc_cell_metrics.get('cell_id') or acc_cell_metrics.get('cell_id') == 'unknown':
                                        acc_cell_metrics[key] = value
                                else:
                                    acc_cell_metrics[key] = value
                        
                        # Merge UE metrics from this KPI
                        ue_metrics = kpi.get("UEMetrics", [])
                        if ue_metrics:
                            existing_ue_metrics = self.accumulated_kpis[accumulation_key].get("UEMetrics", [])
                            # Add new UE metrics (avoid duplicates)
                            for new_ue in ue_metrics:
                                new_ue_id = new_ue.get("ue_id") or new_ue.get("ueId")
                                if new_ue_id:
                                    # Check if this UE already exists
                                    existing = next((ue for ue in existing_ue_metrics if (ue.get("ue_id") or ue.get("ueId")) == new_ue_id), None)
                                    if existing:
                                        # Merge metrics
                                        for key, value in new_ue.items():
                                            if value is not None:
                                                existing[key] = value
                                    else:
                                        existing_ue_metrics.append(new_ue)
                            self.accumulated_kpis[accumulation_key]["UEMetrics"] = existing_ue_metrics
                    else:
                        # UE-level: aggregate UE metrics into gNB accumulation for global context
                        # Extract UE metrics and aggregate them (e.g., average delay, sum throughput)
                        ue_metrics = kpi.get("UEMetrics", [])
                        if ue_metrics:
                            existing_ue_metrics = self.accumulated_kpis[accumulation_key].get("UEMetrics", [])
                            for new_ue in ue_metrics:
                                new_ue_id = new_ue.get("ue_id") or new_ue.get("ueId")
                                if new_ue_id:
                                    existing = next((ue for ue in existing_ue_metrics if (ue.get("ue_id") or ue.get("ueId")) == new_ue_id), None)
                                    if existing:
                                        # Merge metrics
                                        for key, value in new_ue.items():
                                            if value is not None:
                                                existing[key] = value
                                    else:
                                        existing_ue_metrics.append(new_ue)
                            self.accumulated_kpis[accumulation_key]["UEMetrics"] = existing_ue_metrics
                        
                        # Also aggregate UE-level metrics into cell-level metrics (for global context)
                        # UE LEVEL: UE_PDCP_Delay_DL_ms, DRB_EstabSucc_5QI_UEID, TB_TotNbrDlInitial_Qpsk_UEID, 
                        #           TB_TotNbrDlInitial_64Qam_UEID, UE_PRB_Used_DL, UE_Throughput_DL_Mbps
                        if ue_metrics:
                            # Aggregate UE delays into cell delay (use max if available)
                            ue_delays = [float(ue.get("UE_PDCP_Delay_DL_ms", 0)) for ue in ue_metrics if ue.get("UE_PDCP_Delay_DL_ms") is not None]
                            if ue_delays and not acc_cell_metrics.get('UE_PDCP_Delay_DL_ms'):
                                # Use max UE delay as cell delay if cell delay not available
                                acc_cell_metrics['UE_PDCP_Delay_DL_ms'] = max(ue_delays)
                                logger.debug(f"Aggregated max UE delay {max(ue_delays):.2f}ms into cell metrics")
                            
                            # Aggregate UE throughputs (convert Mbps to bps for consistency)
                            ue_throughputs = [float(ue.get("UE_Throughput_DL_Mbps", 0)) for ue in ue_metrics if ue.get("UE_Throughput_DL_Mbps") is not None]
                            if ue_throughputs:
                                total_thr_mbps = sum(ue_throughputs)
                                # Store as UE_Throughput_DL_Mbps_Total or convert to bps
                                acc_cell_metrics['UE_Throughput_DL_Mbps_Total'] = total_thr_mbps
                                logger.debug(f"Aggregated total UE throughput {total_thr_mbps:.2f}Mbps into cell metrics")
                            
                            # Count active UEs
                            if not acc_cell_metrics.get('Mean_Active_UEs_DL'):
                                acc_cell_metrics['Mean_Active_UEs_DL'] = len(ue_metrics)
                
                # Only process gNB-level accumulations (skip UE-only processing)
                if node_id != 2:
                    continue  # Skip processing for UE nodes - they're aggregated into gNB
                
                # Check if we should process this accumulated KPI
                acc_kpi = self.accumulated_kpis[accumulation_key]
                acc_cell_metrics = acc_kpi["CellMetrics"]
                cell_id = acc_cell_metrics.get('cell_id', 'unknown')  # Use accumulated cell_id for logging
                available_features = [k for k in self.observer.features if k in acc_cell_metrics and acc_cell_metrics[k] is not None]
                completeness = len(available_features) / len(self.observer.features) if self.observer.features else 0.0
                time_since_start = current_time - self.accumulation_timestamps[accumulation_key]  # FIX: use accumulation_key, not cell_id
                
                # Check if we have critical features (delay AND cell metrics) before processing
                has_delay = acc_cell_metrics.get('UE_PDCP_Delay_DL_ms') is not None
                has_cell_metrics = any(k in acc_cell_metrics and acc_cell_metrics[k] is not None 
                                      for k in ['PRB_Used_DL', 'Mean_Active_UEs_DL', 'UE_Throughput_DL_Mbps_Total'])
                
                # Process if:
                # 1. We have sufficient completeness, OR
                # 2. We have both delay and cell metrics (even if completeness is low), OR
                # 3. Timeout expired (but only if we haven't processed recently)
                last_processed = self.last_processed_time.get(accumulation_key, 0)
                time_since_last_process = current_time - last_processed
                
                should_process = (
                    completeness >= self.observer.min_feature_completeness or
                    (has_delay and has_cell_metrics and time_since_start >= 0.5) or  # Wait at least 0.5s for fragments to arrive
                    (time_since_start >= self.accumulation_timeout and time_since_last_process >= 1.0)  # Avoid duplicate processing
                )
                
                if should_process:
                    # Update last processed time to avoid duplicate processing
                    self.last_processed_time[accumulation_key] = current_time
                    
                    # Write accumulated KPI to observer's file (atomic write)
                    import os
                    temp_file_tmp = self.temp_file.with_suffix(self.temp_file.suffix + ".tmp")
                    with open(temp_file_tmp, "w") as f:
                        json.dump({"kpi_stream": [acc_kpi]}, f)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_file_tmp, self.temp_file)
                    
                    delay = acc_cell_metrics.get('UE_PDCP_Delay_DL_ms', 'N/A')
                    # Log which features we have
                    feature_list = ', '.join(available_features[:5])  # Show first 5
                    if len(available_features) > 5:
                        feature_list += f" ... (+{len(available_features)-5} more)"
                    logger.info(f"Processing accumulated KPI for {cell_id}: UE_PDCP_Delay_DL_ms={delay}, feature completeness={completeness:.1%}, features={len(available_features)}/{len(self.observer.features)} [{feature_list}]")
                    
                    # Process with observer
                    try:
                        state = self.observer.step(self.last_playbook)
                        if state is not None:
                            # Publish state window
                            try:
                                import numpy as np
                                state_list = state.tolist() if hasattr(state, 'tolist') else state
                            except ImportError:
                                state_list = state if isinstance(state, list) else state.tolist() if hasattr(state, 'tolist') else list(state)
                            
                            # Get state shape info
                            if isinstance(state_list, list) and len(state_list) > 0:
                                if isinstance(state_list[0], list):
                                    shape_info = f"[{len(state_list)}, {len(state_list[0])}]"
                                else:
                                    shape_info = f"[{len(state_list)}]"
                            else:
                                shape_info = "unknown"
                            
                            await self.bus.pub("kpi.window", make_msg(
                                "kpi.window", "STATE_WINDOW", "kpi.window.v1",
                                {
                                    "state": state_list,
                                    "reward": 0.0,  # Observer computes reward internally
                                }
                            ))
                            logger.info(f"Published state window to membus (shape: {shape_info})")
                        else:
                            logger.debug(f"Observer returned None state - window may not be full yet (buf size: {len(self.observer.buf)})")
                    except Exception as e:
                        logger.error(f"Error in observer.step(): {e}")
                        import traceback
                        traceback.print_exc()
                    
                    # Clear accumulated KPI after processing
                    if accumulation_key in self.accumulated_kpis:
                        del self.accumulated_kpis[accumulation_key]
                    if accumulation_key in self.accumulation_timestamps:
                        del self.accumulation_timestamps[accumulation_key]
                else:
                    # Still accumulating - log progress
                    logger.debug(f"Accumulating KPI for {cell_id}: {len(available_features)}/{len(self.observer.features)} features, {completeness:.1%} complete, {time_since_start:.2f}s elapsed")
                
                # Clean up old accumulated KPIs (timeout expired) - only for gNB
                if node_id == 2:  # Only check timeout for gNB-level accumulations
                    expired_keys = [
                        key for key, ts in self.accumulation_timestamps.items()
                        if current_time - ts >= self.accumulation_timeout and key in self.accumulated_kpis
                    ]
                    for key in expired_keys:
                        if key in self.accumulated_kpis:
                            logger.warning(f"Timeout: Processing incomplete KPI for {key} after {self.accumulation_timeout}s")
                            # Process even if incomplete
                            acc_kpi = self.accumulated_kpis[key]
                            import os
                            temp_file_tmp = self.temp_file.with_suffix(self.temp_file.suffix + ".tmp")
                            with open(temp_file_tmp, "w") as f:
                                json.dump({"kpi_stream": [acc_kpi]}, f)
                                f.flush()
                                os.fsync(f.fileno())
                            os.replace(temp_file_tmp, self.temp_file)
                            try:
                                state = self.observer.step(self.last_playbook)
                                if state is not None:
                                    import numpy as np
                                    state_list = state.tolist() if hasattr(state, 'tolist') else state
                                    await self.bus.pub("kpi.window", make_msg(
                                        "kpi.window", "STATE_WINDOW", "kpi.window.v1",
                                        {"state": state_list, "reward": 0.0}
                                    ))
                            except Exception as e:
                                logger.error(f"Error processing expired KPI: {e}")
                            del self.accumulated_kpis[key]
                            if key in self.accumulation_timestamps:
                                del self.accumulation_timestamps[key]
    
    async def _listen_intent(self, q):
        """Listen for RL intent updates."""
        while True:
            msg = await q.get()
            rl_intent = msg.payload
            # Update observer's intent
            self.observer.intent = Intent(
                type=rl_intent.get("type", "REDUCE_LATENCY"),
                metric=rl_intent.get("metric", "delay_p95_ms"),
                target=float(rl_intent.get("target", 40.0)),
                direction=rl_intent.get("direction", "lower_better"),
                action_cost=float(rl_intent.get("action_cost", 0.01)),
                reward_clip=float(rl_intent.get("reward_clip", 2.0)),
            )
            logger.info(f"Observer intent updated: {self.observer.intent}")


async def run_ai_loop_with_membus(
    tcp_server: XAppTCPServer,
    cells: List[str],
    slices: List[str],
    target_metric: str = "delay_p95_ms",
    target_value: float = 40.0,
    steps: int = 1000,
    offline_model: Optional[str] = None,
    minirocket_model: Optional[str] = None,
    minirocket_gnb_model: Optional[str] = None,
    minirocket_ue_model: Optional[str] = None,
    use_llm: bool = False,
):
    """Run the AI loop using membus architecture matching system design."""
    # Create membus
    bus = MemBus()
    tcp_server.bus = bus
    
    # Initialize components
    action_space = ActionSpace(cells=cells, slices=slices)
    knowledge_base = CacheLibrary(max_per_key=20)  # Knowledge Base shared by Reasoner & Proposer
    
    # Create predictor and observer
    tmp_intent = Intent(type="REDUCE_LATENCY", metric=target_metric, target=target_value)
    
    # Create temp file for observer
    temp_kpi_file = Path(tempfile.gettempdir()) / "xapp_kpi_temp.json"
    temp_kpi_file.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_kpi_file, "w") as f:
        json.dump({"kpi_stream": []}, f)
    
    # Create observer with feature completeness threshold (lowered to work with sparse KPIs)
    dummy_observer = RLObserver(
        predictor=None, 
        intent=tmp_intent, 
        kpi_file=str(temp_kpi_file), 
        window=12,
        min_feature_completeness=0.1  # Require at least 10% of features (1 out of 10) to be present
    )
    predictor = SlateDQNPredictor(
        action_space, 
        feat_dim=len(dummy_observer.features), 
        seed=0
    )
    
    # Load model: Try online checkpoint first, then offline model, then start from scratch
    online_checkpoint = "models/qnet_online.pt"
    model_loaded = False
    
    # Try to load online checkpoint (resume from previous run)
    if predictor.load_checkpoint(online_checkpoint, load_replay_buffer=False):
        logger.info(f"✓ Resumed training from online checkpoint: {online_checkpoint} (step={predictor.steps})")
        model_loaded = True
    # Fallback to offline model if provided
    elif offline_model:
        try:
            predictor.load_offline(offline_model)
            logger.info(f"✓ Loaded pre-trained offline model from {offline_model}")
            model_loaded = True
        except Exception as e:
            logger.warning(f"Could not load pre-trained model ({e}). Starting from scratch.")
    else:
        logger.info("Starting from scratch (no checkpoint or offline model provided)")
    
    observer = RLObserver(
        predictor=predictor, 
        intent=tmp_intent, 
        kpi_file=str(temp_kpi_file), 
        window=12,
        min_feature_completeness=0.1  # Require at least 10% of features (1 out of 10) to be present
    )
    
    # Create actor
    actor = Actor("configs")
    converter = PlaybookToCommandConverter()
    actor_agent = XAppActorAgent(bus, actor, tcp_server, converter)
    
    # Create agents according to system design
    # 1. Minirocket Agents (KPI Stream → Minirocket → deviation.detected)
    # Support both gNB-level and UE-level deviation detection
    minirocket_agents = []
    
    # gNB-level agent (monitors cell-level metrics)
    if minirocket_gnb_model or minirocket_model:
        # Determine gNB metric name from target_metric or use default
        gnb_metric = target_metric
        # Map common metric names to actual CSV column names
        if gnb_metric == "delay_p95_ms":
            gnb_metric = "DRB_PdcpSduDelayDl"  # Use actual column name from CSV
        elif gnb_metric == "thr_dl_bps":
            gnb_metric = "DRB_MeanActiveUeDl"  # Or appropriate gNB metric
        
        gnb_model_path = minirocket_gnb_model or minirocket_model or "models/minirocket_xapp_gnb.joblib"
        minirocket_gnb_agent = MinirocketAgent(
            bus,
            model_path=gnb_model_path,
            metric=gnb_metric,
            window_size=128
        )
        minirocket_agents.append(("gnb", minirocket_gnb_agent))
        logger.info(f"Created gNB-level MinirocketAgent: metric={gnb_metric}, model={gnb_model_path}")
    
    # UE-level agent (monitors UE-level metrics)
    if minirocket_ue_model or minirocket_model:
        # Determine UE metric name from target_metric or use default
        ue_metric = target_metric
        # Map common metric names to actual CSV column names
        if ue_metric == "delay_p95_ms":
            ue_metric = "UE_DRB_PdcpSduDelayDl_UEID"  # Use actual column name from CSV
        elif ue_metric == "thr_dl_bps":
            ue_metric = "UE_DRB_UEThpDl_UEID"  # Or appropriate UE metric
        
        ue_model_path = minirocket_ue_model or minirocket_model or "models/minirocket_xapp_ue.joblib"
        minirocket_ue_agent = MinirocketAgent(
            bus,
            model_path=ue_model_path,
            metric=ue_metric,
            window_size=128
        )
        minirocket_agents.append(("ue", minirocket_ue_agent))
        logger.info(f"Created UE-level MinirocketAgent: metric={ue_metric}, model={ue_model_path}")
    
    # Fallback: if no specific models provided, use single agent with default model
    if not minirocket_agents:
        minirocket_agent = MinirocketAgent(
            bus,
            model_path=minirocket_model or "models/minirocket.joblib",
            metric=target_metric
        )
        minirocket_agents = [("default", minirocket_agent)]
        logger.info(f"Created default MinirocketAgent: metric={target_metric}, model={minirocket_model or 'models/minirocket.joblib'}")
    
    # 2. Enhanced Reasoner Agent (deviation.detected + SLO Intents → intent.current)
    reasoner_agent = EnhancedReasonerAgent(
        bus,
        knowledge_base=knowledge_base,
        use_llm=use_llm
    )
    
    # Publish initial SLO intent so system can start working even without deviations
    async def publish_initial_slo():
        await asyncio.sleep(2.0)  # Wait a bit for system to initialize
        initial_slo = {
            "slo_id": "initial_slo",
            "metric": target_metric,
            "target": target_value,
            "direction": "lower_better" if "delay" in target_metric or "latency" in target_metric else "higher_better"
        }
        await bus.pub("slo.intent", make_msg(
            "slo.intent", "SLO_INTENT", "slo.intent.v1", initial_slo
        ))
        logger.info(f"Published initial SLO intent: {target_metric} target={target_value}")
        
        # Also create an initial intent directly (for systems that start without deviations)
        # This allows the proposer to start generating playbooks immediately
        initial_intent = {
            "intent_id": "initial_intent",
            "metric": target_metric,
            "target": target_value,
            "direction": initial_slo["direction"],
            "type": "REDUCE_LATENCY" if "delay" in target_metric or "latency" in target_metric else "INCREASE_THROUGHPUT"
        }
        await bus.pub("intent.current", make_msg(
            "intent.current", "INTENT", "intent.v1", initial_intent
        ))
        await bus.pub("intent.rl", make_msg(
            "intent.rl", "RL_INTENT", "rl_intent.v1", {
                "type": initial_intent["type"],
                "metric": initial_intent["metric"],
                "target": initial_intent["target"],
                "direction": initial_intent["direction"],
                "action_cost": 0.01,
                "reward_clip": 2.0,
            }
        ))
        logger.info(f"Published initial intent: {initial_intent['type']} for {target_metric} (target={target_value})")
    
    # 3. Proposer Agent (intent.current + Knowledge Base → proposer.candidates)
    proposer_agent = ProposerAgent(
        bus,
        action_space,
        knowledge_base=knowledge_base
    )
    
    # 4. Predictor Agent (kpi.window + proposer.candidates → predictor.scored)
    predictor_agent = PredictorAgent(bus, predictor)
    
    # 5. Observer Bridge (kpi.raw → observer → kpi.window)
    observer_bridge = ObserverBridge(bus, observer, temp_kpi_file)
    
    # Setup checkpoint saving
    online_checkpoint = "models/qnet_online.pt"
    
    async def periodic_checkpoint_saver():
        """Periodically save checkpoint every 50 steps."""
        while True:
            await asyncio.sleep(30)  # Check every 30 seconds
            if predictor.steps > 0 and predictor.steps % 50 == 0:
                try:
                    checkpoint_path = predictor.save_checkpoint(online_checkpoint, save_replay_buffer=False)
                    logger.info(f"💾 Saved periodic checkpoint to {checkpoint_path} (step={predictor.steps}, replay_size={len(predictor.replay)})")
                except Exception as e:
                    logger.warning(f"Failed to save periodic checkpoint: {e}")
    
    def save_on_exit(signum=None, frame=None):
        """Save checkpoint before exiting."""
        try:
            checkpoint_path = predictor.save_checkpoint(online_checkpoint, save_replay_buffer=False)
            logger.info(f"💾 Saved final checkpoint to {checkpoint_path} (step={predictor.steps})")
        except Exception as e:
            logger.error(f"Failed to save checkpoint on exit: {e}")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, save_on_exit)
    signal.signal(signal.SIGTERM, save_on_exit)
    
    # Run all agents
    logger.info("Starting AI loop with membus architecture (matching system design)...")
    
    # Create tasks for all minirocket agents
    minirocket_tasks = [
        asyncio.create_task(agent.run(), name=f"minirocket_agent_{name}")
        for name, agent in minirocket_agents
    ]
    
    tasks = minirocket_tasks + [
        asyncio.create_task(reasoner_agent.run(), name="reasoner_agent"),
        asyncio.create_task(proposer_agent.run(), name="proposer_agent"),
        asyncio.create_task(predictor_agent.run(), name="predictor_agent"),
        asyncio.create_task(observer_bridge.run(), name="observer_bridge"),
        asyncio.create_task(actor_agent.run(), name="actor_agent"),
        asyncio.create_task(publish_initial_slo(), name="publish_initial_slo"),
        asyncio.create_task(periodic_checkpoint_saver(), name="checkpoint_saver"),
    ]
    
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        # Save final checkpoint before exiting
        try:
            checkpoint_path = predictor.save_checkpoint(online_checkpoint, save_replay_buffer=False)
            logger.info(f"💾 Saved final checkpoint to {checkpoint_path} (step={predictor.steps})")
        except Exception as e:
            logger.error(f"Failed to save final checkpoint: {e}")
        
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    parser = argparse.ArgumentParser(description="AI System Demo with xApp TCP Integration (Membus)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="TCP server host")
    parser.add_argument("--port", type=int, default=6000, help="TCP server port")
    parser.add_argument("--steps", type=int, default=1000, help="Max steps")
    parser.add_argument("--target-metric", type=str, default="delay_p95_ms", help="Target metric")
    parser.add_argument("--target-value", type=float, default=40.0, help="Target value")
    parser.add_argument("--cells", type=str, nargs="+", default=["CELL_001"], help="Cell IDs")
    parser.add_argument("--slices", type=str, nargs="+", default=["SLICE_A"], help="Slice IDs")
    parser.add_argument("--offline-model", type=str, default=None, help="Path to pre-trained Q-network model")
    parser.add_argument("--minirocket-model", type=str, default=None, 
                       help="Path to MiniRocket model (legacy, for single model). Use --minirocket-gnb-model and --minirocket-ue-model for separate gNB/UE models.")
    parser.add_argument("--minirocket-gnb-model", type=str, default=None,
                       help="Path to MiniRocket model for gNB-level metrics (e.g., models/minirocket_xapp_gnb.joblib)")
    parser.add_argument("--minirocket-ue-model", type=str, default=None,
                       help="Path to MiniRocket model for UE-level metrics (e.g., models/minirocket_xapp_ue.joblib)")
    parser.add_argument("--use-llm", action="store_true", help="Use LLM for intent reasoning (default: fallback)")
    parser.add_argument("--commands-enabled", action="store_true", default=True,
                       help="Enable sending control commands to xApp (default: enabled)")
    parser.add_argument("--commands-disabled", action="store_false", dest="commands_enabled",
                       help="Disable sending control commands to xApp (useful for KPI collection only)")
    
    args = parser.parse_args()
    
    # Create TCP server
    tcp_server = XAppTCPServer(host=args.host, port=args.port, commands_enabled=args.commands_enabled)
    
    if not args.commands_enabled:
        logger.info("=" * 60)
        logger.info("COMMANDS DISABLED - AI will process KPIs but NOT send control commands")
        logger.info("This is useful for collecting KPIs from simulation without interference")
        logger.info("=" * 60)
    await tcp_server.start()
    
    try:
        await run_ai_loop_with_membus(
            tcp_server,
            cells=args.cells,
            slices=args.slices,
            target_metric=args.target_metric,
            target_value=args.target_value,
            steps=args.steps,
            offline_model=args.offline_model,
            minirocket_model=args.minirocket_model,
            minirocket_gnb_model=args.minirocket_gnb_model,
            minirocket_ue_model=args.minirocket_ue_model,
            use_llm=args.use_llm,
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await tcp_server.stop()


if __name__ == "__main__":
    asyncio.run(main())
