# Log Level Usage Guide

## Overview

The logging system now supports category-based filtering to minimize clutter and focus on specific aspects of the system.

## Log Categories

1. **LEARNING** - Model training, loss, Q-values, replay buffer
2. **INTENT** - Intent creation and updates
3. **SCORING/PLAYBOOK** - Q-value scoring, playbook generation
4. **DEVIATION/COMMANDS** - Deviation detection, command execution
5. **OBSERVER** - State window generation, feature completeness
6. **REWARD** - Reward computation details
7. **KPI** - Low-level KPI I/O (message reception, node_id extraction)

## Usage

### In deploy_ai.sh

Set the `LOG_LEVEL` environment variable before running:

```bash
# Show only learning progress
export LOG_LEVEL="1"
./deploy_ai.sh

# Show learning and intent updates
export LOG_LEVEL="1,2"
./deploy_ai.sh

# Show scoring and playbook creation
export LOG_LEVEL="3"
./deploy_ai.sh

# Show deviation detection and commands
export LOG_LEVEL="4"
./deploy_ai.sh

# Show everything (default)
export LOG_LEVEL="all"
./deploy_ai.sh

# Show everything except KPI logs (recommended for cleaner output)
export LOG_LEVEL="1,2,3,4,5,6"
./deploy_ai.sh

# Multiple categories
export LOG_LEVEL="1,3,4"
./deploy_ai.sh

# Show only KPI logs (for debugging KPI reception)
export LOG_LEVEL="7"
./deploy_ai.sh
```

### Direct Python Execution

```bash
python src/demo/ain/RL_demo/xapp_demo_membus.py --log-level "1,2,3"
```

## Examples

### Focus on Learning Only
```bash
export LOG_LEVEL="1"
./deploy_ai.sh
```
**Output:**
- `[LEARNING] Step=X, loss=Y, Q_mean=Z, ...`
- `[LEARNING] Loaded checkpoint: ...`
- `[LEARNING] NaN/inf loss detected, skipping update`

### Focus on Intent and Scoring
```bash
export LOG_LEVEL="2,3"
./deploy_ai.sh
```
**Output:**
- `[INTENT] Published initial intent: ...`
- `[INTENT] Observer intent updated: ...`
- `[SCORING] Best playbook selected: Q=...`
- `[PLAYBOOK] Generated 5 candidate playbooks ...`

### Focus on Deviation Detection and Commands
```bash
export LOG_LEVEL="4"
./deploy_ai.sh
```
**Output:**
- `[DEVIATION] ML model detected deviation: ...`
- `[DEVIATION] Deviation detected: ... (debounced)`
- `[COMMANDS] Sent command to ...`
- `[COMMANDS] Sending command 1/2: ...`

### Minimal Output (Learning + Commands)
```bash
export LOG_LEVEL="1,4"
./deploy_ai.sh
```
**Output:**
- Learning progress every 10 steps
- Deviation detections
- Commands sent
- No intent, scoring, observer, reward, or KPI details

### Clean Output (All except KPI logs)
```bash
export LOG_LEVEL="1,2,3,4,5,6"
./deploy_ai.sh
```
**Output:**
- All categories except noisy KPI I/O logs
- Recommended for normal operation

## Category Details

### 1. LEARNING
- Model training steps
- Loss values
- Q-value statistics
- Replay buffer size
- Gradient norms
- Checkpoint loading

### 2. INTENT
- Initial SLO intent creation
- Intent updates from reasoner
- Observer intent updates

### 3. SCORING/PLAYBOOK
- Playbook generation
- Q-value scoring statistics
- Best playbook selection
- Action distribution

### 4. DEVIATION/COMMANDS
- MiniRocket deviation detection
- Threshold-based deviation detection
- Deviation debouncing
- Command sending
- Command execution status

### 5. OBSERVER
- State window generation
- Feature completeness
- KPI accumulation
- UE metric aggregation

### 6. REWARD
- Reward computation details
- Metric values (prev/curr)
- Reward components
- Enhanced reward with context bonus

### 7. KPI
- KPI message reception
- Node ID extraction
- UE node ID extraction
- Cell ID mapping
- KPI message structure debugging

## Tips

1. **Start with all logs** (`LOG_LEVEL="all"`) to understand system behavior
2. **Focus on specific issues** by enabling only relevant categories
3. **Combine categories** for targeted debugging (e.g., `"1,4"` for learning + commands)
4. **Use category 5 (OBSERVER)** to debug feature completeness issues
5. **Use category 6 (REWARD)** to understand reward computation

## Default Behavior

If `LOG_LEVEL` is not set, all categories are enabled (`LOG_LEVEL="all"`).

