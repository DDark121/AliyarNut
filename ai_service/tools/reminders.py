from __future__ import annotations

import re
from typing import Annotated, Any, Dict, Optional

from icecream import ic
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool

from ai_service.services.reminder_scheduler import (
    UnsupportedReminderSourceError,
    reminder_scheduler,
)


def _get_val(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_source(value: Any) -> str:
    source = str(value or "").strip().lower()
    if not source:
        return ""
    return re.sub(r"[^a-z0-9_-]+", "_", source).strip("_")


def _extract_context(config: RunnableConfig) -> Dict[str, Any]:
    configurable = config.get("configurable", {}) or {}
    context = configurable.get("context", {}) or {}
    metadata = config.get("metadata", {}) or {}
    thread_id = configurable.get("thread_id")

    if not isinstance(context, dict):
        context = {
            "peer_id": _get_val(context, "peer_id"),
            "access_hash": _get_val(context, "access_hash"),
            "source": _get_val(context, "source"),
            "source_id": _get_val(context, "source_id"),
            "lead_id": _get_val(context, "lead_id"),
        }
    else:
        context = dict(context)

    source = _normalize_source(context.get("source") or metadata.get("source"))
    if not source and thread_id and str(thread_id).startswith("telegram_"):
        source = "telegram"
    if source:
        context["source"] = source

    source_id = context.get("source_id")
    lead_id = context.get("lead_id")
    if source in {"telegram"} and thread_id:
        raw = str(thread_id)
        prefix, sep, tail = raw.partition("_")
        if sep and tail.isdigit() and prefix == "telegram":
            parsed = int(tail)
            if source_id is None:
                source_id = parsed
            if lead_id is None:
                lead_id = parsed

    if source_id is not None:
        context["source_id"] = source_id
    if lead_id is not None:
        context["lead_id"] = lead_id

    if source == "telegram":
        peer_id = _safe_int(context.get("peer_id") or source_id or lead_id)
        if peer_id is not None:
            context["peer_id"] = peer_id
            context.setdefault("source_id", peer_id)
            context.setdefault("lead_id", peer_id)

    return context


def _resolve_target(context: Dict[str, Any]) -> tuple[Optional[str], Optional[int]]:
    source = _normalize_source(context.get("source")) or None
    lead_key = _safe_int(
        context.get("lead_id")
        or context.get("source_id")
        or context.get("peer_id")
    )
    return source, lead_key


@tool
async def schedule_reminder(
    minutes: int,
    message: str,
    name: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Запланируй напоминание для текущего лида.

    Используй, когда клиент просит написать позже, уточнить информацию или вернуться к диалогу через время.
    Аргументы:
    - minutes: через сколько минут отправить сообщение.
    - message: текст напоминания.
    - name: уникальное имя напоминания для дальнейшего удаления.
    Ограничения:
    - `message` должно быть естественным продолжением диалога и подталкивать к ответу.
    - Не используй слова "напоминание/напоминаю/не забудьте".
    - Не используй приветствия "добрый день/утро/вечер"; если нужно приветствие, используй "здравствуйте".
    """
    if not name or not str(name).strip():
        return "Ошибка: нужно передать уникальное имя напоминания."
    if not message or not str(message).strip():
        return "Ошибка: текст напоминания пуст."

    context = _extract_context(config or {})
    source, lead_key = _resolve_target(context)
    if source is None or lead_key is None:
        ic(f"schedule_reminder: invalid context: {context}")
        return "Ошибка: не удалось определить source и lead_key для напоминания."
    if not reminder_scheduler.supports_source(source):
        return f"Источник '{source}' пока не поддерживается для авто-касаний (нет delivery adapter)."

    try:
        reminder_id, run_time = await reminder_scheduler.schedule(
            source=source,
            lead_key=lead_key,
            minutes=int(minutes),
            message=str(message).strip(),
            name=str(name).strip(),
            context=context,
        )
        await reminder_scheduler.on_smart_scheduled(
            source=source,
            lead_key=int(lead_key),
            reminder_id=reminder_id,
        )
        ic(f"Reminder scheduled: {reminder_id} at {run_time.isoformat()}")
        return f"📅 Напоминание '{name}' запланировано на {run_time.isoformat()}."
    except UnsupportedReminderSourceError as exc:
        ic(f"schedule_reminder unsupported source: {exc}")
        return str(exc)
    except Exception as exc:
        ic(f"schedule_reminder failed: {exc}")
        return "❌ Ошибка при создании напоминания."


@tool
async def Delreminder(
    name: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Удаляет ранее созданное напоминание для текущего лида по имени."""
    if not name or not str(name).strip():
        return "Ошибка: передай имя напоминания."

    context = _extract_context(config or {})
    source, lead_key = _resolve_target(context)
    if source is None or lead_key is None:
        ic(f"Delreminder: invalid context: {context}")
        return "Ошибка: не удалось определить лид и источник."
    if not reminder_scheduler.supports_source(source):
        return f"Источник '{source}' пока не поддерживается для авто-касаний (нет delivery adapter)."

    try:
        reminder_id = reminder_scheduler.get_smart_reminder_id(
            source=source,
            lead_key=int(lead_key),
            name=str(name).strip(),
        )
        removed = await reminder_scheduler.cancel(
            source=source,
            lead_key=lead_key,
            name=str(name).strip(),
        )
        await reminder_scheduler.on_smart_removed(
            source=source,
            lead_key=int(lead_key),
            reminder_id=reminder_id,
        )
        if removed:
            return f"🗑️ Напоминание '{name}' удалено."
        return f"❌ Напоминание '{name}' не найдено."
    except UnsupportedReminderSourceError as exc:
        ic(f"Delreminder unsupported source: {exc}")
        return str(exc)
    except Exception as exc:
        ic(f"Delreminder failed: {exc}")
        return f"❌ Ошибка при удалении напоминания '{name}'."
