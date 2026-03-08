import base64
import binascii
import io
import os
from typing import Optional

import httpx
from PIL import Image
from PIL import UnidentifiedImageError
from icecream import ic
from openai import AsyncOpenAI

from ai_service.config.settings import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_GEMINI_TRANSCRIBE_MODEL = "google/gemini-3-flash-preview"
TRANSCRIBE_PROMPT = "Напиши весь текст, который слышишь в аудио. Верни только текст."


class MediaService:
    def __init__(self):
        # Инициализируем клиента один раз при создании сервиса
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.media_debug = os.getenv("AGENT_MEDIA_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.gemini_transcribe_model = (
            os.getenv("GEMINI_TRANSCRIBE_MODEL", DEFAULT_GEMINI_TRANSCRIBE_MODEL).strip()
            or DEFAULT_GEMINI_TRANSCRIBE_MODEL
        )
        self.openrouter_http_referer = (
            os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:8000").strip()
            or "http://localhost:8000"
        )

    def _debug(self, message: str) -> None:
        if self.media_debug:
            ic(message)

    def _extract_text_from_content(self, content: object) -> str:
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, dict):
            text = content.get("text")
            return text.strip() if isinstance(text, str) else ""

        if isinstance(content, list):
            text_parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
            return "\n".join(text_parts).strip()

        return ""

    async def transcribe_audio(self, base64_audio: str) -> str:
        """Принимает base64, возвращает текст транскрипции"""
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY не найден в .env")

        cleaned_audio = self._clean_base64(base64_audio)
        try:
            file_bytes = base64.b64decode(cleaned_audio, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Невалидный base64 аудио: {exc}") from exc

        self._debug(f"[agent-media][audio] decoded_bytes={len(file_bytes)}")

        payload = {
            "model": self.gemini_transcribe_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TRANSCRIBE_PROMPT},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64.b64encode(file_bytes).decode("utf-8"),
                                "format": "ogg",
                            },
                        },
                    ],
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.openrouter_http_referer,
        }

        timeout = httpx.Timeout(120.0, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            response = await http_client.post(OPENROUTER_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            self._debug(f"[agent-media][audio] empty_openrouter_response={data}")
            raise RuntimeError("Ошибка транскрибации: пустой ответ от Gemini")

        content = choices[0].get("message", {}).get("content")
        transcript = self._extract_text_from_content(content)
        self._debug(f"[agent-media][audio] transcript_len={len(transcript)}")
        if transcript:
            return transcript

        raise RuntimeError("Ошибка транскрибации: Gemini вернул пустой текст")

    def _clean_base64(self, payload: str) -> str:
        """Принимает raw base64 или data URL и возвращает чистый base64."""
        original_len = len(payload or "")
        if "," in payload and payload.lstrip().lower().startswith("data:"):
            payload = payload.split(",", 1)[1]
        # Убираем пробелы/переводы строк и выравниваем padding.
        cleaned = "".join(payload.split())
        missing_padding = len(cleaned) % 4
        if missing_padding:
            cleaned += "=" * (4 - missing_padding)
        self._debug(
            f"[agent-media][base64] clean_base64 original_len={original_len} cleaned_len={len(cleaned)}"
        )
        return cleaned

    def _convert_image_to_png_base64(self, base64_image: str) -> Optional[str]:
        """Декодирует изображение и перекодирует в PNG. Возвращает None, если это невалидная картинка."""
        cleaned = self._clean_base64(base64_image)
        try:
            image_bytes = base64.b64decode(cleaned, validate=False)
            self._debug(f"[agent-media][image] decoded_bytes={len(image_bytes)}")
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Невалидный base64 изображения: {exc}") from exc

        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                self._debug(
                    f"[agent-media][image] pil_open format={img.format} mode={img.mode} size={img.size}"
                )
                # OpenAI поддерживает PNG; убираем режимы вроде CMYK/P, чтобы избежать редких ошибок.
                if img.mode not in ("RGB", "RGBA", "L"):
                    img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
                png_bytes = buffer.getvalue()
        except (UnidentifiedImageError, OSError):
            self._debug("[agent-media][image] PIL could not identify image")
            return None

        self._debug(f"[agent-media][image] png_bytes={len(png_bytes)}")
        return base64.b64encode(png_bytes).decode("utf-8")

    async def describe_photo(self, base64_image: str) -> str:
        """Принимает base64, возвращает описание фото"""
        self._debug(f"[agent-media][image] describe_photo input_b64_len={len(base64_image or '')}")
        # 1. Приводим любое входящее изображение к PNG.
        # Если файл не является валидным изображением, возвращаем пустой текст,
        # чтобы не ронять /photo_description при поврежденных или некорректных файлах.
        png_base64 = self._convert_image_to_png_base64(base64_image)
        if not png_base64:
            self._debug("[agent-media][image] describe_photo skip: png_base64 is empty")
            return ""
        image_data_url = f"data:image/png;base64,{png_base64}"

        # 2. GPT Vision
        response = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Внимательно изучи фотку и опиши ее"},
                        {"type": "image_url", "image_url": {"url": image_data_url}}
                    ]
                }
            ],
            max_tokens=300
        )
        message = response.choices[0].message.content
        self._debug(f"[agent-media][image] vision_text_len={len(message or '')}")
        return message

# Создаем единственный экземпляр (Singleton), чтобы импортировать его везде
media_service = MediaService()
