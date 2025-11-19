# xApp Integration Demo - Quick Start Guide

This guide explains how to run the AI system integrated with the xApp over TCP.

## Prerequisites

### Install Dependencies

The project uses Poetry for dependency management. Install dependencies with:

```bash
# If you have Poetry installed
poetry install

# Or install manually with pip
pip install numpy pandas scikit-learn sktime pydantic loguru pyarrow numba llvmlite matplotlib torch
```

### Verify Installation

```bash
python3 -c "import torch; import numpy; print('Dependencies OK')"
```

## Overview

The demo script (`xapp_demo.py`) runs a complete AI contextual bandit system that:
1. **Listens** on TCP port 6000 (default) for KPI messages from relay server or xApp
2. **Processes** KPIs and creates optimization intents
3. **Generates** playbooks (action sequences) to optimize network performance
4. **Sends** control commands back to relay server or xApp over TCP

### Architecture Options

**Option 1: With Relay Server (Recommended)**
```
xApp → Relay (port 5000) → AI Server (port 6000)
```

**Option 2: Direct Connection**
```
xApp → AI Server (port 5000)
```

## Protocol

### KPI Messages (xApp → AI)
```
Length-prefixed frame: [uint32 len][JSON bytes]

JSON format:
{
  "type": "kpi",
  "meid": "gnb:131-133-31000000",
  "kpi": {
    "cellObjectID": "1112",
    "measurements": [
      {"name": "UE_PDCP_Delay_DL_ms", "value": 150.5},
      {"name": "PRB_Used_DL", "value": 50},
      ...
    ],
    "ues": [...]
  }
}
```

### Control Commands (AI → xApp)
```
Length-prefixed frame: [uint32 len][JSON bytes]

JSON format:
{
  "type": "control",
  "meid": "gnb:131-133-31000000",
  "cmd": {
    "cmd": "set-mcs",
    "node": 0,
    "mcs": 15
  }
}
```

## Available Commands

The AI can send these commands to xApp:

1. **`set-mcs`** - Set Modulation and Coding Scheme
   ```json
   {"cmd": "set-mcs", "node": 0, "mcs": 15}
   ```
   - `mcs`: 0-28 (mmWave range), or <0 to disable fixed MCS

2. **`set-bandwidth`** - Set Bandwidth
   ```json
   {"cmd": "set-bandwidth", "node": 0, "bandwidth": 100}
   ```
   - `bandwidth`: Resource blocks (RBs)

3. **`move-enb`** - Move Base Station
   ```json
   {"cmd": "move-enb", "node": 0, "dx": 10.0, "dy": 5.0, "dz": 0.0}
   ```

4. **`stop`** - Stop Simulation
   ```json
   {"cmd": "stop"}
   ```

## How to Run

### 1. Start the AI Server

**Using Poetry (recommended):**
```bash
cd /home/hybrid/AI/hybridAI-nextG
poetry run python3 src/demo/ain/RL_demo/xapp_demo.py --host 0.0.0.0 --port 6000
```

**Or activate Poetry shell first:**
```bash
cd /home/hybrid/AI/hybridAI-nextG
poetry shell
python3 src/demo/ain/RL_demo/xapp_demo.py --host 0.0.0.0 --port 6000
```

**Note:** If you're using a relay server (which listens on port 5000 for xApp), the AI server should listen on port 6000 (or whatever `EXTERNAL_AI_PORT` is set to in the relay). If connecting directly to xApp without a relay, use port 5000.

**Options:**
- `--host`: TCP server host (default: `0.0.0.0`)
- `--port`: TCP server port (default: `5000`)
- `--steps`: Max number of optimization steps (default: `1000`)
- `--target-metric`: Metric to optimize (default: `delay_p95_ms`)
- `--target-value`: Target value for metric (default: `40.0`)
- `--cells`: Cell IDs (default: `CELL_001`)
- `--slices`: Slice IDs (default: `SLICE_A`)

**Example:**
```bash
poetry run python3 src/demo/ain/RL_demo/xapp_demo.py \
  --port 5000 \
  --target-metric delay_p95_ms \
  --target-value 30.0 \
  --cells CELL_001 CELL_002 \
  --steps 500
```

### 2. Configure xApp to Connect

The xApp should connect to the AI server at:
- **Host**: `host.docker.internal` (if xApp runs in Docker) or `localhost`
- **Port**: `5000` (or whatever you specified)

The xApp will automatically connect when it starts.

### 3. What Happens

1. **xApp connects** → AI server logs: `"xApp client connected: <ip>:<port>"`
2. **xApp sends KPIs** → AI processes them and logs: `"Received kpi message from <client>"`
3. **AI generates intent** → Logs: `"Intent set: <intent details>"`
4. **AI creates playbooks** → Logs: `"Generating 5 candidate playbooks..."`
5. **AI scores playbooks** → Logs: `"Best Q=<score>"`
6. **AI sends commands** → Logs: `"Sent command to <client>: <command>"`
7. **Loop continues** → Process repeats with new KPIs

## KPI Format Conversion

The AI expects KPIs in this internal format:
```json
{
  "timestamp": "2025-11-19T18:37:41.925868Z",
  "Header": {
    "ric_instance_id": "gnb:131-133-31000000",
    "sequence_number": 12345
  },
  "CellMetrics": {
    "cell_id": "1112",
    "delay_p95_ms": 150.5,
    "thr_dl_bps": 50000000,
    "prb_used_dl": 50,
    "active_ue_count": 5,
    ...
  },
  "UEMetrics": [...]
}
```

The `XAppKPIAdapter` automatically converts xApp KPI format to this internal format.

## Playbook to Command Mapping

The AI generates playbooks with actions like:
- `MCS_CAP` → Maps to `set-mcs` command
- `PRB_WEIGHT` → Maps to `set-bandwidth` command
- `SCHEDULER_POLICY` → Not directly supported (logged as warning)
- `SLICE_QOS` → Not directly supported (logged as warning)
- `REPORTING` → No-op (skipped)

## Output Files

The AI saves playbooks to:
- `configs/` directory (created automatically)
- Files named: `pb_<timestamp>.json`

Each playbook JSON contains:
- `playbook_id`: Unique identifier
- `created_at`: Timestamp
- `intent`: Optimization intent
- `metadata`: Q-score, step number, etc.
- `actions`: List of control actions

## Troubleshooting

### AI server not receiving KPIs
- Check that xApp is connecting: Look for `"xApp client connected"` in logs
- Verify xApp is sending messages with `"type": "kpi"`
- Check network connectivity between xApp and AI server

### Commands not being sent
- Check that playbooks contain supported actions (`MCS_CAP`, `PRB_WEIGHT`)
- Look for warnings about unsupported action types
- Verify client is still connected (check `"xApp connection closed"` messages)

### Observer returning None state
- Ensure KPI format is correct
- Check that required metrics are present (delay_p95_ms, etc.)
- Verify temp file is being written correctly

## Example Log Output

```
2025-11-19 18:37:41 - INFO - xApp TCP server started on 0.0.0.0:5000
2025-11-19 18:37:42 - INFO - xApp client connected: 172.17.0.2:54321
2025-11-19 18:37:43 - INFO - Received kpi message from 172.17.0.2:54321
2025-11-19 18:37:43 - INFO - Intent set: Intent(type='REDUCE_LATENCY', metric='delay_p95_ms', target=40.0)
2025-11-19 18:37:43 - INFO - Generating 5 candidate playbooks (ε=0.200)...
2025-11-19 18:37:44 - INFO - Evaluating Q-values for 5 candidates...
2025-11-19 18:37:44 - INFO - Best Q=0.523
2025-11-19 18:37:44 - INFO - [t=000] metric=delay_p95_ms=150.50 hit=False streak=0 eps=0.200 q=0.523
2025-11-19 18:37:44 - INFO -    • A1: MCS_CAP CELL cell=CELL_001 params={'dl_mcs_max': 18}
2025-11-19 18:37:44 - INFO - Sent command to 172.17.0.2:54321: set-mcs
```

## Next Steps

1. **Monitor performance**: Watch how the AI optimizes the target metric
2. **Adjust targets**: Change `--target-value` to see different optimization goals
3. **Add more cells/slices**: Use `--cells` and `--slices` for multi-cell scenarios
4. **Tune hyperparameters**: Modify predictor, proposer, or observer parameters in code

