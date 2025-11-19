# Current Control Commands Implementation Status

## Overview

This document explains what control commands are **currently implemented** in the system and how they work. 

### Direct RIC Control Path (Preferred Method)

The **direct RIC control path** sends commands immediately via E2 RIC-CONTROL-REQUEST messages:

1. **AI** → sends JSON command to xApp via TCP: `{"type":"control","meid":"gnb:131-133-31000000","cmd":{"cmd":"set-mcs","node":0,"mcs":15}}`
2. **xApp** → `AiTcpClient::StartControlCommandListener()` receives it, extracts `meid` and `cmd` JSON
3. **xApp** → calls `XappMsgHandler::send_control(cmd_json, meid)` → `Xapp::send_control_text()` → `send_ric_control_request()`
4. **E2 Termination** → forwards RIC-CONTROL-REQUEST to NS3 over SCTP
5. **NS3** → receives in `MmWaveEnbNetDevice::ControlMessageReceivedCallback()`, applies via `RicControlMessage::ApplySimpleCommand()`

**This is the method used for:** `move-enb`, `stop`, `set-mcs`, `set-bandwidth`

---

## ✅ Currently Implemented Commands (Direct RIC Path)

These commands are **directly supported** in NS3 and can be sent via the RIC control message path:

### 1. `move-enb` - Move Base Station Position
**AI → xApp Format:**
```json
{
  "type": "control",
  "meid": "gnb:131-133-31000000",
  "cmd": {
    "cmd": "move-enb",
    "node": 0,
    "dx": 10.0,
    "dy": 5.0,
    "dz": 0.0
  }
}
```
or
```json
{
  "type": "control",
  "meid": "gnb:131-133-31000000",
  "cmd": {
    "cmd": "move-enb",
    "node": 0,
    "x": 100.0,
    "y": 50.0,
    "z": 0.0
  }
}
```

**Note:** The xApp extracts the inner `cmd` object and sends it to NS3. NS3 receives: `{"cmd":"move-enb","node":0,"dx":10.0,"dy":5.0,"dz":0.0}`

**Parameters:**
- `node`: Node ID (uint32) - the gNB node ID
- `dx`, `dy`, `dz`: Delta movement (double) - **preferred method**
- `x`, `y`, `z`: Absolute position (double) - treated as deltas if dx/dy/dz not provided

**Implementation:** 
- Finds the node by ID
- Gets the `MobilityModel` 
- Moves the eNB position by delta or sets absolute position

**Status:** ✅ **IMPLEMENTED** - Works via `RicControlMessage::ApplySimpleCommand()`

---

### 2. `stop` - Stop Simulation
**AI → xApp Format:**
```json
{
  "type": "control",
  "meid": "gnb:131-133-31000000",
  "cmd": {
    "cmd": "stop"
  }
}
```

**Note:** The xApp extracts the inner `cmd` object and sends it to NS3. NS3 receives: `{"cmd":"stop"}`

**Parameters:** None

**Implementation:** 
- Calls `Simulator::Stop()` immediately
- Terminates the simulation

**Status:** ✅ **IMPLEMENTED** - Works via `RicControlMessage::ApplySimpleCommand()`

---

### 3. `set-mcs` - Set Modulation and Coding Scheme
**AI → xApp Format:**
```json
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

**Note:** The xApp extracts the inner `cmd` object and sends it to NS3. NS3 receives: `{"cmd":"set-mcs","node":0,"mcs":15}`

**Parameters:**
- `node`: Node ID (uint32) - the gNB node
- `mcs`: MCS value (int, 0-28) - valid range for mmWave

**Implementation:** 
- Finds `MmWaveEnbNetDevice` for the node
- Accesses the MAC scheduler (via component carrier)
- Calls `DoSchedSetMcs(mcs)` on `MmWaveFlexTtiMacScheduler`
- Sets both DL and UL MCS to the same value
- If `mcs` is 0-28: enables fixed MCS mode
- If `mcs < 0`: disables fixed MCS, returns to adaptive (AMC)

**How it works:**
- The scheduler has `DoSchedSetMcs(int mcs)` method (line 1747 in `mmwave-flex-tti-mac-scheduler.cc`)
- When fixed MCS is enabled, it sets `m_fixedMcsDl = true` and `m_fixedMcsUl = true`
- The scheduler then uses `m_mcsDefaultDl` and `m_mcsDefaultUl` instead of adaptive MCS

**Status:** ✅ **IMPLEMENTED** - Works via `RicControlMessage::ApplySimpleCommand()`

---

### 4. `set-bandwidth` - Set Bandwidth
**AI → xApp Format:**
```json
{
  "type": "control",
  "meid": "gnb:131-133-31000000",
  "cmd": {
    "cmd": "set-bandwidth",
    "node": 0,
    "bandwidth": 100
  }
}
```

**Note:** The xApp extracts the inner `cmd` object and sends it to NS3. NS3 receives: `{"cmd":"set-bandwidth","node":0,"bandwidth":100}`

**Parameters:**
- `node`: Node ID (uint32, optional - if 0 or missing, searches all nodes)
- `bandwidth`: Bandwidth in RBs (Resource Blocks) (uint8)

**Implementation:**
- Finds `MmWaveEnbNetDevice` for the node
- Calls `SetBandwidth(bandwidth)` on the device
- Updates the PHY/MAC configuration

**Status:** ✅ **IMPLEMENTED** - Works via `RicControlMessage::ApplySimpleCommand()`

---

## 🔧 Current Scenario Setup (`our.cc`)

### Network Topology
- **1 gNB** (base station) at position `(25, 25, 10)` meters
- **1 UE** (user equipment) starting at position `(50, 25, 1.5)` meters
- **1 Remote Host** (RH) for traffic generation
- **EPC** (Evolved Packet Core) with PGW and SGW

### Mobility
- **gNB**: Fixed position (ConstantPositionMobilityModel)
- **UE**: Random walk mobility (RandomWalk2dMobilityModel)
  - Speed: 0.5-2.0 m/s (uniform random)
  - Update interval: 1.0 second
  - Bounds: Rectangle(-120, 120, -120, 120) meters

### Traffic
- **CBR (Constant Bit Rate)**: 50 Mbps UDP traffic from Remote Host to UE
  - Port: 4000
  - Packet size: 1200 bytes
  - Starts at 0.35 seconds
- **Ping**: ICMP ping every 1.0 second
  - Starts at 0.6 seconds
  - RTT logged to `sim_timeseries.csv`

### E2 Configuration
- **E2 Termination IP**: `10.0.2.10` (configurable via `e2TermIp` global value)
- **Indication Periodicity**: 0.1 seconds (100ms)
- **E2 Reports Enabled**:
  - DU (Distributed Unit) reports: ✅ Enabled
  - CU-UP (Control Unit - User Plane) reports: ✅ Enabled
  - CU-CP (Control Unit - Control Plane) reports: ✅ Enabled
- **Reduced PM Values**: `true` (reduced KPI set to prevent E2 termination crashes)

### Scheduler Configuration
- **Type**: `MmWaveFlexTtiMacScheduler` (Flexible TTI MAC Scheduler)
- **HARQ**: Enabled
- **Fixed MCS**: Disabled by default (uses adaptive MCS/AMC)
  - Can be enabled via `set-mcs` command
  - Default MCS values: 10 (DL & UL) - only used when fixed MCS is enabled
- **MCS Range**: 0-28 (mmWave supports higher MCS than LTE)

### RF Configuration
- **Center Frequency**: 28 GHz (mmWave band)
- **Bandwidth**: 56 MHz
- **gNB TX Power**: 10.0 dBm (default)
- **UE Noise Figure**: 7.0 dB

### Propagation Model
- **Pathloss**: `ThreeGppUmiStreetCanyonPropagationLossModel` (3GPP Urban Micro Street Canyon)
- **Channel Condition**: `ThreeGppUmiStreetCanyonChannelConditionModel`

### Output Files
- `sim_timeseries.csv`: Time-series data (throughput, position, MCS, ping RTT)
- `ue_positions.csv`: UE position history
- `gnb_kpis.csv`: gNB-level KPIs (from E2 reports)
- `ue_kpis.csv`: UE-level KPIs (from E2 reports)
- `NetAnimFile_<timestamp>.xml`: NetAnim visualization file


## 📊 How Commands Flow Through the System

### Direct RIC Commands (move-enb, stop, set-mcs, set-bandwidth)

**Command Format from AI:**
```json
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

**Flow:**
```
AI Server (Python)
    ↓ (TCP length-prefixed frame: {"type":"control","meid":"...","cmd":{...}})
xApp (AiTcpClient::configListenerLoop)
    ↓ (Detects "type":"control", extracts meid and cmd JSON)
XappMsgHandler::send_control(cmd_json, meid)
    ↓ (Calls send_ctrl_ callback)
Xapp::send_control_text(text, meid)
    ↓ (Calls send_ric_control_request)
E2 Termination (Docker)
    ↓ (E2AP RIC-CONTROL-REQUEST over SCTP, function_id=300)
NS3 (MmWaveEnbNetDevice::ControlMessageReceivedCallback)
    ↓ (Creates RicControlMessage from E2AP PDU)
RicControlMessage::ApplySimpleCommand()
    ↓ (Parses JSON: {"cmd":"set-mcs","node":0,"mcs":15}, schedules on simulator thread)
MmWaveFlexTtiMacScheduler::DoSchedSetMcs(15)
    ↓ (Sets fixed MCS mode)
Simulation continues with fixed MCS=15
```

**Key Implementation Details:**
- The `cmd` field in the control message can be either a **string** (JSON string) or an **object** (JSON object)
- The xApp extracts the `cmd` field and sends it as-is to NS3 via `send_ric_control_request()`
- NS3's `RicControlMessage::ApplySimpleCommand()` parses the JSON and executes the command
- Commands are executed on the NS3 simulator thread via `Simulator::ScheduleNow()`


## 🎯 Summary

**Implemented Commands (4):**
- ✅ `move-enb` - Move base station
- ✅ `stop` - Stop simulation
- ✅ `set-mcs` - Set MCS (0-28, or <0 to disable fixed MCS)
- ✅ `set-bandwidth` - Set bandwidth in RBs

All commands are sent via the **direct RIC control path** - no CSV files are used.

---

## 📝 Notes

1. **MCS Control**: The `set-mcs` command sets both DL and UL MCS to the same value. To set them separately, you would need to modify the scheduler or add separate commands.

2. **Fixed vs Adaptive MCS**: 
   - When `set-mcs` is called with a value 0-28, it enables **fixed MCS mode**
   - When called with a negative value (e.g., -1), it disables fixed MCS and returns to **adaptive MCS (AMC)**
   - The current MCS can be queried via `GetCurrentMcsDl()` and `GetCurrentMcsUl()` (returns 255 if adaptive)

3. **E2 Termination**: The E2 termination can crash if KPM messages are too large. That's why `reducedPmValues` is set to `true` by default.

