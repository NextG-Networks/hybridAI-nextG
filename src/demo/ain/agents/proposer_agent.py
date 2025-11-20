import asyncio, random
from .utils import make_msg

class ProposerAgent:
    """Proposer that generates playbooks based on intents.
    
    Matches system design:
    - Receives intents from Reasoner
    - Consults Knowledge Base (cache of successful playbooks)
    - Publishes candidates to Predictor
    """
    def __init__(self, bus, action_space, knowledge_base=None):
        self.bus = bus
        self.action_space = action_space
        self.knowledge_base = knowledge_base  # CacheLibrary or similar
        self.intent = None

    async def run(self):
        q_state = await self.bus.sub("kpi.window")
        q_intent = await self.bus.sub("intent.current")
        asyncio.create_task(self._listen_intent(q_intent))
        
        pending_state = None  # Store state if we get it before intent

        while True:
            # Wait for either state or intent
            done, pending = await asyncio.wait(
                [asyncio.create_task(q_state.get()), asyncio.create_task(q_intent.get())],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for t in done:
                msg = t.result()
                if msg.topic == "kpi.window":
                    pending_state = msg.payload
                    print(f"[Proposer] Received state window (pending intent: {self.intent is not None})")
                elif msg.topic == "intent.current":
                    self.intent = msg.payload
                    print(f"[Proposer] Received intent: {self.intent.get('type')} for {self.intent.get('metric')}")
            
            # Cancel pending tasks
            for t in pending:
                t.cancel()
            
            # Generate playbooks if we have both intent and state
            if self.intent and pending_state:
                print(f"[Proposer] Have both intent and state, generating playbooks...")
                
                # Convert intent to proposer meta format
                intent_meta = {
                    "intent": self.intent.get("type", "LATENCY_P95"),
                    "scope": self.intent.get("scope", "GLOBAL")
                }
                
                epsilon = 0.3  # can later decay dynamically
                
                # Use knowledge base (cache) if available
                cache = self.knowledge_base if self.knowledge_base else None
                
                from ain.loop.proposer import ProposerSampler, CANDIDATE_N, PLAYBOOK_K
                playbooks = ProposerSampler.sample_playbooks(
                    self.action_space, 
                    N=CANDIDATE_N,
                    K=PLAYBOOK_K,
                    epsilon=epsilon,
                    intent_meta=intent_meta, 
                    cache=cache
                )
                
                print(f"[Proposer] Generated {len(playbooks)} candidate playbooks")
                
                await self.bus.pub("proposer.candidates", make_msg(
                    "proposer.candidates", "PLAYBOOKS", "playbooks.v1",
                    {"candidates": playbooks}
                ))
                
                # Clear pending state after using it
                pending_state = None

    async def _listen_intent(self, q):
        while True:
            m = await q.get()
            self.intent = m.payload
            print(f"[Proposer] Received intent: {self.intent.get('type')} for {self.intent.get('metric')}")
