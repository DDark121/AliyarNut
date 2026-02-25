from typing import Annotated, Any, Dict
from langchain_core.tools import tool, InjectedToolArg
from langchain_core.runnables import RunnableConfig
from ai_service.models.context import ContextSchema
from icecream import ic
from ai_service.services.telegram_service import telegram_api

REACTION_SEND_EVERY_N_CALLS = 4
_reaction_call_counters: Dict[str, int] = {}


def _get_val(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _reaction_limit_key(config: RunnableConfig, user_id: Any) -> str:
    configurable = (config or {}).get("configurable", {}) or {}
    context = configurable.get("context", {}) or {}
    thread_id = configurable.get("thread_id")
    source = str(_get_val(context, "source") or "").strip().lower()
    source_id = _get_val(context, "source_id")
    peer_id = _get_val(context, "peer_id") or source_id or user_id

    if source:
        return f"{source}:{peer_id}"
    if thread_id:
        return f"thread:{thread_id}"
    return f"user:{peer_id}"


def _should_send_reaction(limit_key: str) -> bool:
    calls = int(_reaction_call_counters.get(limit_key, 0)) + 1
    _reaction_call_counters[limit_key] = calls
    return (calls - 1) % REACTION_SEND_EVERY_N_CALLS == 0


# Предполагаем, что runtime или config вам нужны для доступа к контексту
@tool
async def send_reactions(
    message_id: int, 
    emotion_id: int, 
    # ВАЖНО: Скрываем этот аргумент от LLM и просим LangChain внедрить его
    config: Annotated[RunnableConfig, InjectedToolArg] 
):
    """
       Отправляет реакцию на сообщения человека. 
            Message_id - номер сообщения на которое хочется поставить реакцию,
            emotion_id - номер эмоции которую хочется поставить всего их:
            0 - Вы о чем то договорились или он ущел подумать
            Правила использования. Используй если видишь в этом надобность для проявления эмоций 
            Твоя задача найти эту тонкую грань и использовать реакцию не постояно а когда необходимо и будет уместно. 
            Ты используешь ее не чаще чем 1 раз на 6 сообщений от клиента. Иногда не чаще и 10-20-30 раз если это не уместно.       
    """
    # 1. Логика валидации (относится к инструменту)
    pack = ["🤝"]
    
    if not (0 <= emotion_id < len(pack)):
        return f"Ошибка: Неверный emotion_id {emotion_id}. Доступно: 0, 1, 2."
        
    emotion_char = pack[emotion_id]

    try:
        # 2. Извлечение контекста (относится к инструменту)
        configurable = config.get("configurable", {})
        context = configurable.get("context", {})
        
        user_id = context.get("peer_id") 
        access_hash = context.get("access_hash")
        if not user_id or not access_hash:
            ic("Ошибка Tool: Нет peer_id/access_hash в контексте")
            return "Ошибка: Не могу идентифицировать пользователя (нет контекста)."

        limit_key = _reaction_limit_key(config=config, user_id=user_id)
        if not _should_send_reaction(limit_key):
            ic(f"send_reactions throttled for key={limit_key}")
            return "Реакция успешно отправлена."

        # 3. Делегирование работы сервису (чистый вызов)
        await telegram_api.send_reaction(
            user_id=int(user_id),
            access_hash=int(access_hash),
            message_id=int(message_id),
            emotion=emotion_char
        )

        return "Реакция успешно отправлена."

    except ConnectionError:
        return "Ошибка: Модуль Telegram временно недоступен."
    except ValueError as e:
        return f"Ошибка при выполнении: {e}"
    except Exception as e:
        ic(f"Tool Unexpected Error: {e}")
        return f"Непредвиденная ошибка: {e}"
