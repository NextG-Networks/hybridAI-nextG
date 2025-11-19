#!/usr/bin/env python3
"""
Demo script for AI system integrated with xApp over TCP.

This script:
1. Starts TCP server on port 5000 to receive KPIs from xApp
2. Converts xApp KPI format to internal format
3. Runs the AI contextual bandit loop
4. Sends control commands back to xApp over TCP
"""

from __future__ import annotations
import argparse
import asyncio
import json
import struct
import sys
import time
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
from ain.brain.llm_reasoner import normalize_deviation, to_proposer_meta, to_rl_intent
from ain.brain.openai_client import reason_from_deviation, OpenAIError, fallback_intent_for_deviation
from ain.common.types import ControlAction, Playbook

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class XAppKPIAdapter:
    """Converts xApp KPI format to internal format expected by RLObserver."""
    
    @staticmethod
    def convert_xapp_kpi_to_internal(xapp_kpi: Dict[str, Any], meid: str) -> Dict[str, Any]:
        """
        Convert xApp KPI format to internal format.
        
        xApp format: {"type":"kpi","meid":"...","kpi":{...}}
        Internal format: {"timestamp":"...","Header":{...},"CellMetrics":{...},"UEMetrics":[...]}
        """
        kpi_data = xapp_kpi.get("kpi", {})
        
        # Extract cell metrics from xApp format
        # Based on gnb_kpis.csv: timestamp,meid,cell_id,format,UE_PDCP_Delay_DL_ms,DL_TB_QPSK_Count,DL_TB_16QAM_Count,PRB_Used_DL,Mean_Active_UEs_DL,DL_TB_64QAM_Count
        # Based on ue_kpis.csv: timestamp,meid,cell_id,ue_id,UE_PDCP_Delay_DL_ms,TB_TotNbrDlInitial_Qpsk_UEID,...
        
        cell_metrics = {}
        ue_metrics = []
        
        # Try to extract from measurements array (if xApp sends structured data)
        measurements = kpi_data.get("measurements", [])
        ues = kpi_data.get("ues", [])
        
        # If measurements are provided, extract them
        if measurements:
            for m in measurements:
                name = m.get("name", "")
                value = m.get("value", 0)
                
                # Map xApp measurement names to internal format
                if "delay" in name.lower() or "latency" in name.lower():
                    cell_metrics["delay_p95_ms"] = float(value)
                elif "throughput" in name.lower() or "thr" in name.lower():
                    if "dl" in name.lower():
                        cell_metrics["thr_dl_bps"] = float(value) * 1e6  # Convert Mbps to bps
                    elif "ul" in name.lower():
                        cell_metrics["thr_ul_bps"] = float(value) * 1e6
                elif "bler" in name.lower():
                    if "dl" in name.lower():
                        cell_metrics["bler_dl"] = float(value) / 100.0  # Convert % to ratio
                    elif "ul" in name.lower():
                        cell_metrics["bler_ul"] = float(value) / 100.0
                elif "cqi" in name.lower():
                    cell_metrics["cqi_avg"] = float(value)
                elif "mcs" in name.lower():
                    if "dl" in name.lower():
                        cell_metrics["mcs_dl_avg"] = int(value)
                    elif "ul" in name.lower():
                        cell_metrics["mcs_ul_avg"] = int(value)
                elif "prb" in name.lower() and "used" in name.lower():
                    cell_metrics["prb_used_dl"] = float(value)
                elif "active" in name.lower() and "ue" in name.lower():
                    cell_metrics["active_ue_count"] = int(value)
        
        # Extract from raw fields if available (direct field access)
        if "UE_PDCP_Delay_DL_ms" in kpi_data:
            cell_metrics["delay_p95_ms"] = float(kpi_data["UE_PDCP_Delay_DL_ms"])
        if "PRB_Used_DL" in kpi_data:
            cell_metrics["prb_used_dl"] = float(kpi_data["PRB_Used_DL"])
        if "Mean_Active_UEs_DL" in kpi_data:
            cell_metrics["active_ue_count"] = int(kpi_data["Mean_Active_UEs_DL"])
        if "DL_TB_64QAM_Count" in kpi_data:
            # Can use this for MCS estimation if needed
            pass
        
        # Set defaults for missing values
        cell_metrics.setdefault("cell_id", kpi_data.get("cellObjectID", "CELL_001"))
        cell_metrics.setdefault("prb_total", 100.0)
        cell_metrics.setdefault("thr_dl_bps", 0.0)
        cell_metrics.setdefault("thr_ul_bps", 0.0)
        cell_metrics.setdefault("bler_dl", 0.0)
        cell_metrics.setdefault("bler_ul", 0.0)
        cell_metrics.setdefault("cqi_avg", 10.0)
        cell_metrics.setdefault("mcs_dl_avg", 15)
        cell_metrics.setdefault("mcs_ul_avg", 15)
        cell_metrics.setdefault("delay_p95_ms", 50.0)
        cell_metrics.setdefault("active_ue_count", 1)
        cell_metrics.setdefault("prb_used_dl", 50.0)
        
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
                "sequence_number": int(time.time() * 1000) % 1000000,
            },
            "CellMetrics": cell_metrics,
            "UEMetrics": ue_metrics,
        }
        
        return internal_kpi


class PlaybookToCommandConverter:
    """Converts playbook actions to xApp control commands."""
    
    @staticmethod
    def playbook_to_commands(playbook: Playbook, meid: str, node_id: int = 0) -> List[Dict[str, Any]]:
        """
        Convert playbook actions to xApp control commands.
        
        Maps:
        - MCS_CAP -> set-mcs
        - PRB_WEIGHT -> set-bandwidth (approximation)
        - SCHEDULER_POLICY -> (not directly supported, skip or log)
        - SLICE_QOS -> (not directly supported, skip or log)
        - REPORTING -> (no-op, skip)
        """
        commands = []
        
        for action in playbook.actions:
            cmd = None
            
            if action.type == "MCS_CAP":
                # Map MCS_CAP to set-mcs
                mcs_value = action.params.get("dl_mcs_max", 18)
                cmd = {
                    "type": "control",
                    "meid": meid,
                    "cmd": {
                        "cmd": "set-mcs",
                        "node": node_id,
                        "mcs": int(mcs_value)
                    }
                }
                
            elif action.type == "PRB_WEIGHT":
                # Map PRB_WEIGHT to set-bandwidth (approximate)
                # PRB weight affects allocation, bandwidth is closest equivalent
                weight = action.params.get("weight", 1.0)
                # Convert weight to bandwidth (rough approximation: 1.0 = 100 RBs)
                bandwidth = int(100 * weight)
                cmd = {
                    "type": "control",
                    "meid": meid,
                    "cmd": {
                        "cmd": "set-bandwidth",
                        "node": node_id,
                        "bandwidth": bandwidth
                    }
                }
                
            elif action.type == "SCHEDULER_POLICY":
                # Not directly supported, log warning
                logger.warning(f"SCHEDULER_POLICY action not directly supported: {action.params}")
                continue
                
            elif action.type == "SLICE_QOS":
                # Not directly supported, log warning
                logger.warning(f"SLICE_QOS action not directly supported: {action.params}")
                continue
                
            elif action.type == "REPORTING":
                # No-op, skip
                continue
            
            if cmd:
                commands.append(cmd)
        
        return commands


class XAppTCPServer:
    """TCP server for xApp communication."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 5000):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.clients: Dict[str, asyncio.StreamWriter] = {}
        self.kpi_queue: asyncio.Queue = asyncio.Queue()
        self.meid_map: Dict[str, str] = {}  # Map client_id to meid
        
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
                # Read 4-byte header for message length
                header = await self._recv_all(reader, 4)
                if header is None:
                    logger.info(f"xApp client {client_id} disconnected")
                    break
                    
                # Unpack length (big-endian unsigned int)
                length = struct.unpack("!I", header)[0]
                
                if length == 0 or length > 1024 * 1024:  # 1MB max
                    logger.error(f"Invalid frame length={length}, closing connection")
                    break
                
                # Read the message body
                body = await self._recv_all(reader, length)
                if body is None:
                    logger.error("Incomplete frame body")
                    break
                
                # Decode and parse JSON
                try:
                    text = body.decode("utf-8", errors="replace")
                    message = json.loads(text)
                    
                    msg_type = message.get("type", "unknown")
                    logger.info(f"Received {msg_type} message from {client_id}")
                    
                    if msg_type == "kpi":
                        # Store meid for this client
                        meid = message.get("meid", "unknown")
                        self.meid_map[client_id] = meid
                        # Queue KPI for processing
                        await self.kpi_queue.put((client_id, message))
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
        if client_id not in self.clients:
            logger.warning(f"Client {client_id} not connected")
            return False
            
        try:
            writer = self.clients[client_id]
            response_json = json.dumps(command)
            response_bytes = response_json.encode("utf-8")
            length_header = struct.pack("!I", len(response_bytes))
            
            writer.write(length_header + response_bytes)
            await writer.drain()
            logger.info(f"Sent command to {client_id}: {command.get('cmd', {}).get('cmd', 'unknown')}")
            return True
        except Exception as e:
            logger.error(f"Failed to send command to {client_id}: {e}")
            return False


class XAppActor(Actor):
    """Extended Actor that sends commands to xApp over TCP."""
    
    def __init__(self, out_dir: str, tcp_server: XAppTCPServer, meid: str = "gnb:131-133-31000000", node_id: int = 0):
        super().__init__(out_dir)
        self.tcp_server = tcp_server
        self.meid = meid
        self.node_id = node_id
        self.converter = PlaybookToCommandConverter()
        
    async def apply_async(self, playbook: Playbook, client_id: Optional[str] = None):
        """Apply playbook by sending commands to xApp (async version)."""
        # Save to file (original behavior)
        payload = self.make_payload(playbook)
        self.save_payload(payload)
        logger.info(f"Saved playbook: {payload['playbook_id']}")
        
        # Convert to commands and send to xApp
        commands = self.converter.playbook_to_commands(playbook, self.meid, self.node_id)
        
        logger.info(f"Converted playbook to {len(commands)} command(s)")
        for i, cmd in enumerate(commands):
            logger.info(f"  Command {i+1}: {cmd.get('cmd', {}).get('cmd', 'unknown')}")
        
        if commands:
            # If client_id provided, send to that client, otherwise broadcast
            if client_id:
                for cmd in commands:
                    success = await self.tcp_server.send_command(client_id, cmd)
                    if not success:
                        logger.error(f"Failed to send command to {client_id}")
            else:
                # Send to all connected clients
                for cid in list(self.tcp_server.clients.keys()):
                    for cmd in commands:
                        success = await self.tcp_server.send_command(cid, cmd)
                        if not success:
                            logger.error(f"Failed to send command to {cid}")
        else:
            logger.warning("No commands generated from playbook - playbook may contain unsupported actions")


async def run_ai_loop(
    tcp_server: XAppTCPServer,
    cells: List[str],
    slices: List[str],
    target_metric: str = "delay_p95_ms",
    target_value: float = 40.0,
    steps: int = 1000,
    offline_model: Optional[str] = None,
):
    """Run the AI contextual bandit loop."""
    # Initialize components
    action_space = ActionSpace(cells=cells, slices=slices)
    cache = CacheLibrary(max_per_key=20)
    
    # Create predictor and observer
    tmp_intent = Intent(type="REDUCE_LATENCY", metric=target_metric, target=target_value)
    
    # Create a temporary file for observer (will be updated with real KPIs)
    import tempfile
    temp_kpi_file = Path(tempfile.gettempdir()) / "xapp_kpi_temp.json"
    temp_kpi_file.parent.mkdir(parents=True, exist_ok=True)
    # Initialize with empty KPI stream
    with open(temp_kpi_file, "w") as f:
        json.dump({"kpi_stream": []}, f)
    
    # Create observer with temp file (will be updated when KPIs arrive)
    # We need to create a dummy observer first to get feature count
    dummy_observer = RLObserver(predictor=None, intent=tmp_intent, kpi_file=str(temp_kpi_file), window=12)
    
    # Create predictor with correct feature dimension
    predictor = SlateDQNPredictor(
        action_space, 
        feat_dim=len(dummy_observer.features), 
        seed=0
    )
    
    # Now create the real observer with predictor attached
    observer = RLObserver(predictor=predictor, intent=tmp_intent, kpi_file=str(temp_kpi_file), window=12)
    
    # Load pre-trained model if provided
    if offline_model:
        try:
            predictor.load_offline(offline_model)
            logger.info(f"Loaded pre-trained model from {offline_model}")
        except Exception as e:
            logger.warning(f"Could not load pre-trained model ({e}). Starting from scratch.")
    
    # Create actor with TCP server
    actor = XAppActor("configs", tcp_server, meid="gnb:131-133-31000000", node_id=0)
    
    # KPI adapter
    kpi_adapter = XAppKPIAdapter()
    
    # State
    cooldown_clock: Dict = {}
    last_playbook = None
    success_streak = 0
    step = 0
    net_intent = None
    intent_meta = {"intent": "LATENCY_P95", "scope": "GLOBAL"}
    current_client_id = None
    current_meid = "gnb:131-133-31000000"
    
    if offline_model:
        logger.info("AI loop started with pre-trained model, waiting for KPIs from xApp...")
    else:
        logger.info("AI loop started (training from scratch), waiting for KPIs from xApp...")
    
    while step < steps:
        # Wait for KPI from xApp
        try:
            client_id, xapp_message = await asyncio.wait_for(tcp_server.kpi_queue.get(), timeout=5.0)
            current_client_id = client_id
            current_meid = tcp_server.meid_map.get(client_id, current_meid)
            actor.meid = current_meid
            
            logger.info(f"Processing KPI from {client_id}, meid={current_meid}")
            
            # Convert KPI format
            internal_kpi = kpi_adapter.convert_xapp_kpi_to_internal(xapp_message, current_meid)
            logger.debug(f"Converted KPI: metric={internal_kpi.get('CellMetrics', {}).get('delay_p95_ms', 'N/A')}")
            
        except asyncio.TimeoutError:
            logger.debug("No KPI received, waiting...")
            await asyncio.sleep(0.5)
            continue
        
        # Process KPI - write to temp file for observer to read
        import tempfile
        temp_file = Path(tempfile.gettempdir()) / "xapp_kpi_temp.json"
        with open(temp_file, "w") as f:
            json.dump({"kpi_stream": [internal_kpi]}, f)
        
        # Update observer's kpi_file (must be Path object, not string)
        observer.kpi_file = temp_file
        state = observer.step(last_playbook)
        if state is None:
            logger.warning("Observer returned None state")
            await asyncio.sleep(0.5)
            continue
        
        latest = observer.last_kpi_raw or {}
        cell = (latest or {}).get("CellMetrics", {})
        curr = float(cell.get(observer.intent.metric, 0.0))
        hit = (curr <= observer.intent.target) if observer.intent.direction == "lower_better" else (curr >= observer.intent.target)
        success_streak = success_streak + 1 if hit else 0
        
        # Bootstrap or update intent on step==0 or when needed
        if step == 0 or net_intent is None:
            dev_raw = {
                "source": "xapp_kpi",
                "metric": target_metric,
                "value": curr,
                "target": target_value,
                "direction": "lower_better" if ("delay" in target_metric or "latency" in target_metric) else "higher_better",
                "severity": "medium",
                "scope": {
                    "cell_id": cell.get("cell_id") or "CELL_001",
                    "region": "A",
                    "service": "demo",
                    "tenancy": "prod",
                },
                "evidence_ref": "telemetry://xapp/kpi",
            }
            
            dev = normalize_deviation(dev_raw)
            try:
                net_intent = reason_from_deviation(dev)
            except OpenAIError:
                net_intent = fallback_intent_for_deviation(dev)
            
            intent_meta = to_proposer_meta(net_intent)
            rl_cfg = to_rl_intent(net_intent)
            
            new_intent = Intent(
                type=rl_cfg["type"],
                metric=rl_cfg["metric"],
                target=float(rl_cfg["target"]),
                direction=rl_cfg["direction"],
                action_cost=float(rl_cfg.get("action_cost", 0.01)),
                reward_clip=float(rl_cfg.get("reward_clip", 2.0)),
            )
            observer.intent = new_intent
            logger.info(f"Intent set: {observer.intent} (scope={intent_meta['scope']})")
        
        # Generate candidate playbooks
        logger.info(f"Generating {CANDIDATE_N} candidate playbooks (ε={predictor.epsilon():.3f})...")
        candidates = ProposerSampler.sample_playbooks(
            action_space, N=CANDIDATE_N, K=PLAYBOOK_K,
            cooldown_clock=cooldown_clock,
            cache=cache,
            intent_meta=intent_meta,
            epsilon=predictor.epsilon()
        )
        
        # Score playbooks
        logger.info(f"Evaluating Q-values for {len(candidates)} candidates...")
        scored = predictor.score_playbooks(state, candidates)
        scored.sort(key=lambda x: x[1], reverse=True)
        best_pb, best_q = scored[0]
        logger.info(f"Best Q={best_q:.3f}")
        
        # Update cooldown
        for a in best_pb.actions:
            ck = cooldown_key(a)
            cooldown_clock[ck] = max(cooldown_clock.get(ck, 0), COOLDOWN_STEPS)
        for k in list(cooldown_clock.keys()):
            cooldown_clock[k] -= 1
            if cooldown_clock[k] <= 0:
                cooldown_clock.pop(k, None)
        
        # Cache
        cache.add(intent_meta, best_pb, best_q)
        
        logger.info(f"[t={step:03d}] metric={observer.intent.metric}={curr:.2f} hit={hit} streak={success_streak} "
                   f"eps={predictor.epsilon():.3f} q={best_q:.3f}")
        for i, a in enumerate(best_pb.actions):
            logger.info(f"   • A{i+1}: {a.type} {a.scope} cell={a.cell_id} slice={a.slice_id} params={a.params}")
        
        # Apply playbook (sends commands to xApp)
        logger.info(f"[Step {step}] Applying playbook and sending commands to xApp...")
        await actor.apply_async(best_pb, client_id=current_client_id)
        logger.info(f"[Step {step}] Commands sent successfully")
        
        last_playbook = best_pb
        predictor.steps += 1
        step += 1
        
        if success_streak >= 4:
            logger.info(f"Intent achieved for {success_streak} consecutive readings. Resetting streak.")
            success_streak = 0
        
        await asyncio.sleep(0.1)  # Small delay between iterations


async def main():
    parser = argparse.ArgumentParser(description="AI System Demo with xApp TCP Integration")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="TCP server host")
    parser.add_argument("--port", type=int, default=6000, 
                       help="TCP server port (default: 6000 for relay server, use 5000 if connecting directly to xApp)")
    parser.add_argument("--steps", type=int, default=1000, help="Max steps")
    parser.add_argument("--target-metric", type=str, default="delay_p95_ms", help="Target metric")
    parser.add_argument("--target-value", type=float, default=40.0, help="Target value")
    parser.add_argument("--cells", type=str, nargs="+", default=["CELL_001"], help="Cell IDs")
    parser.add_argument("--slices", type=str, nargs="+", default=["SLICE_A"], help="Slice IDs")
    parser.add_argument("--offline-model", type=str, default=None, 
                       help="Path to pre-trained model (e.g., models/qnet_offline.pt). If not provided, starts training from scratch.")
    
    args = parser.parse_args()
    
    # Create TCP server
    tcp_server = XAppTCPServer(host=args.host, port=args.port)
    await tcp_server.start()
    
    try:
        # Run AI loop
        await run_ai_loop(
            tcp_server,
            cells=args.cells,
            slices=args.slices,
            target_metric=args.target_metric,
            target_value=args.target_value,
            steps=args.steps,
            offline_model=args.offline_model,
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await tcp_server.stop()


if __name__ == "__main__":
    asyncio.run(main())

