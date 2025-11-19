# Relay Server Setup Guide

## Overview

The relay server sits between the xApp and the AI server, forwarding messages in both directions. This allows the AI server to be on a different machine or network.

## Architecture

```
┌─────────┐         ┌──────────┐         ┌──────────┐
│  xApp   │────────►│  Relay   │────────►│ AI Server│
│         │  :5000  │  Server  │  :6000  │          │
│         │◄────────│          │◄────────│          │
└─────────┘         └──────────┘         └──────────┘
```

## Setup Steps

### 1. Start the AI Server

```bash
cd /home/hybrid/AI/hybridAI-nextG
poetry shell
python3 src/demo/ain/RL_demo/xapp_demo.py --port 6000
```

The AI server will listen on port 6000 (default) for connections from the relay.

### 2. Start the Relay Server

In your xApp project directory:

```bash
# Set environment variables (optional, defaults shown)
export EXTERNAL_AI_HOST=127.0.0.1  # or IP of AI server machine
export EXTERNAL_AI_PORT=6000

# Run the relay server
python3 relay_server.py
```

The relay server will:
- Listen on port 5000 for xApp connections
- Connect to AI server at `EXTERNAL_AI_HOST:EXTERNAL_AI_PORT` (default: 127.0.0.1:6000)
- Forward KPIs from xApp → AI Server
- Forward commands from AI Server → xApp

### 3. Configure xApp

The xApp should connect to the relay server at:
- **Host**: `host.docker.internal` (if xApp runs in Docker) or `localhost`
- **Port**: `5000` (relay server port)

## Environment Variables

The relay server uses these environment variables:

- `EXTERNAL_AI_HOST`: IP address or hostname of AI server (default: `127.0.0.1`)
- `EXTERNAL_AI_PORT`: Port where AI server listens (default: `6000`)

## Message Flow

### KPI Flow (xApp → AI)
1. xApp sends KPI to relay on port 5000
2. Relay forwards KPI to AI server on port 6000
3. AI processes KPI and generates intent/playbook

### Command Flow (AI → xApp)
1. AI sends control command to relay on port 6000
2. Relay forwards command to xApp on port 5000
3. xApp applies command to network

## Troubleshooting

### Relay can't connect to AI server
- Check that AI server is running: `netstat -tuln | grep 6000`
- Verify `EXTERNAL_AI_HOST` and `EXTERNAL_AI_PORT` are correct
- Check firewall rules if AI server is on different machine

### xApp can't connect to relay
- Check that relay is running: `netstat -tuln | grep 5000`
- Verify xApp is configured to connect to relay's host:port
- Check network connectivity

### Messages not being forwarded
- Check relay logs for connection status
- Verify both connections are established (xApp → Relay, Relay → AI)
- Check message format matches expected protocol

## Testing Without Relay

If you want to test the AI server directly without the relay:

```bash
# Start AI server on port 5000 (direct connection)
python3 src/demo/ain/RL_demo/xapp_demo.py --port 5000

# Configure xApp to connect directly to AI server
```

## Benefits of Using Relay

1. **Separation**: AI server can run on different machine/network
2. **Flexibility**: Easy to swap AI implementations
3. **Debugging**: Centralized logging of all messages
4. **Resilience**: Relay can handle reconnections automatically

