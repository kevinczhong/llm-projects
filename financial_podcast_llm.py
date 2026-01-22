import os
import json
from urllib import response
from dotenv import load_dotenv
from datetime import date
# from openai import OpenAI
from langfuse.openai import openai
from langfuse import observe, get_client
from podcast_tools import generate_image, write_to_file, create_audio, TOOLS

load_dotenv()
# llm = OpenAI()
llm = openai
langfuse = get_client()

MAIN_TOOLS = [
  {
    "type": "function",
    "name": "podcast_production",
    "description": "Creates a script from retrieved search results, and creates an audio file of the script via TTS.",
      "parameters": {
        "type": "object",
        "properties": {
          "summary": {
            "type": "string",
            "description": "Summary of the podcast topic",
          }
        },
        "required": ["summary"],
      },
  },
]

@observe
def llm_response(history):
  response = llm.responses.create(
    model="gpt-4.1-mini",
    input=history,
    tools=TOOLS
  )
  return response

@observe
def agent_loop(history):
  while True:
    response = llm_response(history)
    history += response.output

    tool_calls = [obj for obj in response.output if getattr(obj, "type", None) == "function_call"]
    text_messages = [obj for obj in response.output if getattr(obj, "type", None) == "message"]

    if not tool_calls:
      break

    if text_messages:
      print(f"\nAssistant: {response.output_text}")

    for tool_call in tool_calls:
      function_name = tool_call.name
      args = json.loads(tool_call.arguments)

      if function_name == "write_to_file":
        result = {"write_to_file": write_to_file(**args)}
      elif function_name == "create_audio":
        result = {"create_audio": create_audio(**args)}
      # elif function_name == "generate_image":
      #   result = {"generate_image": generate_image(**args)}

      history += [{"type": "function_call_output", "call_id": tool_call.call_id, "output": json.dumps(result)}]

  return response

def system_prompt():
  return f"""You are a podcaster writer/researcher, announcer, and visual artist for financial podcast producers.
  The podcaster producer will provide a topic, and you will, step-by-step, create a podcast episode on the topic. 
  The podcast name is Financial Daily. Your namespace for all files generated should follow the format of:

  Financial_Daily_{date.today().strftime("%B %d, %Y")}

  Here are the steps you will take:
  1. Run the user-provided topic through your web_search tool and retrieve a summary of the latest news on the topic.
  2. Turn the summary into a script for a podcast episode and use your write_to_file tool to save the script as a .txt file.
  3. Use your create_audio tool to generate an audio file from the script, in the user-specified style if provided.
  4. Generate an image of a financial line graph using the generate_image tool to serve as the podcast image.
  """

assistant_message = "I'm here to help you create a financial podcast episode. Please provide a topic, and your preferred style."
user_input = input(f"\nAssistant: {assistant_message}\n\nUser: ")

history = [
    {"role": "developer", "content": system_prompt()},
    {"role": "assistant", "content": assistant_message},
    {"role": "user", "content": user_input}
]

with langfuse.start_as_current_observation(as_type = "span", name = "podcast-conversation") as span:
  while user_input != "exit":
    response = agent_loop(history)
              
    print(f"\nAssistant: {response.output_text}")

    user_input = input("\nUser: ")
    history += [
      {"role": "assistant", "content": response.output_text},
      {"role": "user", "content": user_input}
    ]
  
  span.update(output = "Conversation complete.")

langfuse.flush()