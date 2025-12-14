import asyncio
import random
import logging
from .utils import make_msg
from ain.common.log_config import should_log, LOG_INTENT, LOG_SCORING

logger = logging.getLogger(__name__)

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
                    if should_log(LOG_SCORING):
                        logger.debug(f"[PLAYBOOK] Received state window (pending intent: {self.intent is not None})")
                elif msg.topic == "intent.current":
                    self.intent = msg.payload
                    intent_type = self.intent.get('type', 'UNKNOWN')
                    intent_metric = self.intent.get('metric', 'UNKNOWN')
                    intent_target = self.intent.get('target', 'UNKNOWN')
                    if should_log(LOG_INTENT):
                        logger.info(f"[INTENT] Received intent: type={intent_type}, metric={intent_metric}, target={intent_target}")
            
            # Cancel pending tasks
            for t in pending:
                t.cancel()
            
            # Generate playbooks if we have both intent and state
            if self.intent and pending_state:
                if should_log(LOG_SCORING):
                    logger.info(f"[PLAYBOOK] Have both intent and state, generating playbooks...")
                
                try:
                    # Extract situation (if available in state message)
                    # Use .get() on pending_state assuming it's a dict, otherwise default
                    situation = "normal"
                    if isinstance(pending_state, dict):
                         situation = pending_state.get("situation", "normal")
                    
                    # Convert intent to proposer meta format
                    # Map intent type to proposer intent tag
                    intent_type = self.intent.get("type", "REDUCE_LATENCY")
                    # Map to proposer intent tags (LATENCY_P95, THR_DL, etc.)
                    if "LATENCY" in intent_type or "delay" in intent_type.lower():
                        proposer_intent = "LATENCY_P95"
                    elif "THROUGHPUT" in intent_type or "thr" in intent_type.lower():
                        proposer_intent = "THR_DL"
                    else:
                        proposer_intent = "LATENCY_P95"  # Default fallback
                    
                    intent_meta = {
                        "intent": proposer_intent,
                        "scope": self.intent.get("scope", "GLOBAL")
                    }
                    
                    epsilon = 0.3  # can later decay dynamically
                    
                    # Use knowledge base (cache) if available
                    cache = self.knowledge_base if self.knowledge_base else None
                    # Calculate cache size (CacheLibrary doesn't support len())
                    if cache and hasattr(cache, 'store'):
                        cache_size = sum(len(entries) for entries in cache.store.values())
                    else:
                        cache_size = 0
                    
                    from ain.loop.proposer import ProposerSampler, CANDIDATE_N, PLAYBOOK_K
                    playbooks = ProposerSampler.sample_playbooks(
                        self.action_space, 
                        N=CANDIDATE_N,
                        K=PLAYBOOK_K,
                        epsilon=epsilon,
                        intent_meta=intent_meta, 
                        cache=cache,
                        situation=situation
                    )
                    
                    # Log playbook details
                    if should_log(LOG_SCORING):
                        action_types = [action.type for pb in playbooks for action in pb.actions]
                        action_counts = {action_type: action_types.count(action_type) for action_type in set(action_types)}
                        logger.info(f"[PLAYBOOK] Generated {len(playbooks)} candidate playbooks (epsilon={epsilon:.2f}, cache_size={cache_size})")
                        logger.debug(f"[PLAYBOOK] Action distribution: {action_counts}")
                    
                    await self.bus.pub("proposer.candidates", make_msg(
                        "proposer.candidates", "PLAYBOOKS", "playbooks.v1",
                        {"candidates": playbooks}
                    ))
                    if should_log(LOG_SCORING):
                        logger.debug(f"[PLAYBOOK] Published {len(playbooks)} candidate playbooks to proposer.candidates")
                    
                    # Clear pending state after using it
                    pending_state = None
                except Exception as e:
                    logger.error(f"[PLAYBOOK] Error generating playbooks: {e}", exc_info=True)
                    # Don't clear pending_state on error, so we can retry

    async def _listen_intent(self, q):
        while True:
            m = await q.get()
            self.intent = m.payload
            if should_log(LOG_INTENT):
                intent_type = self.intent.get('type', 'UNKNOWN')
                intent_metric = self.intent.get('metric', 'UNKNOWN')
                logger.info(f"[INTENT] Received intent update: type={intent_type}, metric={intent_metric}")
