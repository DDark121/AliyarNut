from functools import wraps
from telethon import events
from telegram_service.models.model import ChatType, BaseTool


def filter_chat_type(chat_types:ChatType):

    def decorator(func): 

        async def wrapper(event: events.NewMessage.Event):
            if event.is_private:
                chat_type = "private"
            elif event.is_group:
                chat_type = "group"
            elif event.is_channel:
                chat_type = "channel"
            else:
                chat_type = "unknown"

            # Проверяем, разрешён ли этот тип
            if chat_type in chat_types:
                await func(event)  # Вызов оригинальной функции
        return wrapper
    
    return decorator


def validate_pydantic(schema):
    """Декоратор, который проверяет, есть ли аргументы, перед тем как валидировать."""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, **kwargs):
            if schema == BaseTool.EmptyArgs:
                return await func(self)

         
            data = schema(**kwargs)  # 🔥 Преобразуем dict в Pydantic-объект
            return await func(self, data)  

        return wrapper
    return decorator
