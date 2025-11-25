import asyncio
from .utils import make_msg

class PredictorAgent:
    def __init__(self, bus, predictor):
        self.bus = bus
        self.model = predictor
        self.state = None
        self.pending_candidates = None  # Store candidates if we get them before state

    async def run(self):
        try:
            print("[Predictor] Initializing predictor agent...")
            q_state = await self.bus.sub("kpi.window")
            q_cands = await self.bus.sub("proposer.candidates")
            
            print("[Predictor] ✓ Subscribed to kpi.window and proposer.candidates")

            # Process messages from both queues independently
            state_task = asyncio.create_task(self._process_state_queue(q_state))
            cands_task = asyncio.create_task(self._process_candidates_queue(q_cands))
            trainer_task = asyncio.create_task(self._online_trainer())
            
            print("[Predictor] ✓ Started all processing tasks (state, candidates, trainer)")
            
            # Keep running and monitor tasks
            while True:
                await asyncio.sleep(1)
                # Check if tasks are still running
                if state_task.done():
                    print("[Predictor] ERROR: State queue task died!")
                    try:
                        state_task.result()  # This will raise the exception
                    except Exception as e:
                        print(f"[Predictor] State task error: {e}")
                        import traceback
                        traceback.print_exc()
                if cands_task.done():
                    print("[Predictor] ERROR: Candidates queue task died!")
                    try:
                        cands_task.result()  # This will raise the exception
                    except Exception as e:
                        print(f"[Predictor] Candidates task error: {e}")
                        import traceback
                        traceback.print_exc()
        except Exception as e:
            print(f"[Predictor] Fatal error in run(): {e}")
            import traceback
            traceback.print_exc()
            raise
    
    async def _process_state_queue(self, q_state):
        """Process state window messages."""
        print("[Predictor] State queue handler started")
        while True:
            try:
                msg = await q_state.get()
                # Handle both Msg objects and direct payloads
                if hasattr(msg, 'payload'):
                    state = msg.payload.get("state") if isinstance(msg.payload, dict) else msg.payload
                else:
                    state = msg.get("state") if isinstance(msg, dict) else msg
                
                self.state = state
                state_shape = len(self.state) if isinstance(self.state, list) else 'unknown'
                print(f"[Predictor] Received state window (shape: {state_shape})")
                
                # Check if we have pending candidates to score
                if self.pending_candidates is not None:
                    print(f"[Predictor] Have pending candidates ({len(self.pending_candidates)}), scoring now...")
                    await self._score_playbooks(self.pending_candidates)
                    self.pending_candidates = None
            except Exception as e:
                print(f"[Predictor] Error processing state queue: {e}")
                import traceback
                traceback.print_exc()
    
    async def _process_candidates_queue(self, q_cands):
        """Process candidate playbook messages."""
        print("[Predictor] Started listening for candidate playbooks...")
        while True:
            try:
                msg = await q_cands.get()
                print(f"[Predictor] Got message from candidates queue: type={type(msg)}, has_payload={hasattr(msg, 'payload')}")
                
                # Handle both Msg objects and direct payloads
                if hasattr(msg, 'payload'):
                    payload = msg.payload
                    print(f"[Predictor] Message has payload: type={type(payload)}")
                    if isinstance(payload, dict):
                        candidates = payload.get("candidates", [])
                    else:
                        candidates = []
                else:
                    if isinstance(msg, dict):
                        candidates = msg.get("candidates", [])
                    else:
                        candidates = []
                
                print(f"[Predictor] Received {len(candidates)} candidate playbooks")
                
                # Check if we have state to score with
                if self.state is not None:
                    await self._score_playbooks(candidates)
                    self.pending_candidates = None
                else:
                    print("[Predictor] Received candidates but no state yet, storing for later...")
                    self.pending_candidates = candidates
            except Exception as e:
                print(f"[Predictor] Error processing candidates queue: {e}")
                import traceback
                traceback.print_exc()
    
    async def _score_playbooks(self, playbooks):
        """Score playbooks with current state."""
        if not playbooks:
            return
        
        print(f"[Predictor] Scoring {len(playbooks)} playbooks...")
        
        # Convert state to numpy array if needed
        import numpy as np
        if isinstance(self.state, list):
            state_array = np.array(self.state)
        else:
            state_array = self.state
        
        try:
            scored = self.model.score_playbooks(state_array, playbooks)
            if scored:
                # Check for NaN values (common with untrained models)
                import math
                has_nan = any(math.isnan(q) or not math.isfinite(q) for _, q in scored)
                
                if has_nan:
                    print(f"[Predictor] Warning: Model returned NaN/infinite Q values (untrained model). Using fallback scoring.")
                    # Fallback: assign random small values for untrained model
                    import random
                    scored = [(pb, random.uniform(-0.1, 0.1)) for pb, _ in scored]
                
                best_q = max(q for _, q in scored)
                print(f"[Predictor] Scored {len(scored)} playbooks, best Q={best_q:.3f}")
                
                await self.bus.pub("predictor.scored", make_msg(
                    "predictor.scored", "SCORED", "scored.v1",
                    {"scored": [(pb, float(q)) for pb, q in scored]}
                ))
            else:
                print("[Predictor] No scored playbooks returned from model")
        except Exception as e:
            print(f"[Predictor] Error scoring playbooks: {e}")
            import traceback
            traceback.print_exc()

    async def _online_trainer(self):
        q = await self.bus.sub("predictor.train.sample")
        while True:
            msg = await q.get()
            loss = self.model.learn_from_sample(msg.payload)
            if loss is not None:
                await self.bus.pub("events.log", make_msg(
                    "events.log", "PREDICTOR_LOSS", "log.v1", {"loss": loss}
                ))
