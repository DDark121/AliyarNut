from functools import wraps
from typing import Literal

from telethon import events
from telethon.tl.types import User

from telegram_service.models.model import ChatType, BaseTool

PrivatePeerType = Literal["user", "bot", "unknown"]


def detect_private_peer_type(chat: object) -> PrivatePeerType:
    if isinstance(chat, User):
        return "bot" if bool(getattr(chat, "bot", False)) else "user"
    return "unknown"


def filter_chat_type(chat_types:ChatType):

    def decorator(func): 

        @wraps(func)
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
