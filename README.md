Demo walkthrough:  
  
1. Run fake_kpi to create a fake kpi stream.  
  
2. loop_observer takes this stream and turns it into a tensor representing the state, the observer also looks at the intent (metric, target, direction) in order to create hit/streak/reward. The fake intent is created in a fallback reasoner at the moment but will be replaced with a GPT API call.  
  
3. proposer then creates playbooks, this is done purely random at the moment and will later use memory to make books.  

4. predictor now takes the state tensor -> vector with GRU, takes the playbook -> dense vector with MLP, these vector are then combined to create a Q score (right now no learning only stubs for stable/unstable models).  
  
5. The highest Q value is printed out A1/A2/A3.  
  
6. This playbook is then sent to the acutor that makes it into a standardized JSON structure.  

7. Back to loop_observer reads KPI to state tensor and recalculate rewards.  

Notes: No learning, No membus communication (right now just calls from demo_cli), proposer needs to be fixed so it not just random.  
Checklist: Fix proposer  
           Fix predictor learning 
           Fix real reasoner intents (might be out of scope for this demo, we will see if i have time)