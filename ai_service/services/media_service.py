import base64
import binascii
import io
import os
from typing import Optional

from PIL import Image
from PIL import UnidentifiedImageError
from icecream import ic
from openai import AsyncOpenAI

from ai_service.config.settings import settings

class MediaService:
    def __init__(self):
        # Инициализируем клиента один раз при создании сервиса
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.media_debug = os.getenv("AGENT_MEDIA_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}

    def _debug(self, message: str) -> None:
        if self.media_debug:
            ic(message)

    async def transcribe_audio(self, base64_audio: str) -> str:
        """Принимает base64, возвращает текст транскрипции"""
        # 1. Декодируем
        file_bytes = base64.b64decode(base64_audio)
        self._debug(f"[agent-media][audio] decoded_bytes={len(file_bytes)}")
        
        # 2. BytesIO
        voice_data = io.BytesIO(file_bytes)
        voice_data.name = "voice_message.ogg"  # Важно для OpenAI

        # 3. Whisper
        transcript = await self.client.audio.transcriptions.create(
            model="whisper-1",
            file=voice_data,
            response_format="text"
        )
        self._debug(f"[agent-media][audio] transcript_len={len(str(transcript))}")
        return str(transcript)

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
            f"[agent-media][image] clean_base64 original_len={original_len} cleaned_len={len(cleaned)}"
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
