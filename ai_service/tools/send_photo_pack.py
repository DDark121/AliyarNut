import base64
import os
import time
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from icecream import ic
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool

from ai_service.services.telegram_service import TelegramApiTimeoutError, telegram_api

_RECENT_SENDS: Dict[str, float] = {}

def _extract_source(config: RunnableConfig) -> Optional[str]:
    configurable = config.get("configurable", {}) or {}
    context = configurable.get("context", {}) or {}
    metadata = config.get("metadata", {}) or {}

    source = None
    if isinstance(context, dict):
        source = context.get("source")
    if not source and isinstance(metadata, dict):
        source = metadata.get("source")
    if source:
        source_str = str(source).strip().lower()
        if source_str == "telegram":
            return source_str
        return None

    raw_thread_id = configurable.get("thread_id")
    if raw_thread_id:
        raw_str = str(raw_thread_id)
        if raw_str.startswith("telegram_"):
            return "telegram"
    return None


def _get_val(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_telegram_peer(config: RunnableConfig) -> tuple[Optional[int], Optional[int]]:
    configurable = config.get("configurable", {}) or {}
    context = configurable.get("context", {}) or {}
    user_id = _get_val(context, "peer_id")
    access_hash = _get_val(context, "access_hash")
    try:
        user_id_int = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id_int = None
    try:
        access_hash_int = int(access_hash) if access_hash is not None else None
    except (TypeError, ValueError):
        access_hash_int = None
    return user_id_int, access_hash_int


def _photo_dir_for(file_id: int) -> Path:
    base_dir = os.getenv("PHOTO_BASE_DIR", "photo").strip() or "photo"
    base = Path(base_dir)
    if not base.is_absolute():
        base = Path.cwd() / base
    return base / str(file_id)


def _collect_photo_payloads(folder: Path) -> List[Dict[str, str]]:
    allowed = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in allowed]
    photos: List[Dict[str, str]] = []
    for file_path in files:
        raw = file_path.read_bytes()
        photos.append(
            {
                "file_name": file_path.name,
                "content_base64": base64.b64encode(raw).decode("utf-8"),
            }
        )
    return photos


def _send_all_enabled() -> bool:
    raw = os.getenv("PHOTO_PACK_SEND_ALL", "1")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _select_photos_for_send(photos: List[Dict[str, str]]) -> List[Dict[str, str]]:
    # По умолчанию отправляем весь набор фото из выбранной папки.
    # Для отладки можно отключить через PHOTO_PACK_SEND_ALL=0.
    if _send_all_enabled():
        return photos
    return photos[:1]


def _cooldown_seconds() -> int:
    raw = os.getenv("PHOTO_PACK_COOLDOWN_SEC", "45")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 45
    return value if value >= 0 else 45


def _dedupe_key(file_id: int, config: RunnableConfig) -> str:
    configurable = (config or {}).get("configurable", {}) or {}
    context = configurable.get("context", {}) or {}
    thread_id = configurable.get("thread_id")
    source = _extract_source(config or {}) or "unknown"
    source_id = _get_val(context, "source_id") or _get_val(context, "peer_id")
    return f"{source}:{source_id}:{thread_id}:{file_id}"


def _is_recent_duplicate(file_id: int, config: RunnableConfig) -> bool:
    key = _dedupe_key(file_id=file_id, config=config or {})
    now = time.time()
    cooldown = _cooldown_seconds()
    last = _RECENT_SENDS.get(key)
    if last is not None and (now - last) < cooldown:
        return True
    _RECENT_SENDS[key] = now
    return False


@tool
async def send_photo_pack(
    file_id: int,
    config: Annotated[RunnableConfig, InjectedToolArg],
    description: Optional[str] = None,
):
    """
    Отправляет фото-пак из папки `photo/<file_id>`.

    Аргументы:
    - file_id: идентификатор папки с изображениями.
    - description: опциональный текст к фото.

    Текущая карта pack_id:
    - 1: рисовая маска для лица (рецепт по запросу про пигментацию/тон)

    """

    if _is_recent_duplicate(file_id=file_id, config=config or {}):
        return "Фото уже отправлялись недавно, повтор пропущен."

    folder = _photo_dir_for(file_id)
    if not folder.exists() or not folder.is_dir():
        return f"Папка с фото не найдена: {folder}"

    photos = _collect_photo_payloads(folder)
    if not photos:
        return f"В папке {folder} нет поддерживаемых изображений."
    photos_to_send = _select_photos_for_send(photos)

    source = _extract_source(config or {})
    if source == "telegram":
        user_id, access_hash = _extract_telegram_peer(config or {})
        if user_id is None or access_hash is None:
            ic(f"send_photo_pack: telegram context invalid user_id={user_id} access_hash={access_hash}")
            return "Не удалось определить пользователя Telegram для отправки фото."

        try:
            await telegram_api.send_photos(
                user_id=user_id,
                access_hash=access_hash,
                photos=photos_to_send,
                description=description,
            )
            return f"Отправлено {len(photos_to_send)} фото. Можешь его не описывать, просто скажи если нужно что то еще."
        except TelegramApiTimeoutError as exc:
            ic(f"send_photo_pack telegram timeout: {repr(exc)}")
            return (
                "Фото отправляются дольше обычного. Они могут прийти с задержкой, "
                "проверьте чат через несколько секунд."
            )
        except Exception as exc:
            ic(f"send_photo_pack telegram failed: {repr(exc)}")
            return "Не удалось отправить фото в Telegram."

    return "Источник не поддерживается для отправки фото."
