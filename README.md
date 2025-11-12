Demo v1.2  
Demo walkthrough:  
  
1. Run fake_kpi to create a fake kpi stream.  
  
2. loop_observer takes this stream and turns it into a tensor representing the state, the observer also looks at the intent (metric, target, direction) in order to create hit/streak/reward. The fake intent is created in a fallback reasoner at the moment but will be replaced with a GPT API call.  
  
3. proposer then creates playbooks, this is done purely random at the moment and will later use memory to make books.  

4. predictor now takes the state tensor -> vector with GRU, takes the playbook -> dense vector with MLP, these vector are then combined to create a Q score (right now no learning only stubs for stable/unstable models).  
  
5. The highest Q value is printed out A1/A2/A3.  
  
6. This playbook is then sent to the acutor that makes it into a standardized JSON structure.  

7. Back to loop_observer reads KPI to state tensor and recalculate rewards.  

Notes: No learning, No membus communication (right now just calls from demo_cli), proposer needs to be fixed so it not just random.  
Checklist:   
           Fix predictor learning  
           Fix real reasoner intents (might be out of scope for this demo, we will see if i have time)  
           Fix membus instead of calls from demo file  
  
Demo v2.0  
Fixed predictor learning:  
We now how a online and offline training for the predictor, offline training is now us just randomly generating (state, action, reward, next_state) into a replay buffer that we then use to train a Q-network. So we get vectorization of state and actions and using the fake generated replay buffer it learns how to predict Q values. For the online training it works in the same way where we save the new scenarios and keep fine tuning the Q network to make better predictios based on what happend.   
  
This leads to playbooks being the same all the time which makes sense since we use the same seed for initial weigths we get the same predicted Q value each time, our proposer also isnt random enough to actaully force a drastic change but it should be fine to have it like this for a real network rather than a fakes scenario. Running with the pretrained weights also changes the outcome which is a good sign of learning (even though its faked now) actaully working. 