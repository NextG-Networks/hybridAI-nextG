#!/bin/bash
# Deploy script for AI system
# Usage: ./deploy_ai.sh [options]

set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Default values
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-6000}"
TARGET_METRIC="${TARGET_METRIC:-DRB_PdcpSduDelayDl}"
TARGET_VALUE="${TARGET_VALUE:-40.0}"
CELLS="${CELLS:-CELL_001}"
SLICES="${SLICES:-SLICE_A}"
STEPS="${STEPS:-1000}"
LOG_LEVEL="${LOG_LEVEL:-8,2}"
# Log level categories: 1=LEARNING, 2=INTENT, 3=SCORING/PLAYBOOK, 4=DEVIATION, 5=OBSERVER, 6=REWARD, 7=KPI, 8=COMMANDS
# Use comma-separated numbers (e.g., "1,2,3") or "all" for everything
# Default: "4,8,2" = DEVIATION + COMMANDS + INTENT
# Set LOG_LEVEL="1,2,3,4,5,6" to disable KPI logs (category 7) but enable all others
# Set LOG_LEVEL="6" to see only reward logs
# Set LOG_LEVEL="all" to see everything

# Loss plotting options
SAVE_LOSS_HISTORY="${SAVE_LOSS_HISTORY:-true}"
PLOT_LOSS="${PLOT_LOSS:-false}"
STABLE_LOSS_FILE="${STABLE_LOSS_FILE:-models/loss_history_stable.json}"
UNSTABLE_LOSS_FILE="${UNSTABLE_LOSS_FILE:-models/loss_history_unstable.json}"
LOSS_PLOT_OUTPUT="${LOSS_PLOT_OUTPUT:-loss_plot.png}"

# Note: To compare stable vs unstable models:
# 1. Run stable model (default, uses target network with TAU=0.005):
#    ./deploy_ai.sh -- loss history saved to models/loss_history.json
#    cp models/loss_history.json models/loss_history_stable.json
# 2. Run unstable model (modify TAU in predictor.py to 1.0 or disable target network):
#    ./deploy_ai.sh -- loss history saved to models/loss_history.json
#    cp models/loss_history.json models/loss_history_unstable.json
# 3. Plot both:
#    PLOT_LOSS=true STABLE_LOSS_FILE=models/loss_history_stable.json UNSTABLE_LOSS_FILE=models/loss_history_unstable.json ./deploy_ai.sh
#    OR: python3 plot_loss.py --stable models/loss_history_stable.json --unstable models/loss_history_unstable.json

# Check if poetry is installed
if ! command -v poetry &> /dev/null; then
    echo "Error: Poetry is not installed. Please install Poetry first."
    exit 1
fi

# Check if dependencies are installed
if [ ! -d ".venv" ] && [ ! -f "poetry.lock" ]; then
    echo "Installing dependencies..."
    poetry install
fi

# Build command arguments
ARGS=(
    "--host" "$HOST"
    "--port" "$PORT"
    "--target-metric" "$TARGET_METRIC"
    "--target-value" "$TARGET_VALUE"
    "--cells" $CELLS
    "--slices" $SLICES
    "--steps" "$STEPS"
    "--log-level" "$LOG_LEVEL"
)

# Add optional MiniRocket models if they exist
if [ -f "models/minirocket_xapp_gnb.joblib" ]; then
    ARGS+=("--minirocket-gnb-model" "models/minirocket_xapp_gnb.joblib")
fi

if [ -f "models/minirocket_xapp_ue.joblib" ]; then
    ARGS+=("--minirocket-ue-model" "models/minirocket_xapp_ue.joblib")
fi

# Add offline model if specified
if [ -n "$OFFLINE_MODEL" ] && [ -f "$OFFLINE_MODEL" ]; then
    ARGS+=("--offline-model" "$OFFLINE_MODEL")
fi

# Add use-llm flag if specified
if [ "$USE_LLM" = "true" ]; then
    ARGS+=("--use-llm")
fi

# Add commands-disabled flag if specified
if [ "$COMMANDS_DISABLED" = "true" ]; then
    ARGS+=("--commands-disabled")
fi

# Add model type for loss history filename
MODEL_TYPE="${MODEL_TYPE:-stable}"
ARGS+=("--model-type" "$MODEL_TYPE")

# Add loss history file if specified
if [ -n "$LOSS_HISTORY_FILE" ]; then
    ARGS+=("--loss-history-file" "$LOSS_HISTORY_FILE")
fi

# Add SLO file if specified
SLO_FILE="${SLO_FILE:-configs/slos.json}"
ARGS+=("--slo-file" "$SLO_FILE")

echo "=========================================="
echo "Starting AI System"
echo "=========================================="
echo "Host: $HOST"
echo "Port: $PORT"
echo "Target Metric: $TARGET_METRIC"
echo "Target Value: $TARGET_VALUE"
echo "Cells: $CELLS"
echo "Slices: $SLICES"
echo "Steps: $STEPS"
echo "Log Level: $LOG_LEVEL"
echo "Save Loss History: $SAVE_LOSS_HISTORY"
echo "Plot Loss: $PLOT_LOSS"
echo "=========================================="

# Function to plot loss after training
plot_loss_function() {
    if [ "$PLOT_LOSS" = "true" ]; then
        echo "=========================================="
        echo "Plotting loss function..."
        echo "=========================================="
        
        # Check if we have loss history files
        if [ -f "$STABLE_LOSS_FILE" ] || [ -f "$UNSTABLE_LOSS_FILE" ]; then
            PLOT_ARGS=()
            if [ -f "$STABLE_LOSS_FILE" ]; then
                PLOT_ARGS+=("--stable" "$STABLE_LOSS_FILE")
            fi
            if [ -f "$UNSTABLE_LOSS_FILE" ]; then
                PLOT_ARGS+=("--unstable" "$UNSTABLE_LOSS_FILE")
            fi
            PLOT_ARGS+=("--output" "$LOSS_PLOT_OUTPUT")
            
            poetry run python3 plot_loss.py "${PLOT_ARGS[@]}"
            echo "Loss plot saved to: $LOSS_PLOT_OUTPUT"
        else
            echo "Warning: No loss history files found to plot."
            echo "  Expected: $STABLE_LOSS_FILE or $UNSTABLE_LOSS_FILE"
            echo "  Loss history is saved automatically during training to models/loss_history.json"
        fi
    fi
}

# Set up trap to plot loss on exit
if [ "$PLOT_LOSS" = "true" ]; then
    trap plot_loss_function EXIT
fi

# Activate poetry shell and run the AI
poetry run python3 src/demo/ain/RL_demo/xapp_demo_membus.py "${ARGS[@]}"

# Plot loss after training completes (if not already done by trap)
if [ "$PLOT_LOSS" = "true" ]; then
    plot_loss_function
fi

