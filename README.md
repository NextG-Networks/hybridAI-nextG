brain/llm_reasoner.py
  
Sub to deviation, passes deviation to client helper,  receives a structured intent {category, goal, scope, contraints}, then publishes the intent.  
  

brain/openai_client.py  
  
This is a wrapper where we get the response from chatgpt and enfore json struct and the prompt we use.  