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
import json
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
        
        if measurements:
            for m in measurements:
                name = m.get("name", "")
                value = m.get("value", 0)
                
                # Map xApp measurement names to internal format
                if "delay" in name.lower() or "latency" in name.lower():
                    cell_metrics["delay_p95_ms"] = float(value)
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
                elif "prb" in name.lower() and "used" in name.lower():
                    cell_metrics["prb_used_dl"] = float(value)
                elif "active" in name.lower() and "ue" in name.lower():
                    cell_metrics["active_ue_count"] = int(value)
        
        # Extract from raw fields if available
        if "UE_PDCP_Delay_DL_ms" in kpi_data:
            cell_metrics["delay_p95_ms"] = float(kpi_data["UE_PDCP_Delay_DL_ms"])
        if "PRB_Used_DL" in kpi_data:
            cell_metrics["prb_used_dl"] = float(kpi_data["PRB_Used_DL"])
        if "Mean_Active_UEs_DL" in kpi_data:
            cell_metrics["active_ue_count"] = int(kpi_data["Mean_Active_UEs_DL"])
        
        # Only set cell_id as fallback (needed for identification)
        # All other metrics are optional - missing values will be handled gracefully
        if "cell_id" not in cell_metrics:
            cell_metrics["cell_id"] = kpi_data.get("cellObjectID", kpi_data.get("cell_id", "CELL_001"))
        
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
    def playbook_to_commands(playbook, meid: str, node_id: int = 0) -> List[Dict[str, Any]]:
        """Convert playbook actions to xApp control commands."""
        commands = []
        
        for action in playbook.actions:
            cmd = None
            
            if action.type == "MCS_CAP":
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
                weight = action.params.get("weight", 1.0)
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
    
    def __init__(self, host: str = "0.0.0.0", port: int = 6000, bus: Optional[MemBus] = None):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.clients: Dict[str, asyncio.StreamWriter] = {}
        self.bus = bus
        self.meid_map: Dict[str, str] = {}
        self.kpi_adapter = XAppKPIAdapter()
        
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
            
            # Convert to commands
            meid = self.default_meid
            commands = self.converter.playbook_to_commands(best_pb, meid, self.node_id)
            
            logger.info(f"Converted playbook to {len(commands)} command(s)")
            
            if commands:
                # Send to all connected clients
                for client_id in list(self.tcp_server.clients.keys()):
                    # Use meid from map if available
                    meid = self.tcp_server.meid_map.get(client_id, self.default_meid)
                    # Update commands with correct meid
                    for cmd in commands:
                        cmd["meid"] = meid
                        success = await self.tcp_server.send_command(client_id, cmd)
                        if not success:
                            logger.error(f"Failed to send command to {client_id}")
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
                # Write to observer's file (atomic write)
                import os
                temp_file_tmp = self.temp_file.with_suffix(self.temp_file.suffix + ".tmp")
                with open(temp_file_tmp, "w") as f:
                    json.dump({"kpi_stream": [kpi]}, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_file_tmp, self.temp_file)
                
                delay = kpi.get('CellMetrics', {}).get('delay_p95_ms', 'N/A')
                
                # Check feature completeness before processing
                cell_metrics = kpi.get('CellMetrics', {})
                available_features = [k for k in self.observer.features if k in cell_metrics and cell_metrics[k] is not None]
                completeness = len(available_features) / len(self.observer.features) if self.observer.features else 0.0
                
                if completeness < self.observer.min_feature_completeness:
                    logger.warning(f"KPI has low feature completeness ({completeness:.1%} < {self.observer.min_feature_completeness:.1%}), waiting for more data. Available: {available_features}, Missing: {[f for f in self.observer.features if f not in available_features]}")
                else:
                    logger.info(f"Processing KPI: delay_p95_ms={delay}, feature completeness={completeness:.1%}")
                
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
    
    # Create observer with feature completeness threshold (default: 50% of features must be present)
    dummy_observer = RLObserver(
        predictor=None, 
        intent=tmp_intent, 
        kpi_file=str(temp_kpi_file), 
        window=12,
        min_feature_completeness=0.5  # Require at least 50% of features to be present
    )
    predictor = SlateDQNPredictor(
        action_space, 
        feat_dim=len(dummy_observer.features), 
        seed=0
    )
    
    if offline_model:
        try:
            predictor.load_offline(offline_model)
            logger.info(f"Loaded pre-trained model from {offline_model}")
        except Exception as e:
            logger.warning(f"Could not load pre-trained model ({e}). Starting from scratch.")
    
    observer = RLObserver(
        predictor=predictor, 
        intent=tmp_intent, 
        kpi_file=str(temp_kpi_file), 
        window=12,
        min_feature_completeness=0.5  # Require at least 50% of features to be present
    )
    
    # Create actor
    actor = Actor("configs")
    converter = PlaybookToCommandConverter()
    actor_agent = XAppActorAgent(bus, actor, tcp_server, converter)
    
    # Create agents according to system design
    # 1. Minirocket Agent (KPI Stream → Minirocket → deviation.detected)
    minirocket_agent = MinirocketAgent(
        bus, 
        model_path=minirocket_model or "models/minirocket.joblib",
        metric=target_metric
    )
    
    # 2. Enhanced Reasoner Agent (deviation.detected + SLO Intents → intent.current)
    reasoner_agent = EnhancedReasonerAgent(
        bus,
        knowledge_base=knowledge_base,
        use_llm=use_llm
    )
    
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
    
    # Run all agents
    logger.info("Starting AI loop with membus architecture (matching system design)...")
    
    tasks = [
        asyncio.create_task(minirocket_agent.run(), name="minirocket_agent"),
        asyncio.create_task(reasoner_agent.run(), name="reasoner_agent"),
        asyncio.create_task(proposer_agent.run(), name="proposer_agent"),
        asyncio.create_task(predictor_agent.run(), name="predictor_agent"),
        asyncio.create_task(observer_bridge.run(), name="observer_bridge"),
        asyncio.create_task(actor_agent.run(), name="actor_agent"),
    ]
    
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
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
    parser.add_argument("--minirocket-model", type=str, default=None, help="Path to MiniRocket model (default: models/minirocket.joblib)")
    parser.add_argument("--use-llm", action="store_true", help="Use LLM for intent reasoning (default: fallback)")
    
    args = parser.parse_args()
    
    # Create TCP server
    tcp_server = XAppTCPServer(host=args.host, port=args.port)
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
            use_llm=args.use_llm,
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await tcp_server.stop()


if __name__ == "__main__":
    asyncio.run(main())
