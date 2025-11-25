#!/usr/bin/env python3
"""
AI Relay Server
Relays messages between xApp and external AI server:
- Receives KPIs from xApp → forwards to external AI
- Receives commands from external AI → forwards to xApp

This is a pure relay - it does NOT generate commands itself.
"""

import socket
import struct
import json
import threading
import time
import sys
import os
import csv
from datetime import datetime

# Configuration
XAPP_LISTEN_HOST = "0.0.0.0"
XAPP_LISTEN_PORT = 5000  # Port where xApp connects to

# Command interface port (for test scripts)
CMD_INTERFACE_PORT = 5002

# External AI server configuration
EXTERNAL_AI_HOST = os.getenv("EXTERNAL_AI_HOST", "127.0.0.1")
EXTERNAL_AI_PORT = int(os.getenv("EXTERNAL_AI_PORT", "6000"))

MAX_FRAME = 1024 * 1024  # 1MB max frame size

# Track active connections
xapp_connections = {}  # {addr: conn} - connections from xApp
ai_connection = None  # Connection to external AI server
connections_lock = threading.Lock()

# CSV output files
CSV_GNB_FILE = "gnb_kpis.csv"
CSV_UE_FILE = "ue_kpis.csv"

# CSV writers and file handles
gnb_csv_file = None
ue_csv_file = None
gnb_csv_writer = None
ue_csv_writer = None
gnb_fieldnames = None
ue_fieldnames = None

def get_measurement_label(name):
    """Convert measurement name to human-readable label"""
    # Simple sanitization - can be enhanced with full mapping from ai_dummy_server if needed
    return name.replace(".", "_").replace(" ", "_")

def init_csv_files():
    """Initialize CSV files with headers"""
    global gnb_csv_file, ue_csv_file, gnb_csv_writer, ue_csv_writer, gnb_fieldnames, ue_fieldnames
    
    # Initialize gNB CSV
    file_exists = os.path.exists(CSV_GNB_FILE)
    if file_exists:
        with open(CSV_GNB_FILE, 'r') as f:
            reader = csv.DictReader(f)
            gnb_fieldnames = reader.fieldnames or ['timestamp', 'meid', 'cell_id', 'format']
    else:
        gnb_fieldnames = ['timestamp', 'meid', 'cell_id', 'format']
    
    gnb_csv_file = open(CSV_GNB_FILE, 'a', newline='')
    gnb_csv_writer = csv.DictWriter(gnb_csv_file, fieldnames=gnb_fieldnames, extrasaction='ignore')
    if not file_exists:
        gnb_csv_writer.writeheader()
    
    # Initialize UE CSV
    file_exists = os.path.exists(CSV_UE_FILE)
    if file_exists:
        with open(CSV_UE_FILE, 'r') as f:
            reader = csv.DictReader(f)
            ue_fieldnames = reader.fieldnames or ['timestamp', 'meid', 'cell_id', 'ue_id']
    else:
        ue_fieldnames = ['timestamp', 'meid', 'cell_id', 'ue_id']
    
    ue_csv_file = open(CSV_UE_FILE, 'a', newline='')
    ue_csv_writer = csv.DictWriter(ue_csv_file, fieldnames=ue_fieldnames, extrasaction='ignore')
    if not file_exists:
        ue_csv_writer.writeheader()
    
    print(f"[RELAY] CSV logging enabled: {CSV_GNB_FILE}, {CSV_UE_FILE}")

def close_csv_files():
    """Close CSV files"""
    global gnb_csv_file, ue_csv_file
    if gnb_csv_file:
        gnb_csv_file.close()
    if ue_csv_file:
        ue_csv_file.close()

def write_gnb_csv(timestamp, meid, cell_id, format_type, measurements):
    """Write gNB (cell-level) measurements to CSV"""
    global gnb_csv_writer, gnb_fieldnames, gnb_csv_file
    
    if not gnb_csv_writer:
        return
    
    row = {
        'timestamp': timestamp,
        'meid': meid,
        'cell_id': cell_id,
        'format': format_type
    }
    
    for meas in measurements:
        name = meas.get("name", f"id_{meas.get('id', 'unknown')}")
        value = meas.get("value", "")
        csv_name = get_measurement_label(name)
        row[csv_name] = value
        
        if csv_name not in gnb_fieldnames:
            gnb_fieldnames.append(csv_name)
            gnb_csv_file.close()
            existing_data = []
            if os.path.exists(CSV_GNB_FILE):
                with open(CSV_GNB_FILE, 'r') as f:
                    reader = csv.DictReader(f)
                    existing_data = list(reader)
            gnb_csv_file = open(CSV_GNB_FILE, 'w', newline='')
            gnb_csv_writer = csv.DictWriter(gnb_csv_file, fieldnames=gnb_fieldnames, extrasaction='ignore')
            gnb_csv_writer.writeheader()
            for old_row in existing_data:
                gnb_csv_writer.writerow(old_row)
            gnb_csv_file.close()
            gnb_csv_file = open(CSV_GNB_FILE, 'a', newline='')
            gnb_csv_writer = csv.DictWriter(gnb_csv_file, fieldnames=gnb_fieldnames, extrasaction='ignore')
    
    gnb_csv_writer.writerow(row)
    gnb_csv_file.flush()

def write_ue_csv(timestamp, meid, cell_id, ue_id, measurements):
    """Write UE measurements to CSV"""
    global ue_csv_writer, ue_fieldnames, ue_csv_file
    
    if not ue_csv_writer:
        return
    
    row = {
        'timestamp': timestamp,
        'meid': meid,
        'cell_id': cell_id,
        'ue_id': ue_id
    }
    
    for meas in measurements:
        name = meas.get("name", f"id_{meas.get('id', 'unknown')}")
        value = meas.get("value", "")
        csv_name = get_measurement_label(name)
        row[csv_name] = value
        
        if csv_name not in ue_fieldnames:
            ue_fieldnames.append(csv_name)
            ue_csv_file.close()
            existing_data = []
            if os.path.exists(CSV_UE_FILE):
                with open(CSV_UE_FILE, 'r') as f:
                    reader = csv.DictReader(f)
                    existing_data = list(reader)
            ue_csv_file = open(CSV_UE_FILE, 'w', newline='')
            ue_csv_writer = csv.DictWriter(ue_csv_file, fieldnames=ue_fieldnames, extrasaction='ignore')
            ue_csv_writer.writeheader()
            for old_row in existing_data:
                ue_csv_writer.writerow(old_row)
            ue_csv_file.close()
            ue_csv_file = open(CSV_UE_FILE, 'a', newline='')
            ue_csv_writer = csv.DictWriter(ue_csv_file, fieldnames=ue_fieldnames, extrasaction='ignore')
    
    ue_csv_writer.writerow(row)
    ue_csv_file.flush()

def process_kpi_for_csv(msg):
    """Process KPI message and write to CSV files"""
    try:
        kpi = msg.get("kpi", {})
        if not kpi:
            return
        
        meid = msg.get("meid", "unknown")
        timestamp = int(datetime.now().timestamp() * 1000)  # Milliseconds timestamp
        
        # Extract cell ID
        cell_id = kpi.get("cellObjectID", "N/A")
        format_type = kpi.get("format", "unknown")
        
        # Get measurements and UEs
        measurements = kpi.get("measurements", [])
        ues = kpi.get("ues", [])
        
        # Filter non-zero measurements
        non_zero_measurements = [m for m in measurements if m.get("value", 0) != 0]
        
        # Write gNB (cell-level) measurements
        if non_zero_measurements:
            write_gnb_csv(timestamp, meid, cell_id, format_type, non_zero_measurements)
        
        # Write UE measurements (one row per UE)
        for ue in ues:
            ue_id = ue.get("ueId", "N/A")
            ue_measurements = [m for m in ue.get("measurements", []) if m.get("value", 0) != 0]
            if ue_measurements:
                write_ue_csv(timestamp, meid, cell_id, ue_id, ue_measurements)
    except Exception as e:
        print(f"[RELAY] Error processing KPI for CSV: {e}")

def recv_framed(conn):
    """Receive a length-prefixed frame from connection"""
    try:
        # Read length (4 bytes, network byte order)
        len_data = conn.recv(4)
        if len(len_data) < 4:
            return None
        
        msg_len = struct.unpack("!I", len_data)[0]
        if msg_len == 0 or msg_len > MAX_FRAME:
            print(f"[RELAY] Invalid message length: {msg_len}")
            return None
        
        # Read message body
        msg_data = b""
        while len(msg_data) < msg_len:
            chunk = conn.recv(msg_len - len(msg_data))
            if not chunk:
                return None
            msg_data += chunk
        
        return msg_data.decode("utf-8")
    except Exception as e:
        print(f"[RELAY] Error receiving framed message: {e}")
        return None

def send_framed(conn, text):
    """Send a length-prefixed frame to connection"""
    try:
        msg_bytes = text.encode("utf-8")
        frame = struct.pack("!I", len(msg_bytes)) + msg_bytes
        conn.sendall(frame)
        return True
    except Exception as e:
        print(f"[RELAY] Error sending framed message: {e}")
        return False

def connect_to_external_ai():
    """Connect to external AI server"""
    global ai_connection
    
    while True:
        try:
            print(f"[RELAY] Connecting to external AI at {EXTERNAL_AI_HOST}:{EXTERNAL_AI_PORT}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((EXTERNAL_AI_HOST, EXTERNAL_AI_PORT))
            ai_connection = sock
            print(f"[RELAY] ✅ Connected to external AI at {EXTERNAL_AI_HOST}:{EXTERNAL_AI_PORT}")
            
            # Start receiving messages from external AI
            threading.Thread(target=receive_from_ai, args=(sock,), daemon=True).start()
            break
            
        except ConnectionRefusedError:
            print(f"[RELAY] ⚠️  External AI not available at {EXTERNAL_AI_HOST}:{EXTERNAL_AI_PORT}, retrying in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            print(f"[RELAY] ❌ Error connecting to external AI: {e}, retrying in 5 seconds...")
            time.sleep(5)

def receive_from_ai(conn):
    """Receive messages from external AI and forward to xApp"""
    global ai_connection
    
    try:
        while True:
            text = recv_framed(conn)
            if text is None:
                break
            
            try:
                msg = json.loads(text)
                msg_type = msg.get("type", "unknown")
                
                print(f"[RELAY] ← Received from AI: type={msg_type}, size={len(text)} bytes")
                
                if msg_type == "control":
                    # Forward control command to all connected xApps
                    meid = msg.get("meid", "")
                    cmd = msg.get("cmd", {})
                    print(f"[RELAY] → Forwarding control command to xApp: meid={meid}, cmd={cmd}")
                    
                    with connections_lock:
                        if not xapp_connections:
                            print(f"[RELAY] ⚠️  No xApp connections available, dropping command")
                        else:
                            # Forward to all connected xApps (usually just one)
                            for addr, xapp_conn in list(xapp_connections.items()):
                                try:
                                    if send_framed(xapp_conn, text):
                                        print(f"[RELAY] ✅ Forwarded control command to xApp {addr}")
                                    else:
                                        print(f"[RELAY] ❌ Failed to forward to xApp {addr}")
                                except Exception as e:
                                    print(f"[RELAY] ❌ Error forwarding to xApp {addr}: {e}")
                else:
                    print(f"[RELAY] ⚠️  Unknown message type from AI: {msg_type}")
                    
            except json.JSONDecodeError as e:
                print(f"[RELAY] ❌ Invalid JSON from AI: {e}")
                print(f"[RELAY] Raw: {text[:200]}...")
            except Exception as e:
                print(f"[RELAY] ❌ Error processing message from AI: {e}")
                import traceback
                traceback.print_exc()
                
    except Exception as e:
        print(f"[RELAY] ❌ Connection to external AI lost: {e}")
    finally:
        with connections_lock:
            ai_connection = None
        conn.close()
        print(f"[RELAY] Reconnecting to external AI...")
        # Reconnect in background
        threading.Thread(target=connect_to_external_ai, daemon=True).start()

def handle_xapp_connection(conn, addr):
    """Handle connection from xApp"""
    global ai_connection
    
    print(f"[RELAY] ✅ xApp connected from {addr}")
    
    with connections_lock:
        xapp_connections[addr] = conn
    
    try:
        while True:
            text = recv_framed(conn)
            if text is None:
                break
            
            try:
                msg = json.loads(text)
                msg_type = msg.get("type", "unknown")
                
                print(f"[RELAY] ← Received from xApp {addr}: type={msg_type}, size={len(text)} bytes")
                
                if msg_type == "kpi":
                    # Write KPI to CSV files
                    process_kpi_for_csv(msg)
                    
                    # Forward KPI to external AI
                    meid = msg.get("meid", "unknown")
                    print(f"[RELAY] → Forwarding KPI to external AI: meid={meid}")
                    
                    with connections_lock:
                        if ai_connection is None:
                            print(f"[RELAY] ⚠️  External AI not connected, dropping KPI")
                        else:
                            try:
                                if send_framed(ai_connection, text):
                                    print(f"[RELAY] ✅ Forwarded KPI to external AI")
                                else:
                                    print(f"[RELAY] ❌ Failed to forward KPI to external AI")
                            except Exception as e:
                                print(f"[RELAY] ❌ Error forwarding KPI: {e}")
                                with connections_lock:
                                    ai_connection = None
                elif msg_type == "recommendation_request":
                    # Forward recommendation request to external AI
                    meid = msg.get("meid", "unknown")
                    print(f"[RELAY] → Forwarding recommendation request to external AI: meid={meid}")
                    
                    with connections_lock:
                        if ai_connection is None:
                            print(f"[RELAY] ⚠️  External AI not connected, dropping request")
                            # Send empty response back to xApp
                            reply = json.dumps({"no_action": True})
                            send_framed(conn, reply)
                        else:
                            try:
                                if send_framed(ai_connection, text):
                                    # Wait for response from AI and forward back to xApp
                                    response = recv_framed(ai_connection)
                                    if response:
                                        print(f"[RELAY] ← Received recommendation response from AI, forwarding to xApp")
                                        send_framed(conn, response)
                                    else:
                                        print(f"[RELAY] ❌ No response from AI, sending no_action")
                                        reply = json.dumps({"no_action": True})
                                        send_framed(conn, reply)
                                else:
                                    print(f"[RELAY] ❌ Failed to forward recommendation request")
                                    reply = json.dumps({"no_action": True})
                                    send_framed(conn, reply)
                            except Exception as e:
                                print(f"[RELAY] ❌ Error forwarding recommendation request: {e}")
                                with connections_lock:
                                    ai_connection = None
                                reply = json.dumps({"no_action": True})
                                send_framed(conn, reply)
                else:
                    print(f"[RELAY] ⚠️  Unknown message type from xApp: {msg_type}")
                    
            except json.JSONDecodeError as e:
                print(f"[RELAY] ❌ Invalid JSON from xApp: {e}")
                print(f"[RELAY] Raw: {text[:200]}...")
            except Exception as e:
                print(f"[RELAY] ❌ Error processing message from xApp: {e}")
                import traceback
                traceback.print_exc()
                
    except Exception as e:
        print(f"[RELAY] ❌ Connection from xApp {addr} lost: {e}")
    finally:
        with connections_lock:
            if addr in xapp_connections:
                del xapp_connections[addr]
        conn.close()
        print(f"[RELAY] xApp {addr} disconnected")

def handle_command_interface(conn, addr):
    """Handle commands from test scripts via TCP (port 5002)"""
    try:
        print(f"[RELAY] Command interface: Connected from {addr}")
        data = conn.recv(4096)
        if not data:
            return
        
        try:
            cmd_json = json.loads(data.decode("utf-8"))
            meid = cmd_json.get("meid", "")
            cmd = cmd_json.get("cmd", {})
            
            if not meid or not cmd:
                response = {"status": "error", "message": "Missing 'meid' or 'cmd' field"}
                conn.sendall(json.dumps(response).encode("utf-8"))
                return
            
            # Format as control message
            control_msg = {
                "type": "control",
                "meid": meid,
                "cmd": cmd
            }
            control_msg_json = json.dumps(control_msg)
            
            print(f"[RELAY] Command interface: Received command for meid={meid}, cmd={cmd}")
            
            # Forward directly to xApp (bypassing external AI)
            with connections_lock:
                if not xapp_connections:
                    response = {"status": "error", "message": "No xApp connections available"}
                    conn.sendall(json.dumps(response).encode("utf-8"))
                    print(f"[RELAY] ⚠️  No xApp connections available, cannot forward command")
                    return
                
                # Forward to all connected xApps (usually just one)
                success = False
                for xapp_addr, xapp_conn in list(xapp_connections.items()):
                    try:
                        if send_framed(xapp_conn, control_msg_json):
                            print(f"[RELAY] ✅ Forwarded command to xApp {xapp_addr}")
                            success = True
                        else:
                            print(f"[RELAY] ❌ Failed to forward to xApp {xapp_addr}")
                    except Exception as e:
                        print(f"[RELAY] ❌ Error forwarding to xApp {xapp_addr}: {e}")
                
                if success:
                    response = {"status": "ok", "message": f"Command forwarded to xApp for MEID {meid}"}
                else:
                    response = {"status": "error", "message": "Failed to forward command to xApp"}
                
                conn.sendall(json.dumps(response).encode("utf-8"))
                
        except json.JSONDecodeError as e:
            response = {"status": "error", "message": f"Invalid JSON: {e}"}
            conn.sendall(json.dumps(response).encode("utf-8"))
        except Exception as e:
            response = {"status": "error", "message": str(e)}
            conn.sendall(json.dumps(response).encode("utf-8"))
            print(f"[RELAY] Command interface error: {e}")
            import traceback
            traceback.print_exc()
            
    except Exception as e:
        print(f"[RELAY] Command interface error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

def command_interface_server():
    """Run TCP server for command interface on separate port (5002)"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((XAPP_LISTEN_HOST, CMD_INTERFACE_PORT))
            s.listen(5)
            print(f"[RELAY] ✅ Command interface listening on {XAPP_LISTEN_HOST}:{CMD_INTERFACE_PORT}")
            while True:
                conn, addr = s.accept()
                threading.Thread(target=handle_command_interface, args=(conn, addr), daemon=True).start()
    except Exception as e:
        print(f"[RELAY] ❌ Command interface server error: {e}")
        import traceback
        traceback.print_exc()

def main():
    print(f"[RELAY] =========================================")
    print(f"[RELAY] AI Relay Server")
    print(f"[RELAY] =========================================")
    print(f"[RELAY] Listening for xApp on: {XAPP_LISTEN_HOST}:{XAPP_LISTEN_PORT}")
    print(f"[RELAY] Command interface on: {XAPP_LISTEN_HOST}:{CMD_INTERFACE_PORT}")
    print(f"[RELAY] Forwarding to external AI: {EXTERNAL_AI_HOST}:{EXTERNAL_AI_PORT}")
    print(f"[RELAY]")
    print(f"[RELAY] Environment variables:")
    print(f"[RELAY]   EXTERNAL_AI_HOST={EXTERNAL_AI_HOST} (default: 127.0.0.1)")
    print(f"[RELAY]   EXTERNAL_AI_PORT={EXTERNAL_AI_PORT} (default: 6000)")
    print(f"[RELAY]")
    print(f"[RELAY] Starting relay server...")
    print(f"[RELAY]")
    
    # Initialize CSV files
    init_csv_files()
    
    # Start command interface server in background
    threading.Thread(target=command_interface_server, daemon=True).start()
    
    # Connect to external AI in background
    threading.Thread(target=connect_to_external_ai, daemon=True).start()
    
    # Give servers a moment to start
    time.sleep(1)
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((XAPP_LISTEN_HOST, XAPP_LISTEN_PORT))
            s.listen(5)
            print(f"[RELAY] ✅ Listening for xApp connections on {XAPP_LISTEN_HOST}:{XAPP_LISTEN_PORT}")
            
            while True:
                conn, addr = s.accept()
                # Handle each xApp connection in a separate thread
                threading.Thread(target=handle_xapp_connection, args=(conn, addr), daemon=True).start()
                
    except KeyboardInterrupt:
        print("\n[RELAY] Shutting down...")
        close_csv_files()
    except Exception as e:
        print(f"[RELAY] ❌ Server error: {e}")
        import traceback
        traceback.print_exc()
        close_csv_files()

if __name__ == "__main__":
    main()

