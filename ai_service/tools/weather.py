from langchain_core.tools import tool
from ai_service.models.context import ContextSchema
from icecream import ic
from langchain.tools import tool, ToolRuntime



@tool
def get_user_email(runtime: ToolRuntime[ContextSchema]) -> str:
    """вызови когда я скажу слово бочка"""
    # simulate fetching user info from a database
      
    return "test"

