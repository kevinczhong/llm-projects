from dotenv import load_dotenv
import os
from openai import OpenAI
from pinecone import Pinecone

load_dotenv('.env')
llm = OpenAI()
pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
dense_index = pc.Index("edi-spec")

def search_docs(query):
    results = dense_index.search(
        namespace= "850_PO_implementation",
        query={
            "top_k": 3,
            "inputs": {
                'text': query
            }
        }
    )

    documentation = ""
    for hit in results['result']['hits']:
        fields = hit.get('fields')
        chunk_text = fields.get('text')
        documentation += chunk_text

    return documentation

def system_prompt(documentation):
    return f'You are an expert EDI systems analyst. Your role is to analyze EDI specifications and provide robust, accurate information to internal technical stakeholders. Respond to user queries solely on the following documentation: {documentation}. If the subject of the user query is not covered in the documentation, say "I cannot answer this question based on the provided documentation."'

# Main conversation loop:
assistant_message = "Hello! I'm EDI Spec Assistant. How may I help you today?"
user_input = input(f"\nAssistant: {assistant_message}\n\nUser: ")

history = [
    {"role": "developer", "content": ""},
    {"role": "assistant", "content": assistant_message},
    {"role": "user", "content": user_input}
]

while user_input != "exit":
    documentation = search_docs(user_input)
    history[0] = {"role": "developer", "content": system_prompt(documentation)}

    response = llm.responses.create(
        model="gpt-4.1-mini",
        input=history,
        temperature=0
    )

    llm_response_text = f"\nAssistant: {response.output_text}"
    print(llm_response_text)

    user_input = input("\nUser: ")
    history += [
        {"role": "assistant", "content": response.output_text},
        {"role": "user", "content": user_input}
    ]