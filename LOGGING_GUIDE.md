# Logging Guide for AI System

This guide explains the logging system added to track learning, intent creation, playbook generation, and predictor scoring.

## Log Levels

- **INFO**: Important events (intent creation, playbook selection, learning milestones)
- **DEBUG**: Detailed information (Q-values, rewards, state generation)
- **WARNING**: Issues that don't stop execution (NaN values, missing data)

## Log Prefixes

All logs are prefixed with tags to make filtering easier:

- `[LEARNING]` - Model training and learning progress
- `[SCORING]` - Predictor scoring of playbooks
- `[INTENT]` - Intent creation and updates
- `[PLAYBOOK]` - Playbook generation
- `[REWARD]` - Reward computation
- `[OBSERVER]` - State window generation

## Key Logs to Monitor

### 1. Learning Progress

**What to look for:**
```
[LEARNING] Step=X, loss=Y, Q_mean=Z, target_mean=W, reward_mean=V, grad_norm=U, replay_size=R
```

**Indicators of learning:**
- ✅ Loss decreasing over time
- ✅ Q-values becoming non-zero and varying
- ✅ Replay buffer size increasing
- ✅ Gradient norm stable (not exploding)

**Red flags:**
- ❌ Loss always NaN or very high (>10.0)
- ❌ Q-values always ~0 (untrained model)
- ❌ Replay buffer not growing
- ❌ Gradient norm exploding (>10.0)

### 2. Intent Creation

**What to look for:**
```
[INTENT] Published initial intent: type=REDUCE_LATENCY, metric=DRB_PdcpSduDelayDl, target=40.0
[INTENT] Observer intent updated: type=REDUCE_LATENCY, metric=DRB_PdcpSduDelayDl, target=40.0
```

**Indicators:**
- ✅ Intent created with correct metric name (not `delay_p95_ms`)
- ✅ Intent updates when deviations detected
- ✅ Target values are reasonable

### 3. Playbook Generation

**What to look for:**
```
[PLAYBOOK] Generated 5 candidate playbooks (epsilon=0.30, cache_size=10)
[PLAYBOOK] Action distribution: {'TX_POWER': 2, 'MCS_CAP': 2, 'SCHEDULER_POLICY': 1}
```

**Indicators:**
- ✅ Multiple playbooks generated
- ✅ Diverse action types
- ✅ Cache being used (cache_size > 0)

### 4. Predictor Scoring

**What to look for:**
```
[SCORING] Scored 5 playbooks: Q_mean=0.1234, Q_std=0.0567, Q_range=[-0.001, 0.250]
[SCORING] Best playbook selected: Q=0.2500, Q_mean=0.1234, Q_std=0.0567, actions=3, candidates=5
```

**Indicators of learning:**
- ✅ Q-values varying (not all ~0)
- ✅ Q-values increasing over time for good playbooks
- ✅ Q_std > 0 (model is differentiating between playbooks)

**Red flags:**
- ❌ All Q-values ~0 (model untrained)
- ❌ Q_std = 0 (model not differentiating)
- ❌ Q-values always negative

### 5. Reward Computation

**What to look for:**
```
[REWARD] metric=DRB_PdcpSduDelayDl, prev=45.2, curr=42.1, target=40.0, delta_rel=0.068, violation=1, actions=2, reward=0.048
```

**Indicators:**
- ✅ Rewards positive when metric improves
- ✅ Rewards negative when metric worsens
- ✅ Rewards account for action costs
- ✅ Rewards clipped to reasonable range

## Filtering Logs

### View only learning progress:
```bash
grep "\[LEARNING\]" logs.txt
```

### View intent updates:
```bash
grep "\[INTENT\]" logs.txt
```

### View playbook generation:
```bash
grep "\[PLAYBOOK\]" logs.txt
```

### View scoring details:
```bash
grep "\[SCORING\]" logs.txt
```

### View all DEBUG logs:
```bash
grep "DEBUG" logs.txt
```

## Example: Checking if Model is Learning

1. **Check replay buffer growth:**
   ```bash
   grep "replay_size" logs.txt | tail -20
   ```
   Should see increasing values.

2. **Check loss trend:**
   ```bash
   grep "\[LEARNING\].*loss=" logs.txt | tail -20
   ```
   Loss should generally decrease or stabilize.

3. **Check Q-value diversity:**
   ```bash
   grep "\[SCORING\].*Q_std=" logs.txt | tail -20
   ```
   Q_std should be > 0 (model differentiating between playbooks).

4. **Check reward distribution:**
   ```bash
   grep "\[REWARD\].*reward=" logs.txt | tail -20
   ```
   Rewards should vary (not all 0.0).

## Troubleshooting

### Model not learning:
- Check replay buffer size (should be >= BATCH_SIZE)
- Check for NaN/inf in loss logs
- Verify rewards are being computed correctly
- Check if Q-values are all ~0 (untrained model)

### No playbooks generated:
- Check intent creation logs
- Verify state windows are being published
- Check proposer agent is running

### Q-values always zero:
- Model is untrained (normal at startup)
- Wait for replay buffer to fill
- Check if learning steps are happening

