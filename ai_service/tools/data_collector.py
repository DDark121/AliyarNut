from __future__ import annotations

from typing import Annotated, Any, List, Tuple

from icecream import ic
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import InjectedState

from ai_service.memory.langmem import langmem_manager
from ai_service.services.amocrm_service import AmoCrmNotConfiguredError, amocrm_service
from ai_service.services.reminder_scheduler import reminder_scheduler


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_telegram_user(config: RunnableConfig) -> int | None:
    configurable = config.get("configurable", {}) or {}
    context = configurable.get("context", {}) or {}
    metadata = config.get("metadata", {}) or {}

    def get_val(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    peer_id = get_val(context, "peer_id")
    if peer_id:
        try:
            return int(peer_id)
        except (TypeError, ValueError):
            pass

    raw_thread_id = metadata.get("thread_id") or configurable.get("thread_id")
    if raw_thread_id is None:
        return None

    raw_str = str(raw_thread_id)
    prefix, sep, tail = raw_str.partition("_")
    if sep and prefix == "telegram" and tail.isdigit():
        return int(tail)
    if raw_str.isdigit():
        return int(raw_str)

    return None


async def _extract_lead_content_and_payload(
    messages: List[BaseMessage],
) -> Tuple[Any | None, dict[str, Any] | None]:
    memories = await langmem_manager.ainvoke({"messages": messages})
    if not memories:
        return None, None

    content = getattr(memories[0], "content", None)
    if content is None or not hasattr(content, "model_dump"):
        return None, None

    return content, content.model_dump(mode="json")


def _extract_source(config: RunnableConfig) -> str | None:
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


def _extract_context(config: RunnableConfig) -> dict[str, Any]:
    configurable = config.get("configurable", {}) or {}
    context = configurable.get("context", {}) or {}

    if isinstance(context, dict):
        return dict(context)

    return {
        "lead_id": getattr(context, "lead_id", None),
        "peer_id": getattr(context, "peer_id", None),
        "access_hash": getattr(context, "access_hash", None),
        "source": getattr(context, "source", None),
        "source_id": getattr(context, "source_id", None),
        "user_name": getattr(context, "user_name", None),
    }


def _normalize_required(value: Any) -> str:
    return str(value or "").strip()


def _missing_required_fields(lead_payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _normalize_required(lead_payload.get("name")):
        missing.append("name")
    if not _normalize_required(lead_payload.get("phone")):
        missing.append("phone")
    if not _normalize_required(lead_payload.get("payment_check")):
        missing.append("payment_check")
    return missing


def _resolve_touch_target(config: RunnableConfig, source: str | None, context: dict[str, Any]) -> tuple[str | None, int | None]:
    if not source:
        return None, None

    lead_key = (
        _safe_int(context.get("lead_id"))
        or _safe_int(context.get("source_id"))
        or _safe_int(context.get("peer_id"))
    )
    if lead_key is None and source == "telegram":
        lead_key = _extract_telegram_user(config)

    return source, lead_key


async def _disable_fixed_touches_for_call_me(config: RunnableConfig) -> None:
    source = _extract_source(config)
    context = _extract_context(config)
    target_source, target_lead_key = _resolve_touch_target(config, source, context)
    if not target_source or target_lead_key is None:
        ic("call_me: fixed touches disable skipped (target unresolved)")
        return

    try:
        await reminder_scheduler.mark_call_me(source=target_source, lead_key=target_lead_key)
        ic(f"call_me: fixed touches disabled for {target_source}:{target_lead_key}")
    except Exception as exc:
        ic(f"call_me: failed to disable fixed touches for {target_source}:{target_lead_key}: {exc}")


@tool
async def call_me(
    config: Annotated[RunnableConfig, InjectedToolArg],
    messages: Annotated[List[BaseMessage], InjectedState("messages")],
):
    """Финализирует подтвержденную оплату и синхронизирует лида в amoCRM."""
    await _disable_fixed_touches_for_call_me(config)

    extracted_lead, lead_payload = await _extract_lead_content_and_payload(messages)
    if extracted_lead is None or lead_payload is None:
        ic("call_me: не удалось извлечь lead из памяти")
        return "CALL_ME_CRM_FAIL|reason=lead_not_extracted"

    missing = _missing_required_fields(lead_payload)
    if missing:
        ic(f"call_me: missing required fields: {missing}")
        return f"CALL_ME_NEED_FIELDS|missing={','.join(missing)}"

    source = _extract_source(config)
    context = _extract_context(config)
    telegram_user = _extract_telegram_user(config) if source == "telegram" else None

    try:
        crm_result = await amocrm_service.upsert_paid_lead(
            lead_payload=lead_payload,
            context=context,
            telegram_user=telegram_user,
        )
    except ValueError as exc:
        ic(f"call_me validation failed: {exc}")
        details = str(exc).split(":", 1)[-1].strip()
        return f"CALL_ME_NEED_FIELDS|missing={details or 'name,phone,payment_check'}"
    except AmoCrmNotConfiguredError as exc:
        ic(f"call_me amo not configured: {exc}")
        return "CALL_ME_CRM_FAIL|reason=amo_not_configured"
    except Exception as exc:
        ic(f"call_me CRM failed: {exc}")
        return "CALL_ME_CRM_FAIL|reason=amo_upsert_failed"

    if source is None:
        ic("call_me: source is undefined; reminder mark skipped")
    elif source != "telegram":
        ic(f"call_me: source '{source}' completed without reminder mark integration")

    lead_id = crm_result.get("lead_id")
    contact_id = crm_result.get("contact_id")
    lead_action = "created" if bool(crm_result.get("lead_created")) else "updated"
    contact_action = "created" if bool(crm_result.get("contact_created")) else "updated"
    group_link = str(crm_result.get("group_link") or "").strip()

    result_parts = [
        "CALL_ME_OK",
        f"lead_id={lead_id}",
        f"contact_id={contact_id}",
        f"lead_action={lead_action}",
        f"contact_action={contact_action}",
    ]

    if group_link:
        result_parts.append(f"group_link={group_link}")

    warnings = list(crm_result.get("warnings") or [])
    if warnings:
        result_parts.append(f"warnings_count={len(warnings)}")

    skipped_fields = list(crm_result.get("skipped_fields") or [])
    if skipped_fields:
        result_parts.append(f"skipped_fields_count={len(skipped_fields)}")

    return "|".join(result_parts)