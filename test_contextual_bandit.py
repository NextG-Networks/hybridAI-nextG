#!/usr/bin/env python3
import sys
import os
sys.path.append('src/demo')

def test_contextual_bandit():
    print("=== Testing Contextual Bandit Components ===\n")
    
    # Test 1: Context Extractor
    print("1. Testing Context Extractor...")
    try:
        from ain.bandit.context_extractor import ContextExtractor, NetworkContext
        extractor = ContextExtractor()
        
        fake_kpi = {
            'CellMetrics': {
                'delay_p95_ms': 75.0,
                'thr_dl_bps': 35000000,
                'bler_dl': 0.045,
                'active_ue_count': 85,
                'cqi_avg': 8.5,
                'mcs_avg': 14
            }
        }
        
        context = extractor.extract_from_kpi(fake_kpi)
        print(f"   ✅ Context extracted: lat={context.latency_ms}ms, thr={context.throughput_dl_mbps}Mbps")
    except Exception as e:
        print(f"   ❌ Context extractor failed: {e}")
        return False
    
    # Test 2: Enhanced Observer
    print("\n2. Testing Enhanced Observer...")
    try:
        from ain.loop.observer_rl import Intent, RLObserver
        
        class MockPredictor:
            def encode_playbook_onehot(self, pb): return [[1,0,0]]
            class MockReplay:
                def push(self, s, p, r, s2, done): pass
            replay = MockReplay()
            def learn_step(self): pass

        intent = Intent('REDUCE_LATENCY', 'delay_p95_ms', 40.0)
        observer = RLObserver(MockPredictor(), intent, enable_contextual_bandit=True)
        print(f"   ✅ Observer created with contextual bandit: {observer.enable_contextual_bandit}")
    except Exception as e:
        print(f"   ❌ Observer failed: {e}")
        return False
    
    # Test 3: Enhanced Proposer
    print("\n3. Testing Enhanced Proposer...")
    try:
        from ain.loop.proposer import ActionSpace, ProposerSampler
        
        action_space = ActionSpace(cells=['CELL_001'], slices=['SLICE_A'])
        playbooks = ProposerSampler.sample_playbooks(action_space, N=2)
        print(f"   ✅ Proposer generated {len(playbooks)} playbooks")
        
        # Test contextual proposer if available
        if hasattr(ProposerSampler, 'sample_contextual_playbooks'):
            contextual_playbooks = ProposerSampler.sample_contextual_playbooks(
                action_space, context=context, situation="high_latency", N=2
            )
            print(f"   ✅ Contextual proposer generated {len(contextual_playbooks)} contextual playbooks")
        
    except Exception as e:
        print(f"   ❌ Proposer failed: {e}")
        return False
    
    print("\n=== All Tests Passed! Contextual Bandit Ready! ===")
    return True

if __name__ == "__main__":
    success = test_contextual_bandit()
    sys.exit(0 if success else 1)
