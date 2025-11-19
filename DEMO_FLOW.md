# Complete Demo Flow - AI System with xApp Integration

## Architecture Overview

```
┌─────────────┐         TCP (port 5000)         ┌──────────────┐
│    xApp     │◄─────────────────────────────────┤  AI System   │
│             │                                  │              │
│  - Receives │  KPI Messages                   │  - Observer  │
│    KPIs     │─────────────────────────────────►│  - Reasoner  │
│    from RIC │                                  │  - Proposer  │
│             │  Control Commands                │  - Predictor │
│  - Sends    │◄─────────────────────────────────│  - Actor     │
│    to RIC   │                                  │              │
└─────────────┘                                  └──────────────┘
      │                                                   │
      │ E2 Interface                                      │
      ▼                                                   │
┌─────────────┐                                          │
│     RIC     │                                          │
│             │                                          │
│  - E2       │                                          │
│    Reports  │                                          │
│  - Control  │                                          │
│    Messages │                                          │
└─────────────┘                                          │
      │                                                   │
      │ SCTP                                              │
      ▼                                                   │
┌─────────────┐                                          │
│   ns3 Sim   │                                          │
│             │                                          │
│  - Network  │                                          │
│    Events   │                                          │
│  - Metrics  │                                          │
└─────────────┘                                          │
```

## Step-by-Step Flow

### 1. Initialization

**AI System:**
- Starts TCP server on port 5000
- Initializes components:
  - `RLObserver`: Processes KPIs, computes rewards
  - `SlateDQNPredictor`: Deep Q-Network for playbook scoring
  - `ProposerSampler`: Generates candidate playbooks
  - `XAppActor`: Sends commands to xApp
- Waits for xApp connection

**xApp:**
- Connects to AI server at `host.docker.internal:5000` (or `localhost:5000`)
- Establishes TCP connection
- Ready to send KPIs

### 2. KPI Reception Loop

**Step 2.1: xApp Receives KPIs from RIC**
- RIC sends E2 reports (KPMs) to xApp
- xApp formats KPIs as JSON:
  ```json
  {
    "type": "kpi",
    "meid": "gnb:131-133-31000000",
    "kpi": {
      "cellObjectID": "1112",
      "measurements": [...],
      "ues": [...]
    }
  }
  ```

**Step 2.2: xApp Sends to AI**
- xApp sends length-prefixed frame: `[4 bytes: length][JSON bytes]`
- AI receives and parses message
- AI stores `meid` for this client connection

**Step 2.3: AI Converts KPI Format**
- `XAppKPIAdapter` converts xApp format to internal format
- Maps measurement names to internal metrics:
  - `UE_PDCP_Delay_DL_ms` → `delay_p95_ms`
  - `PRB_Used_DL` → `prb_used_dl`
  - `Mean_Active_UEs_DL` → `active_ue_count`
  - etc.

**Step 2.4: AI Processes KPI**
- Writes KPI to temp file (observer reads from file)
- `RLObserver` reads KPI and:
  - Extracts features (throughput, BLER, CQI, MCS, delay, etc.)
  - Updates sliding window of states
  - Computes reward based on intent

### 3. Intent Creation (First Iteration)

**Step 3.1: Detect Deviation**
- AI checks if metric violates target
- Example: `delay_p95_ms = 150.5` > target `40.0`

**Step 3.2: Create Network Intent**
- `ReasonerAgent` (or fallback) creates intent:
  ```python
  {
    "intent_id": "uuid",
    "category": "performance",
    "goal": "restore_slo",
    "scope": {"cell_id": "CELL_001", ...},
    "slo": {"latency_ms": 40.0}
  }
  ```

**Step 3.3: Convert to RL Intent**
- Maps to RL format:
  ```python
  Intent(
    type="REDUCE_LATENCY",
    metric="delay_p95_ms",
    target=40.0,
    direction="lower_better"
  )
  ```

### 4. Playbook Generation

**Step 4.1: Generate Candidates**
- `ProposerSampler` generates N=5 candidate playbooks
- Each playbook contains K=3 actions
- Actions sampled from action space:
  - `MCS_CAP`: Set MCS limit (14, 18, 22)
  - `PRB_WEIGHT`: Set PRB allocation (0.8, 1.0, 1.2)
  - `SCHEDULER_POLICY`: Set scheduler (PF, RR, MAX_THROUGHPUT)
  - `SLICE_QOS`: Set slice QoS
  - `REPORTING`: No-op

**Step 4.2: Respect Constraints**
- Checks cooldown periods (prevents rapid reconfiguration)
- Avoids conflicts (no duplicate actions on same entity)
- Uses cache of successful playbooks (if available)

### 5. Playbook Scoring

**Step 5.1: Encode State and Actions**
- State: GRU encoder processes temporal window `[W, F]`
- Actions: MLP encoder processes one-hot action vectors
- Playbook: GRU encoder processes action sequence

**Step 5.2: Compute Q-Values**
- Fusion network combines state and playbook embeddings
- Outputs Q-value for each candidate playbook
- Higher Q-value = better expected reward

**Step 5.3: Select Best Playbook**
- Choose playbook with highest Q-value
- Apply epsilon-greedy exploration (20% → 5% over time)

### 6. Command Execution

**Step 6.1: Convert Playbook to Commands**
- `PlaybookToCommandConverter` maps actions:
  - `MCS_CAP` → `set-mcs` command
  - `PRB_WEIGHT` → `set-bandwidth` command
  - Others → logged as warnings (not directly supported)

**Step 6.2: Send to xApp**
- `XAppActor` sends commands over TCP:
  ```json
  {
    "type": "control",
    "meid": "gnb:131-133-31000000",
    "cmd": {
      "cmd": "set-mcs",
      "node": 0,
      "mcs": 18
    }
  }
  ```
- Length-prefixed frame: `[4 bytes: length][JSON bytes]`

**Step 6.3: xApp Forwards to RIC**
- xApp extracts `cmd` object
- Sends RIC-CONTROL-REQUEST via E2 interface
- RIC forwards to ns3 simulation

**Step 6.4: ns3 Applies Command**
- `MmWaveEnbNetDevice::ControlMessageReceivedCallback()` receives command
- `RicControlMessage::ApplySimpleCommand()` parses JSON
- Scheduler applies change (e.g., sets fixed MCS=18)

### 7. Learning Loop

**Step 7.1: Observe Result**
- Network responds to command
- New KPIs flow back through RIC → xApp → AI
- Observer computes reward:
  ```
  reward = relative_improvement - action_cost - violation_penalty
  ```

**Step 7.2: Update Model**
- Store transition: `(state, playbook, reward, next_state)`
- Sample batch from replay buffer
- Train Q-network using TD-learning
- Soft update target network

**Step 7.3: Repeat**
- Loop continues with new KPIs
- Intent achieved when metric meets target for 4 consecutive readings
- New intent created if deviation detected

## Key Components

### XAppKPIAdapter
- Converts xApp KPI format → internal format
- Handles measurement name mapping
- Sets defaults for missing metrics

### PlaybookToCommandConverter
- Maps playbook actions → xApp commands
- Handles unsupported actions gracefully
- Extracts parameters (MCS values, bandwidth, etc.)

### XAppTCPServer
- Manages TCP connections
- Handles length-prefixed protocol
- Queues KPIs for processing
- Sends commands to clients

### XAppActor
- Extends base `Actor` class
- Sends commands over TCP instead of just saving files
- Maintains client connection mapping

## Example Timeline

```
t=0s:  AI server starts, listening on port 5000
t=1s:  xApp connects
t=2s:  xApp sends KPI: delay_p95_ms=150.5
t=2s:  AI creates intent: REDUCE_LATENCY, target=40.0
t=2s:  AI generates 5 playbooks
t=2s:  AI scores playbooks, selects: MCS_CAP=18
t=2s:  AI sends: {"cmd":"set-mcs","mcs":18}
t=2s:  xApp forwards to RIC
t=2s:  ns3 applies MCS=18
t=4s:  xApp sends new KPI: delay_p95_ms=120.0
t=4s:  AI computes reward: +0.20 (improvement)
t=4s:  AI updates Q-network
t=4s:  AI generates new playbooks
t=4s:  AI sends: {"cmd":"set-mcs","mcs":22}
...
t=20s: KPI: delay_p95_ms=35.0 (target met!)
t=20s: Success streak: 1
t=22s: KPI: delay_p95_ms=38.0 (target met!)
t=22s: Success streak: 2
...
t=28s: Success streak: 4 → Intent achieved!
```

## Testing the Flow

1. **Start AI server:**
   ```bash
   python src/demo/ain/RL_demo/xapp_demo.py --port 5000
   ```

2. **Start xApp** (connects to AI automatically)

3. **Monitor logs:**
   - AI logs: Intent creation, playbook generation, command sending
   - xApp logs: KPI reception, command forwarding
   - ns3 logs: Command application, network changes

4. **Check results:**
   - Playbooks saved in `configs/` directory
   - Network metrics improve over time
   - Commands applied successfully

## Troubleshooting

- **No KPIs received**: Check xApp connection, verify message format
- **Commands not sent**: Check playbook actions, verify client connection
- **No improvement**: Adjust target values, check reward function
- **Connection lost**: Check network, verify port accessibility

