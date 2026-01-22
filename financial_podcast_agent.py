import os
import json
from urllib import response
from dotenv import load_dotenv
from datetime import date
# from openai import OpenAI
from langfuse.openai import openai
from langfuse import observe, get_client
from podcast_tools import write_to_file, create_audio

load_dotenv()
# llm = OpenAI()
llm = openai
langfuse = get_client()

MAIN_TOOLS = [
  {
    "type": "function",
    "name": "podcast_production",
    "description": """Performs an enhanced web search. Creates a script from retrieved search results, 
    and creates an audio file of the script via TTS.""",
      "parameters": {
        "type": "object",
        "properties": {
          "topic": {
            "type": "string",
            "description": "Podcast topic",
          }
        },
        "required": ["topic"],
      },
  },
]

@observe
def podcast_production(topic):
  enhanced_search = llm.responses.create(
    model = "gpt-4.1",
    temperature = 0.3,
    tools = [
      {
        "type": "web_search",
      }
    ],
    input = f"""Perform a web search on the user provided topic: {topic} and
    return the top five most relevant results. Prioritize results
    from credible financial news sources, such as Bloomberg, the Economist,
    the Financial Times, etc.
    """
  ).output_text

  script_write = llm.responses.create(
    model = "gpt-4.1",
    temperature = 0.1,
    input = f"""You are an award-winning financial journalist, articulately and
    expertly summarizing the information provided to you in {enhanced_search} in 
    the form of a script for your podcaster colleague. Write in the style of the
    Financial Times or the Economist. Only write the dialogue - DO NOT include any
    text that is not meant to be spoken.
    """
  ).output_text

  write_to_file(f'Financial_Daily_{date.today().strftime("%B %d, %Y")}_script.txt', script_write)

  style = """You are an award-winning news announcer and podcaster; given a script,
  you only articulate the conversational components, rather than the script verbatim.
  Your tone is professional, formal, engaging, and authoritative."""

  create_audio(f'Financial_Daily_{date.today().strftime("%B %d, %Y")}.mp3', script_write, style)

  return "Podcast complete!"

@observe
def llm_response(history):
  response = llm.responses.create(
    model="gpt-4.1-mini",
    input=history,
    tools=MAIN_TOOLS
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

      if function_name == "podcast_production":
        result = {"podcast_production": podcast_production(**args)}
      # if function_name == "write_to_file":
      #   result = {"write_to_file": write_to_file(**args)}
      # elif function_name == "create_audio":
      #   result = {"create_audio": create_audio(**args)}
      # # elif function_name == "generate_image":
      # #   result = {"generate_image": generate_image(**args)}

      history += [{"type": "function_call_output", "call_id": tool_call.call_id, "output": json.dumps(result)}]

  return response

def system_prompt():
  return f"""You are a podcaster writer/researcher, announcer, and visual artist for financial podcast producers.
  The podcaster producer will provide a topic, and you will, step-by-step, create a podcast episode on the topic.

  Once you have your topic, use the provided topic and the style, if the user specifies, and 
  use your podcast_production tool to create your podcast episode; podcast_production will handle web search,
  script creation, and audio generation. When using podcast_production, only pass topic and style in as arguments. 
  
  DO NOT PASS IN filename or script when calling podcast_production."""

assistant_message = "I'm here to help you create a financial podcast episode. Please provide a topic."
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