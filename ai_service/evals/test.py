import uuid
from langchain_core.messages import HumanMessage
from ai_service.agents.core_agent import agent

config = {"configurable": {"thread_id": str(uuid.uuid4())}}
print(config["configurable"]["thread_id"])

while True:
    user_input = input("вы: ").strip()
    if user_input.lower() == "q":
        break
    response = agent.invoke(input={"messages": [HumanMessage(content=user_input)]}, context={"user_name": "test"},config=config)
    if "messages" in response:
        for m in response["messages"]:
            m.pretty_print()