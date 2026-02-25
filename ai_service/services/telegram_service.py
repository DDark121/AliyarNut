import os
from typing import Any, Dict, List, Optional

import httpx
from icecream import ic


class TelegramApiTimeoutError(ConnectionError):
    """Raised when Telegram profile service did not respond in time."""


class TelegramApiService:
    def __init__(
        self,
        base_url: str = "http://telegram_profile:8001", # Хост внутри Docker сети
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        default_timeout = float(os.getenv("TELEGRAM_API_TIMEOUT_SEC", str(timeout)))
        self.timeout = max(2.0, default_timeout)
        send_photos_timeout_raw = os.getenv("TELEGRAM_SEND_PHOTOS_TIMEOUT_SEC", "60")
        try:
            send_photos_timeout = float(send_photos_timeout_raw)
        except (TypeError, ValueError):
            send_photos_timeout = 60.0
        self.send_photos_timeout = max(self.timeout, send_photos_timeout)

    @staticmethod
    def _error_text(exc: Exception) -> str:
        text = str(exc).strip()
        return text or exc.__class__.__name__

    async def _post(self, path: str, payload: Dict[str, Any], *, timeout: Optional[float] = None) -> Any:
        """Базовый метод отправки POST запроса"""
        url = f"{self.base_url}{path}"
        request_timeout = timeout if timeout is not None else self.timeout
        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status() # Вызовет ошибку, если статус не 200
                return response.json()
        except httpx.TimeoutException as e:
            err_text = self._error_text(e)
            ic(f"TelegramApi Timeout Error [{path}]: {err_text}")
            raise TelegramApiTimeoutError(f"Таймаут Telegram API ({path}): {err_text}") from e
        except httpx.RequestError as e:
            err_text = self._error_text(e)
            ic(f"TelegramApi Network Error [{path}]: {err_text}")
            raise ConnectionError(f"Сервис Telegram недоступен: {err_text}") from e
        except httpx.HTTPStatusError as e:
            ic(f"TelegramApi Status Error: {e.response.text}")
            raise ValueError(f"Ошибка от Telegram API: {e.response.status_code}")

    async def send_reaction(
        self,
        user_id: int,
        access_hash: int,
        message_id: int,
        emotion: str
    ) -> Dict[str, Any]:
        """
        Отправляет запрос на установку реакции.
        """
        payload = {
            "user_id": user_id,
            "access_hash": access_hash,
            "message_id": message_id,
            "emotion": emotion
        }
        
        ic(f"TelegramApi: Отправка реакции {emotion} для {user_id}")
        return await self._post("/send-reaction", payload)
    async def send_office_location(
        self,
        user_id: int,
        access_hash: int,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Отправляет запрос на отправку гео-позиции офиса.
        """
        payload = {
            "user_id": int(user_id),
            "access_hash": int(access_hash),
            "latitude": latitude,
            "longitude": longitude,
            "caption": caption,
        }
        
        ic(f"TelegramApi: Запрос на отправку гео для {user_id}")
        return await self._post("/send-office-location", payload)

    async def send_photos(
        self,
        user_id: int,
        access_hash: int,
        photos: List[Dict[str, str]],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Отправляет набор фото пользователю Telegram.
        photos: [{"file_name": "...", "content_base64": "..."}]
        """
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "access_hash": access_hash,
            "photos": photos,
            "description": description,
        }
        ic(f"TelegramApi: Отправка {len(photos)} фото для {user_id}")
        return await self._post("/send-photos", payload, timeout=self.send_photos_timeout)

    async def send_message(
        self,
        user_id: int,
        message: str,
    ) -> Dict[str, Any]:
        """
        Отправляет текстовое сообщение пользователю Telegram.
        """
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "message": message,
        }
        ic(f"TelegramApi: Отправка текстового сообщения для {user_id}")
        return await self._post("/send-message", payload)

    async def resolve_user_name(self, user_id: int) -> Optional[str]:
        """
        Возвращает имя аккаунта Telegram (username/full name) по user_id.
        """
        payload: Dict[str, Any] = {"user_id": int(user_id)}
        ic(f"TelegramApi: Запрос имени аккаунта для {user_id}")
        response = await self._post("/resolve-user-name", payload)
        if isinstance(response, dict):
            value = str(response.get("display_name") or "").strip()
            return value or None
        return None

# Создаем глобальный экземпляр (синглтон)
telegram_api = TelegramApiService()
