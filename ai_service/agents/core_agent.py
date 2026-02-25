
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from icecream import ic
from ai_service.models.context import ContextSchema
from ai_service.config.settings import settings
from ai_service.tools import ALL_TOOLS
from ai_service.middleware import ALL_MIDDLEWARE
from ai_service.memory.database import get_checkpointer 

main_model = ChatOpenAI(
    model=settings.model.name,
    api_key=settings.openrouter_api_key, 
    base_url=settings.model.base_url,
    temperature=settings.model.temperature
)

backup_model = ChatOpenAI(
    model=settings.backup_model.name,
    api_key=settings.openai_api_key, 
    temperature=settings.backup_model.temperature
)

model_with_fallback = main_model.with_fallbacks([backup_model])

ic(f"AGENT TOOLS: {[getattr(t, 'name', getattr(t, '__name__', str(t))) for t in ALL_TOOLS]}")

agent = create_agent(
    model=model_with_fallback,  
    tools=ALL_TOOLS,
    system_prompt=settings.prompts.content,
    checkpointer=get_checkpointer(),
    middleware=ALL_MIDDLEWARE,
    context_schema=ContextSchema
)







