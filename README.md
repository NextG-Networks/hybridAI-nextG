# hybridAI-nextG
HybridAI-NextG explores Hybrid AI for 6G networks by combining symbolic reasoning with neural models to enhance automation in tasks like anomaly detection, forecasting, and self-healing. The project prototypes functional blocks, evaluates performance, and compares Hybrid AI with ML-only approaches.

## Getting Started

### Prerequisites
- [Python 3.11 or 3.12](https://www.python.org/downloads/)
- [Poetry](https://python-poetry.org/docs/#installation)

### Setup
Clone the repository and install dependencies:

```bash
git clone https://github.com/wilhelmrauston/hybridAI-nextG.git
cd hybridAI-nextG
poetry install
```

This will create a .venv/ inside the project folder with all dependencies.

## Development Workflow

# Code Formatting
We use Black for uniform code style (like Prettier for Python).

Format all code before committing:

```
poetry run black src tests

```

## Usage

# 1. Train MiniRocket

Train the MiniRocket model on the demo dataset (ItalyPowerDemand by default):

```
poetry run train_minirocket
```

This saves the model to models/minirocket.joblib.

# 2. Run the Hybrid AI Demo Loop

Launch the reasoning + agent loop with a synthetic KPI stream:

```
poetry run run_loop
```

You should see logs like:

```
Observer: deviation at t=1421 (v=9.86)
Reasoning: created intent ...
Proposer: emitted 2 candidate plans
Predictor: ... → 9.08 ms
Actor: executed ... → 8.73 ms
Assurance: latency_ms=8.73 (ok=True) stable=5/5
Reasoning: removed intent ... (fulfilled)

```


## Project Structure

```
src/ain/
  agents/           # Observer, Predictor, Proposer, Actor
  brain/            # Reasoning layer
  bus/              # Async message bus (pub/sub)
  data/             # Data
  features/         # MiniRocket runtime
  intent/           # Schemas for intents, plans, reports
  pipeline/         # Entry points (train_minirocket, run_loop)
```