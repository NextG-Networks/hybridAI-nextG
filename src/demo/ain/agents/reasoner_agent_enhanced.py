"""
Enhanced Reasoner Agent

Subscribes to deviation events and creates intents based on detected deviations.
"""

import asyncio
from typing import Dict, Any, Optional
from .utils import make_msg
from ain.brain.llm_reasoner import (
    normalize_deviation, 
    to_proposer_meta, 
    to_rl_intent,
    create_network_intent_from_deviation
)
from ain.brain.openai_client import reason_from_deviation, OpenAIError, fallback_intent_for_deviation
from ain.loop.observer_rl import Intent


class EnhancedReasonerAgent:
    """Reasoner that creates intents from deviation events and SLO intents.
    
    Matches system design: 
    - Receives deviations from Minirocket
    - Receives SLO Intents
    - Consults Knowledge Base
    - Publishes intents to Proposer
    """
    
    def __init__(self, bus, knowledge_base=None, use_llm: bool = False):
        """
        Args:
            bus: MemBus instance
            knowledge_base: Knowledge base instance (e.g., CacheLibrary) for storing/retrieving knowledge
            use_llm: Whether to use LLM for intent reasoning (default: False, uses fallback)
        """
        self.bus = bus
        self.knowledge_base = knowledge_base
        self.use_llm = use_llm
        self.current_intent: Optional[Dict[str, Any]] = None
        self.active_intent_id: Optional[str] = None
        self.slo_intents: Dict[str, Dict[str, Any]] = {}  # Store SLO intents by metric
        
    async def run(self):
        """Subscribe to deviation events and SLO intents, create intents."""
        q_deviation = await self.bus.sub("deviation.detected")
        q_slo_intent = await self.bus.sub("slo.intent")  # SLO intents from external source
        q_intent_complete = await self.bus.sub("intent.completed")  # Optional: listen for intent completion
        
        # Listen for SLO intents in background
        asyncio.create_task(self._listen_slo_intents(q_slo_intent))
        
        while True:
            msg = await q_deviation.get()
            deviation = msg.payload
            
            # Get SLO target for this metric if available
            metric = deviation.get("metric")
            if metric in self.slo_intents:
                slo = self.slo_intents[metric]
                deviation["target"] = slo.get("target")
                deviation["slo_id"] = slo.get("slo_id")
            
            # Check if we should create a new intent
            # Skip if we already have an active intent for this metric
            if self.current_intent and self.current_intent.get("metric") == metric:
                # Check if deviation is worse than current target
                current_target = self.current_intent.get("target")
                deviation_value = deviation.get("value")
                direction = deviation.get("direction", "lower_better")
                
                # Only update if deviation is significantly worse
                if direction == "lower_better":
                    if deviation_value <= current_target * 1.1:  # Within 10% of target
                        continue  # Skip, current intent is still valid
                else:
                    if deviation_value >= current_target * 0.9:  # Within 10% of target
                        continue  # Skip, current intent is still valid
            
            # Consult knowledge base if available
            if self.knowledge_base:
                # Could retrieve similar past intents, successful playbooks, etc.
                pass
            
            # Create intent from deviation
            await self._create_intent_from_deviation(deviation)
    
    async def _listen_slo_intents(self, q):
        """Listen for SLO intents and store them."""
        while True:
            msg = await q.get()
            slo = msg.payload
            metric = slo.get("metric")
            if metric:
                self.slo_intents[metric] = slo
                print(f"[Reasoner] Received SLO intent: {metric} target={slo.get('target')}")
    
    async def _create_intent_from_deviation(self, deviation: Dict[str, Any]):
        """Create and publish intent from deviation event."""
        try:
            # Normalize deviation
            dev_norm = normalize_deviation(deviation)
            
            # Create network intent
            net_intent = create_network_intent_from_deviation(dev_norm, use_llm=self.use_llm)
            
            # Convert to proposer meta and RL intent
            intent_meta = to_proposer_meta(net_intent)
            rl_cfg = to_rl_intent(net_intent)
            
            # Create RL Intent object
            rl_intent = Intent(
                type=rl_cfg["type"],
                metric=rl_cfg["metric"],
                target=float(rl_cfg["target"]),
                direction=rl_cfg["direction"],
                action_cost=float(rl_cfg.get("action_cost", 0.01)),
                reward_clip=float(rl_cfg.get("reward_clip", 2.0)),
            )
            
            # Store current intent
            self.current_intent = {
                "intent_id": net_intent.get("intent_id"),
                "metric": rl_intent.metric,
                "target": rl_intent.target,
                "direction": rl_intent.direction,
                "type": rl_intent.type,
            }
            self.active_intent_id = net_intent.get("intent_id")
            
            # Publish intent to membus
            await self.bus.pub("intent.current", make_msg(
                "intent.current", "INTENT", "intent.v1", self.current_intent
            ))
            
            # Also publish RL intent format for observer
            await self.bus.pub("intent.rl", make_msg(
                "intent.rl", "RL_INTENT", "rl_intent.v1", {
                    "type": rl_intent.type,
                    "metric": rl_intent.metric,
                    "target": rl_intent.target,
                    "direction": rl_intent.direction,
                    "action_cost": rl_intent.action_cost,
                    "reward_clip": rl_intent.reward_clip,
                }
            ))
            
            print(f"[Reasoner] Created intent: {rl_intent.type} for {rl_intent.metric} (target={rl_intent.target}, scope={intent_meta.get('scope')})")
            
        except Exception as e:
            print(f"[Reasoner] Error creating intent from deviation: {e}")
            import traceback
            traceback.print_exc()

