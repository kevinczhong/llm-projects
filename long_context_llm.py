from basic_llm import LLM

with open("data/waterloo.md", "r", encoding = "utf-8") as file:
    documentation = file.read()

assistant_message = "I'm an assistant designed to help you with questions about the Waterloo documentation. Please ask your question below." # new intro message
user_input = input(f'\nAssistant: {assistant_message}\n\nUser: ')

history = [
    {"role": "assistant", "content": assistant_message},
    {"role": "user", "content": user_input},
]

while user_input != "exit":
    response = LLM().generate(
        messages_prompt = history,
        system_prompt = f'You are a historical expert on the Napoleonic Wars. You are to answer user queries solely on the following documentation: {documentation}'
    )

    llm_response_text = f'\nAssistant: {response["text"]}'
    print(llm_response_text)

    user_input = input("\nUser: ")
    history += [
        {"role": "assistant", "content": response["text"]},
        {"role": "user", "content": user_input}
    ]