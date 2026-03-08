from __future__ import annotations

import asyncio
import os
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
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_delay_sec: float | None = None,
    ):
        env_base = str(os.getenv("AGENT_BASE_URL", "http://agent:8000") or "http://agent:8000").strip()
        self.base_url = str(base_url or env_base).rstrip("/")

        env_timeout = str(os.getenv("AGENT_HTTP_TIMEOUT_SEC", "30") or "30").strip()
        self.timeout = float(timeout if timeout is not None else env_timeout)

        env_retries = str(os.getenv("AGENT_HTTP_RETRIES", "3") or "3").strip()
        self.max_retries = max(1, int(max_retries if max_retries is not None else env_retries))

        env_delay = str(os.getenv("AGENT_HTTP_RETRY_DELAY_SEC", "0.8") or "0.8").strip()
        self.retry_delay_sec = max(0.1, float(retry_delay_sec if retry_delay_sec is not None else env_delay))

    async def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # 4xx обычно не лечится ретраем, кроме 429.
                if status < 500 and status != 429:
                    raise
                last_error = exc
                ic(f"AgentService HTTP {status} on {path}, attempt {attempt}/{self.max_retries}")
            except httpx.RequestError as exc:
                last_error = exc
                ic(f"AgentService request failed on {path}, attempt {attempt}/{self.max_retries}: {exc}")

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay_sec * attempt)

        if last_error:
            raise last_error
        raise RuntimeError(f"AgentService request failed: {path}")


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
