"""
AI Dashboard WebSocket Server

Subscribes to membus events and broadcasts them to connected web clients.
"""

import asyncio
import json
import logging
from typing import Set
from datetime import datetime, timezone
import websockets
from websockets.server import WebSocketServerProtocol
from pathlib import Path

logger = logging.getLogger(__name__)

class DashboardServer:
    """WebSocket server for real-time AI monitoring dashboard."""
    
    def __init__(self, bus, host: str = "0.0.0.0", port: int = 8081):
        self.bus = bus
        self.host = host
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.static_dir = Path(__file__).parent / "static"
        
    async def register_client(self, websocket: WebSocketServerProtocol):
        """Register a new client connection."""
        self.clients.add(websocket)
        logger.info(f"[DASHBOARD] Client connected. Total clients: {len(self.clients)}")
        
    async def unregister_client(self, websocket: WebSocketServerProtocol):
        """Unregister a client connection."""
        self.clients.discard(websocket)
        logger.info(f"[DASHBOARD] Client disconnected. Total clients: {len(self.clients)}")
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        if not self.clients:
            return
            
        message_json = json.dumps(message)
        disconnected = set()
        
        for client in self.clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
        
        # Clean up disconnected clients
        for client in disconnected:
            await self.unregister_client(client)
    
    async def handle_client(self, websocket: WebSocketServerProtocol):
        """Handle WebSocket client connection."""
        await self.register_client(websocket)
        
        try:
            # Send initial state
            await websocket.send(json.dumps({
                "type": "connected",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": "Connected to AI Dashboard"
            }))
            
            # Keep connection alive
            async for message in websocket:
                # Echo back for ping/pong
                await websocket.send(message)
                
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister_client(websocket)
    
    async def subscribe_to_events(self):
        """Subscribe to membus events and broadcast to clients."""
        # Subscribe to all relevant topics
        q_intent = await self.bus.sub("intent.current")
        q_deviation = await self.bus.sub("deviation.detected")
        q_kpi = await self.bus.sub("kpi.raw")  # Use kpi.raw and accumulate UE metrics
        q_command = await self.bus.sub("command.sent")
        
        # Accumulate UE metrics from fragments
        ue_metrics_accumulator = {}  # ue_id -> ue_metric_dict
        
        async def handle_intents():
            while True:
                msg = await q_intent.get()
                await self.broadcast({
                    "type": "intent",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": msg.payload
                })
        
        async def handle_deviations():
            while True:
                msg = await q_deviation.get()
                await self.broadcast({
                    "type": "deviation",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": msg.payload
                })
        
        async def handle_kpis():
            nonlocal ue_metrics_accumulator
            
            while True:
                try:
                    msg = await q_kpi.get()
                    kpi = msg.payload.get("kpi", {})
                    
                    # Extract cell and UE metrics
                    cell_metrics = kpi.get("CellMetrics", {})
                    ue_metrics_fragment = kpi.get("UEMetrics", [])
                    
                    # Accumulate UE metrics (merge fragments)
                    for ue_metric in ue_metrics_fragment:
                        ue_id = ue_metric.get("ue_id")
                        if ue_id:
                            if ue_id not in ue_metrics_accumulator:
                                ue_metrics_accumulator[ue_id] = {}
                            # Merge this fragment into accumulated data
                            ue_metrics_accumulator[ue_id].update(ue_metric)
                    
                    # Convert accumulator to list
                    ue_metrics_list = list(ue_metrics_accumulator.values())
                    
                    logger.debug(f"[DASHBOARD] Broadcasting KPI: {len(ue_metrics_list)} UEs")
                    
                    await self.broadcast({
                        "type": "kpi",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "data": {
                            "cell": cell_metrics,
                            "ues": ue_metrics_list
                        }
                    })
                except Exception as e:
                    logger.error(f"[DASHBOARD] Error handling KPI: {e}", exc_info=True)
        
        async def handle_commands():
            while True:
                msg = await q_command.get()
                await self.broadcast({
                    "type": "command",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": msg.payload
                })
        
        # Run all handlers concurrently
        await asyncio.gather(
            handle_intents(),
            handle_deviations(),
            handle_kpis(),
            handle_commands()
        )
    
    async def run(self):
        """Start the WebSocket server and event subscription."""
        logger.info(f"[DASHBOARD] Starting WebSocket server on ws://{self.host}:{self.port}")
        
        # Start WebSocket server
        async with websockets.serve(self.handle_client, self.host, self.port):
            logger.info(f"[DASHBOARD] Dashboard available at http://{self.host}:{self.port}")
            
            # Subscribe to events
            await self.subscribe_to_events()


async def start_dashboard(bus, host: str = "0.0.0.0", port: int = 8081):
    """Start dashboard server as background task."""
    server = DashboardServer(bus, host, port)
    await server.run()
