import asyncio
import struct
import json
import logging
from typing import Optional, Dict, Any, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

class XAppTCPServer:
    """TCP server compatible with the dummy AI server protocol."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 5000):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.clients: Dict[str, asyncio.StreamWriter] = {}
        self.message_handlers: Dict[str, Callable] = {}
        self.max_frame_size = 1024 * 1024  # 1MB like your dummy server
        
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
        """Handle incoming client connections using the length-prefixed protocol."""
        client_addr = writer.get_extra_info('peername')
        client_id = f"{client_addr[0]}:{client_addr[1]}"
        
        logger.info(f"xApp client connected: {client_id}")
        self.clients[client_id] = writer
        
        msg_count = 0
        try:
            while True:
                # Read 4-byte header for message length
                header = await self._recv_all(reader, 4)
                if header is None:
                    logger.info(f"xApp client {client_id} closed (received {msg_count} messages)")
                    break
                    
                # Unpack length (big-endian unsigned int)
                length = struct.unpack("!I", header)[0]
                
                if length == 0 or length > self.max_frame_size:
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
                    msg_count += 1
                    
                    logger.info(f"Received message #{msg_count} from {client_id}: type={message.get('type', 'unknown')}")
                    
                    await self._process_xapp_message(client_id, message, writer)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error from {client_id}: {e}")
                except Exception as e:
                    logger.error(f"Error processing message from {client_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Connection error with {client_id}: {e}")
        finally:
            if client_id in self.clients:
                del self.clients[client_id]
            writer.close()
            await writer.wait_closed()
            logger.info(f"xApp connection closed: {client_id}")
            
    async def _process_xapp_message(self, client_id: str, message: Dict[str, Any], writer: asyncio.StreamWriter):
        """Process messages from xApp using the same format as dummy server."""
        message_type = message.get("type", "unknown")
        
        if message_type == "kpi":
            # Handle KPI data
            await self._handle_kpi_data(client_id, message)
            
        elif message_type == "recommendation_request":
            # Handle recommendation requests and send response
            response = await self._handle_recommendation_request(client_id, message)
            await self._send_response(writer, response)
            
        elif message_type in self.message_handlers:
            # Handle custom message types
            try:
                response = await self.message_handlers[message_type](client_id, message)
                if response:
                    await self._send_response(writer, response)
            except Exception as e:
                logger.error(f"Error in handler for {message_type}: {e}")
                
        else:
            logger.warning(f"Unknown message type: {message_type}")
            
    async def _handle_kpi_data(self, client_id: str, message: Dict[str, Any]):
        """Handle KPI data from xApp - publish to internal bus."""
        kpi_data = message.get("kpi", {})
        if kpi_data:
            # Extract key information
            meid = message.get("meid", "unknown")
            cell_id = kpi_data.get("cellObjectID", "unknown")
            measurements = kpi_data.get("measurements", [])
            ues = kpi_data.get("ues", [])
            
            # Create structured data for AI system
            processed_kpi = {
                "timestamp": datetime.now().isoformat(),
                "source": "xapp",
                "client_id": client_id,
                "meid": meid,
                "cell_id": cell_id,
                "measurements": measurements,
                "ues": ues,
                "raw_kpi": kpi_data
            }
            
            # Publish to internal message bus (you'll need to inject this)
            if hasattr(self, 'bus') and self.bus:
                await self.bus.pub("kpi_stream", processed_kpi)
                
            logger.info(f"Processed KPI from {client_id}: cell={cell_id}, measurements={len(measurements)}, ues={len(ues)}")
            
    async def _handle_recommendation_request(self, client_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle recommendation requests from xApp."""
        # Process any included KPI data first
        if "kpi" in message:
            await self._handle_kpi_data(client_id, message)
        
        # Generate recommendation (this would integrate with your AI system)
        # For now, return a structured response
        meid = message.get("meid", "unknown")
        
        # This is where you'd interface with your AI recommendation engine
        # For now, return a no-action response like the dummy server
        recommendation = {
            "meid": meid,
            "timestamp": datetime.now().isoformat(),
            "actions": [],
            "no_action": True,
            "reason": "No optimization needed at this time"
        }
        
        # Publish recommendation request to AI system
        if hasattr(self, 'bus') and self.bus:
            await self.bus.pub("recommendation_request", {
                "client_id": client_id,
                "meid": meid,
                "message": message
            })
        
        return recommendation
        
    async def _send_response(self, writer: asyncio.StreamWriter, response: Dict[str, Any]):
        """Send response using length-prefixed protocol."""
        try:
            response_json = json.dumps(response)
            response_bytes = response_json.encode("utf-8")
            length_header = struct.pack("!I", len(response_bytes))
            
            writer.write(length_header + response_bytes)
            await writer.drain()
            
        except Exception as e:
            logger.error(f"Error sending response: {e}")
            
    def register_handler(self, message_type: str, handler: Callable):
        """Register a handler for a specific message type."""
        self.message_handlers[message_type] = handler
        
    async def send_to_client(self, client_id: str, message: Dict[str, Any]) -> bool:
        """Send a message to a specific client."""
        if client_id in self.clients:
            try:
                await self._send_response(self.clients[client_id], message)
                return True
            except Exception as e:
                logger.error(f"Failed to send to client {client_id}: {e}")
                return False
        return False
        
    async def broadcast_to_clients(self, message: Dict[str, Any]):
        """Broadcast a message to all connected xApp clients."""
        for client_id in list(self.clients.keys()):
            await self.send_to_client(client_id, message)
            
    def set_bus(self, bus):
        """Set the message bus for internal communication."""
        self.bus = bus