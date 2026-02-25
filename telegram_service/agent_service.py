from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from icecream import ic
from telethon.tl.types import (
    Channel,
    Chat,
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    User,
)

from telegram_service.utils.thread_id import normalize_thread_id


def _get_entity_dict(entity) -> dict:
    """Безопасно извлекает peer_id и access_hash для State."""
    peer_id = None
    access_hash = None
    user_name = None
    
    if isinstance(entity, User):
        peer_id = entity.id
        access_hash = getattr(entity, "access_hash", None)
        username = str(getattr(entity, "username", "") or "").strip()
        if username:
            user_name = f"@{username}"
        else:
            first = str(getattr(entity, "first_name", "") or "").strip()
            last = str(getattr(entity, "last_name", "") or "").strip()
            full = " ".join(part for part in (first, last) if part)
            user_name = full or None
    elif isinstance(entity, (Chat, Channel)):
        peer_id = entity.id
        access_hash = getattr(entity, "access_hash", None)
        title = str(getattr(entity, "title", "") or "").strip()
        user_name = title or None
    elif isinstance(entity, InputPeerUser):
        peer_id = entity.user_id
        access_hash = getattr(entity, "access_hash", None)
    elif isinstance(entity, InputPeerChannel):
        peer_id = entity.channel_id
        access_hash = getattr(entity, "access_hash", None)
    elif isinstance(entity, InputPeerChat):
        peer_id = entity.chat_id
    else:
        ic(f"Не удалось извлечь ID/Hash из entity: {type(entity)}")
        
    return {"peer_id": peer_id, "access_hash": access_hash, "user_name": user_name}


class AgentService:
    def __init__(
        self,
        base_url = "http://agent:8000",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()


    async def ask(
        self,
        message: str,
        thread_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_thread_id = normalize_thread_id(thread_id)
        payload = {
            "message": message,
            "thread_id": normalized_thread_id,
            "context": context or {},
        }
        return await self._post("/answer", payload)



    async def get_photo_description(
        self,
        base64_string: str,       # 👈 Принимаем строку
        thread_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_thread_id = normalize_thread_id(thread_id)
        # Формируем Payload под модель MediaRequest
        payload = {
            "file_base64": base64_string,  # 👈 Ключ должен совпадать с Pydantic моделью
            "thread_id": normalized_thread_id,
            "context": context or {},
        }
        # Шлем на правильный эндпоинт
        return await self._post("/photo_description", payload)

    async def stt(
        self,
        base64_string: str,       # 👈 Принимаем строку
        thread_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_thread_id = normalize_thread_id(thread_id)
        payload = {
            "file_base64": base64_string, # 👈 Ключ должен совпадать с Pydantic моделью
            "thread_id": normalized_thread_id,
            "context": context or {},
        }
        return await self._post("/stt_description", payload)

    async def delete_thread(self, thread_id: str) -> bool:
        """Отправляет запрос на удаление истории"""
        normalized_thread_id = normalize_thread_id(thread_id)
        payload = {
            "message": "delete", # Поле обязательно в модели, шлем заглушку
            "thread_id": normalized_thread_id,
            "context": {},
        }
        # Ожидаем, что вернется boolean (true/false)
        return await self._post("/delete_thread", payload)

agent_service = AgentService()
