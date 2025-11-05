What more got added (Poetry doesnt wanna colaborate >:|)
pip install httpx 
pip install torch --index-url https://download.pytorch.org/whl/cpu   
  
learner/predictor.py  
  
Tiny GRU mode (RNN) to get a score for the given playbook.  
Input: History and one playbook  
Output: Score  
Improvments: There is no training loop so right now we dont actaully learn anything.  
This falls back on a heurstic ChatGPT made if GRU isnt installed (no clue if its good but not needed)  
  

learner/objective.py  
  
Turns a score into a sortable cost giving us what playbooks were best.  
  
  
Proposer/proposer_learned.py  
  
Builds state, keeps short history, uses mutate to generate playbooks, scores and ranks with learner files and then returns commands for winner.  
Improvments: Use the actual plays and correct output command.  
  
  
brain/llm_reasoner.py
  
Sub to deviation, passes deviation to client helper,  receives a structured intent {category, goal, scope, contraints}, then publishes the intent.  
  

brain/openai_client.py  
  
This is a wrapper where we get the response from chatgpt and enfore json struct and the prompt we use.  