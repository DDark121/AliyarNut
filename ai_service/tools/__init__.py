from icecream import ic

from .send_reaction import send_reactions
from .data_collector import call_me
from .send_photo_pack import send_photo_pack
from .reminders import schedule_reminder, Delreminder

try:
    from .rag_tools import search_docs_knowledge
except Exception as exc:
    ic(f"RAG tools disabled: {exc}")
    search_docs_knowledge = None

# Создаем общий список инструментов
# Если добавишь новые (калькулятор, поиск), просто допиши их сюда
ALL_TOOLS = [
    call_me,
    send_photo_pack,
    send_reactions,
    schedule_reminder,
    Delreminder,
]

if search_docs_knowledge is not None:
    ALL_TOOLS.append(search_docs_knowledge)
