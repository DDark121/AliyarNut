from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from icecream import ic
from redis.asyncio import Redis

from ai_service.config.settings import PROJECT_ROOT
from ai_service.services.telegram_service import telegram_api


class UnsupportedReminderSourceError(ValueError):
    pass


class ReminderSchedulerService:
    def __init__(self) -> None:
        self.redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        self.timezone = ZoneInfo(os.getenv("REMINDER_TZ", "Asia/Tashkent"))
        self.poll_interval = float(os.getenv("REMINDER_POLL_INTERVAL", "1.0"))
        self.batch_size = int(os.getenv("REMINDER_BATCH_SIZE", "50"))
        self.retry_delay_sec = int(os.getenv("REMINDER_RETRY_DELAY_SEC", "60"))
        self.max_attempts = int(os.getenv("REMINDER_MAX_ATTEMPTS", "3"))
        self.payload_ttl_sec = int(os.getenv("REMINDER_PAYLOAD_TTL_SEC", "604800"))
        self.fixed_state_ttl_sec = int(os.getenv("FIXED_TOUCH_STATE_TTL_SEC", "2592000"))
        self.fixed_safe_start_hour = self._hour_from_env("FIXED_TOUCH_SAFE_START_HOUR", 9)
        self.fixed_safe_end_hour = self._hour_from_env("FIXED_TOUCH_SAFE_END_HOUR", 22)
        if self.fixed_safe_end_hour <= self.fixed_safe_start_hour:
            ic(
                "Invalid fixed touch safe window "
                f"{self.fixed_safe_start_hour}-{self.fixed_safe_end_hour}; fallback to 9-22"
            )
            self.fixed_safe_start_hour = 9
            self.fixed_safe_end_hour = 22

        self.schedule_key = "reminders:scheduled"
        self.payload_prefix = "reminders:item:"
        self.fixed_state_prefix = "reminders:fixed:state:"
        self.fixed_job_prefix = "fixed"
        self.smart_active_prefix = "reminders:smart:active:"

        self.redis: Redis | None = None
        self._worker: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self.delivery_adapters: dict[str, Callable[[Dict[str, Any], int, list[str]], Awaitable[None]]] = {
            "telegram": self._send_telegram_messages,
        }

        self.fixed_touches_enabled = str(os.getenv("FIXED_TOUCHES_ENABLED", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if self.fixed_touches_enabled:
            self.fixed_touches = self._load_fixed_touches_config()
        else:
            self.fixed_touches = []
            ic("Fixed touches disabled (FIXED_TOUCHES_ENABLED=0)")

    @staticmethod
    def _hour_from_env(key: str, default: int) -> int:
        raw = str(os.getenv(key, str(default)) or str(default)).strip()
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return default
        return max(0, min(23, parsed))

    def supports_source(self, source: Any) -> bool:
        source_norm = self._normalize_source(source)
        return bool(source_norm and source_norm in self.delivery_adapters)

    def _payload_key(self, reminder_id: str) -> str:
        return f"{self.payload_prefix}{reminder_id}"

    def _fixed_state_key(self, source: str, lead_key: str | int) -> str:
        return f"{self.fixed_state_prefix}{self._lead_key(source, lead_key)}"

    def _smart_active_key(self, source: str, lead_key: str | int) -> str:
        return f"{self.smart_active_prefix}{self._lead_key(source, lead_key)}"

    @staticmethod
    def _slug(value: str) -> str:
        raw = str(value).strip()
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw).strip("_")
        return slug or "reminder"

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _get_redis(self) -> Redis:
        if self.redis is None:
            self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        await self.redis.ping()
        return self.redis

    def _lead_key(self, source: str, lead_key: str | int) -> str:
        source_slug = self._slug(source)
        lead_slug = self._slug(str(lead_key))
        return f"{source_slug}:{lead_slug}"

    def _build_reminder_id(self, source: str, lead_key: str | int, name: str) -> str:
        base = self._lead_key(source=source, lead_key=lead_key)
        name_slug = self._slug(name)
        return f"{base}:{name_slug}"

    def _fixed_job_id(self, source: str, lead_key: str | int) -> str:
        return f"{self.fixed_job_prefix}:{self._lead_key(source, lead_key)}"

    @staticmethod
    def _split_message_on_tilde(message: str) -> list[str]:
        if not message:
            return []
        if "~" not in message:
            stripped = message.strip()
            return [stripped] if stripped else []
        parts = [part.strip() for part in message.split("~")]
        return [part for part in parts if part]

    @staticmethod
    def _extract_content_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        chunks.append(item["text"])
                elif isinstance(item, str):
                    chunks.append(item)
            return " ".join(part.strip() for part in chunks if part and part.strip()).strip()
        return str(content).strip() if content is not None else ""

    @staticmethod
    def _default_fixed_touches() -> list[dict[str, Any]]:
        return [
            {
                "name": "touch_1",
                "delay_minutes": 1,
                "goal": "Сделай мягкое касание и уточни, удобно ли продолжить диалог сейчас.",
            },
            {
                "name": "touch_2",
                "delay_minutes": 3,
                "goal": "Сделай касание с выгодой: предложи небольшой бонус и мягко верни к следующему шагу.",
            },
            {
                "name": "touch_3",
                "delay_minutes": 5,
                "goal": "Финальное мягкое касание: уточни, когда лучше вернуться к обсуждению.",
            },
        ]

    def _load_fixed_touches_config(self) -> list[dict[str, Any]]:
        raw_path = os.getenv("FIXED_TOUCHES_CONFIG", "config/fixed_touches.json")
        config_path = Path(raw_path)
        if not config_path.is_absolute():
            config_path = PROJECT_ROOT / raw_path

        payload: Any = None
        try:
            if config_path.exists():
                payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            ic(f"Failed to load fixed touches config '{config_path}': {exc}")

        if payload is None:
            payload = self._default_fixed_touches()

        if isinstance(payload, dict):
            payload = payload.get("touches", [])

        if not isinstance(payload, list):
            payload = []

        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(payload):
            if not isinstance(item, dict):
                continue
            goal = str(item.get("goal") or "").strip()
            if not goal:
                continue
            delay_raw = item.get("delay_minutes")
            delay_minutes = self._safe_int(delay_raw)
            if delay_minutes is None or delay_minutes <= 0:
                continue
            normalized.append(
                {
                    "name": str(item.get("name") or f"touch_{idx + 1}"),
                    "delay_minutes": int(delay_minutes),
                    "goal": goal,
                }
            )

        normalized.sort(key=lambda x: x["delay_minutes"])
        return normalized

    @staticmethod
    def _normalize_source(value: Any) -> str:
        source = str(value or "").strip().lower()
        if not source:
            return ""
        source = re.sub(r"[^a-z0-9_-]+", "_", source).strip("_")
        return source

    def _extract_lead_from_context(self, context: Dict[str, Any]) -> Optional[int]:
        return self._safe_int(
            context.get("lead_id")
            or context.get("source_id")
            or context.get("peer_id")
        )

    def _resolve_target(self, *, thread_id: str | None, context: Dict[str, Any]) -> tuple[str, Optional[int]]:
        source = self._normalize_source(context.get("source"))
        if not source and thread_id:
            raw = str(thread_id)
            if raw.startswith("telegram_"):
                source = "telegram"

        lead = self._extract_lead_from_context(context)
        if lead is None and source == "telegram" and thread_id:
            raw = str(thread_id)
            prefix, sep, tail = raw.partition("_")
            if sep and prefix == "telegram" and tail.isdigit():
                lead = int(tail)

        return source, lead

    def _initial_fixed_state(
        self,
        *,
        source: str,
        lead_key: int,
        context: Dict[str, Any],
        thread_id: str | None,
    ) -> Dict[str, Any]:
        return {
            "source": source,
            "lead_key": int(lead_key),
            "touch_index": 0,
            "last_client_ts": None,
            "last_client_message": "",
            "call_me_done": False,
            "completed": False,
            "context": context,
            "thread_id": thread_id,
        }

    async def _load_fixed_state(
        self,
        *,
        source: str,
        lead_key: int,
        context: Dict[str, Any],
        thread_id: str | None,
    ) -> Dict[str, Any]:
        redis = await self._get_redis()
        key = self._fixed_state_key(source, lead_key)
        raw = await redis.get(key)
        if not raw:
            return self._initial_fixed_state(
                source=source,
                lead_key=lead_key,
                context=context,
                thread_id=thread_id,
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return self._initial_fixed_state(
                source=source,
                lead_key=lead_key,
                context=context,
                thread_id=thread_id,
            )
        if not isinstance(payload, dict):
            payload = {}
        merged = self._initial_fixed_state(
            source=source,
            lead_key=lead_key,
            context=context,
            thread_id=thread_id,
        )
        merged.update(payload)
        return merged

    async def _save_fixed_state(self, source: str, lead_key: int, state: Dict[str, Any]) -> None:
        redis = await self._get_redis()
        key = self._fixed_state_key(source, lead_key)
        await redis.set(key, json.dumps(state, ensure_ascii=False), ex=self.fixed_state_ttl_sec)

    async def _schedule_payload(self, reminder_id: str, run_ts: float, payload: Dict[str, Any]) -> None:
        redis = await self._get_redis()
        payload_key = self._payload_key(reminder_id)
        pipe = redis.pipeline(transaction=True)
        pipe.set(payload_key, json.dumps(payload, ensure_ascii=False), ex=self.payload_ttl_sec)
        pipe.zadd(self.schedule_key, {reminder_id: run_ts})
        await pipe.execute()

    async def _cancel_job(self, reminder_id: str) -> bool:
        redis = await self._get_redis()
        payload_key = self._payload_key(reminder_id)
        pipe = redis.pipeline(transaction=True)
        pipe.zrem(self.schedule_key, reminder_id)
        pipe.delete(payload_key)
        removed, deleted = await pipe.execute()
        return bool(removed or deleted)

    async def _cancel_fixed_job(self, source: str, lead_key: int) -> bool:
        return await self._cancel_job(self._fixed_job_id(source, lead_key))

    async def _smart_active_count(self, source: str, lead_key: int) -> int:
        redis = await self._get_redis()
        return int(await redis.scard(self._smart_active_key(source, lead_key)))

    async def _is_fixed_paused(self, source: str, lead_key: int) -> bool:
        return (await self._smart_active_count(source, lead_key)) > 0

    def _apply_fixed_safe_window(self, due_ts: float) -> float:
        due_candidate = max(float(due_ts), time.time() + 0.5)
        due_local = datetime.fromtimestamp(due_candidate, tz=self.timezone)
        start_local = due_local.replace(
            hour=self.fixed_safe_start_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        end_local = due_local.replace(
            hour=self.fixed_safe_end_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

        if due_local < start_local:
            return start_local.timestamp()
        if due_local > end_local:
            next_start = (due_local + timedelta(days=1)).replace(
                hour=self.fixed_safe_start_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            return next_start.timestamp()
        return due_local.timestamp()

    async def _schedule_next_fixed_touch(self, source: str, lead_key: int, state: Dict[str, Any]) -> None:
        if not self.fixed_touches:
            return
        if not self.supports_source(source):
            ic(f"Fixed touch skipped: unsupported source '{source}'")
            return

        touch_index = int(state.get("touch_index") or 0)
        if touch_index >= len(self.fixed_touches):
            state["completed"] = True
            await self._save_fixed_state(source, lead_key, state)
            return

        last_client_ts = state.get("last_client_ts")
        if not isinstance(last_client_ts, (int, float)):
            return

        touch = self.fixed_touches[touch_index]
        due_ts = float(last_client_ts) + float(touch["delay_minutes"]) * 60.0
        run_ts = self._apply_fixed_safe_window(due_ts)

        reminder_id = self._fixed_job_id(source, lead_key)
        payload = {
            "id": reminder_id,
            "kind": "fixed",
            "source": source,
            "lead_key": int(lead_key),
            "touch_index": touch_index,
            "attempts": 0,
        }
        await self._schedule_payload(reminder_id=reminder_id, run_ts=run_ts, payload=payload)

    async def on_client_message(
        self,
        *,
        thread_id: str | None,
        context: Dict[str, Any],
        message: str,
    ) -> None:
        if not self.fixed_touches_enabled or not self.fixed_touches:
            return

        source, lead_key = self._resolve_target(thread_id=thread_id, context=context)
        if not source or lead_key is None:
            return
        if not self.supports_source(source):
            ic(f"Fixed touch ignored: no delivery adapter for source '{source}'")
            return

        state = await self._load_fixed_state(
            source=source,
            lead_key=lead_key,
            context=context,
            thread_id=thread_id,
        )
        if state.get("call_me_done") or state.get("completed"):
            return

        state["context"] = dict(context)
        state["thread_id"] = thread_id
        state["last_client_ts"] = time.time()
        state["last_client_message"] = str(message or "").strip()[:3000]
        await self._save_fixed_state(source, lead_key, state)
        await self._cancel_fixed_job(source, lead_key)

        if await self._is_fixed_paused(source, lead_key):
            return
        await self._schedule_next_fixed_touch(source, lead_key, state)

    async def mark_call_me(self, *, source: str, lead_key: int) -> None:
        source_norm = self._normalize_source(source)
        if not source_norm:
            return

        state = await self._load_fixed_state(
            source=source_norm,
            lead_key=lead_key,
            context={},
            thread_id=None,
        )
        state["call_me_done"] = True
        state["completed"] = True
        await self._save_fixed_state(source_norm, lead_key, state)
        await self._cancel_fixed_job(source_norm, lead_key)

    async def on_smart_scheduled(self, *, source: str, lead_key: int, reminder_id: str) -> None:
        source_norm = self._normalize_source(source)
        if not source_norm:
            return

        redis = await self._get_redis()
        key = self._smart_active_key(source_norm, lead_key)
        await redis.sadd(key, reminder_id)
        await redis.expire(key, self.payload_ttl_sec)
        await self._cancel_fixed_job(source_norm, lead_key)

    async def on_smart_removed(self, *, source: str, lead_key: int, reminder_id: str) -> None:
        source_norm = self._normalize_source(source)
        if not source_norm:
            return

        redis = await self._get_redis()
        key = self._smart_active_key(source_norm, lead_key)
        await redis.srem(key, reminder_id)
        remaining = int(await redis.scard(key))
        if remaining > 0:
            return
        if not self.fixed_touches_enabled:
            return

        state = await self._load_fixed_state(
            source=source_norm,
            lead_key=lead_key,
            context={},
            thread_id=None,
        )
        if state.get("call_me_done") or state.get("completed"):
            return

        await self._schedule_next_fixed_touch(source_norm, lead_key, state)

    async def _send_telegram_messages(self, context: Dict[str, Any], lead_key: int, messages: list[str]) -> None:
        peer_id = self._safe_int(
            context.get("peer_id")
            or context.get("source_id")
            or context.get("lead_id")
            or lead_key
        )
        if peer_id is None:
            raise ValueError("Telegram reminder has no peer_id/source_id/lead_id")
        for msg in messages:
            await telegram_api.send_message(user_id=peer_id, message=msg)

    async def _send_messages(self, *, source: str, context: Dict[str, Any], lead_key: int, messages: list[str]) -> None:
        adapter = self.delivery_adapters.get(source)
        if adapter is None:
            raise UnsupportedReminderSourceError(
                f"Нет delivery adapter для source='{source}'. Планирование пропущено."
            )
        await adapter(context, lead_key, messages)

    async def _generate_fixed_touch_message(
        self,
        *,
        source: str,
        lead_key: int,
        touch_index: int,
        state: Dict[str, Any],
    ) -> str:
        touch = self.fixed_touches[touch_index]
        goal = str(touch.get("goal") or "").strip()
        if not goal:
            return ""

        thread_id = str(state.get("thread_id") or "").strip()
        if not thread_id:
            return self._fixed_touch_fallback()

        try:
            from langchain_core.messages import SystemMessage
            from ai_service.agents.core_agent import agent
        except Exception as exc:
            ic(f"Fixed touch agent import failed for {source}:{lead_key}: {exc}")
            return self._fixed_touch_fallback()

        context = state.get("context") if isinstance(state.get("context"), dict) else {}
        context_data = dict(context)
        context_data.setdefault("source", source)
        context_data.setdefault("lead_id", int(lead_key))
        context_data.setdefault("source_id", str(lead_key))
        context_data.setdefault("peer_id", int(lead_key))

        now_local = datetime.now(self.timezone)
        context_data["current_datetime"] = now_local.strftime("%Y-%m-%d %H:%M:%S")
        context_data["current_date"] = now_local.strftime("%Y-%m-%d")
        context_data["current_time"] = now_local.strftime("%H:%M:%S")
        context_data["current_timezone"] = str(self.timezone)

        system_prompt = (
            "fixed_touch_mode=1\n"
            "Служебный режим: это плановое касание в уже существующем диалоге.\n"
            f"source={source}\n"
            f"lead_key={lead_key}\n"
            f"touch_index={touch_index + 1}/{len(self.fixed_touches)}\n"
            f"goal={goal}\n"
            f"last_client_message={state.get('last_client_message') or 'нет'}\n\n"
            "Сгенерируй только текст сообщения для клиента.\n"
            "Требования:\n"
            "- 1-3 коротких предложения.\n"
            "- Мягкий, естественный тон.\n"
            "- Продолжай контекст текущего треда, не начинай диалог заново.\n"
            "- Не задавай повторно базовые вопросы (имя/телефон и т.п.), если это уже известно в памяти.\n"
            "- Без техтекста, markdown и служебных пояснений.\n"
            "- Не используй 'добрый день/утро/вечер'; если приветствие нужно, используй 'здравствуйте'."
        )
        config = {"configurable": {"thread_id": thread_id, "context": context_data}}

        try:
            result = await agent.ainvoke(
                input={"messages": [SystemMessage(content=system_prompt)]},
                context=context_data,
                config=config,
            )
            messages = list(result.get("messages") or [])
            if not messages:
                return self._fixed_touch_fallback()
            text = self._extract_content_text(getattr(messages[-1], "content", ""))
            if not text:
                return self._fixed_touch_fallback()
            return text
        except Exception as exc:
            ic(f"Fixed touch generation failed for {source}:{lead_key}: {exc}")
            return self._fixed_touch_fallback()

    @staticmethod
    def _fixed_touch_fallback() -> str:
        return (
            "Здравствуйте! Я на связи по вашему запросу. "
            "Если вам удобно, можем продолжить с короткого следующего шага."
        )

    def calculate_safe_time(self, minutes: float) -> datetime:
        now = datetime.now(self.timezone)
        delta_min = max(0, float(minutes))
        planned = now + timedelta(minutes=delta_min)
        earliest = planned.replace(hour=8, minute=0, second=0, microsecond=0)
        latest = planned.replace(hour=22, minute=0, second=0, microsecond=0)

        if planned > latest:
            planned = (planned + timedelta(days=1)).replace(
                hour=8,
                minute=0,
                second=0,
                microsecond=0,
            )
        elif planned < earliest:
            planned = earliest
        return planned

    async def schedule(
        self,
        *,
        source: str,
        lead_key: str | int,
        minutes: float,
        message: str,
        name: str,
        context: Dict[str, Any],
    ) -> tuple[str, datetime]:
        source_norm = self._normalize_source(source)
        if not source_norm:
            raise ValueError("source is required")
        if not self.supports_source(source_norm):
            raise UnsupportedReminderSourceError(
                f"Для source='{source_norm}' нет адаптера доставки."
            )

        lead_int = self._safe_int(lead_key)
        if lead_int is None:
            raise ValueError("lead_key is required")

        run_time = self.calculate_safe_time(minutes)
        reminder_id = self._build_reminder_id(source=source_norm, lead_key=lead_int, name=name)
        payload = {
            "id": reminder_id,
            "kind": "smart",
            "name": name,
            "source": source_norm,
            "lead_key": int(lead_int),
            "message": message,
            "run_at": run_time.isoformat(),
            "context": context,
            "attempts": 0,
        }
        await self._schedule_payload(
            reminder_id=reminder_id,
            run_ts=run_time.timestamp(),
            payload=payload,
        )
        return reminder_id, run_time

    async def cancel(self, *, source: str, lead_key: str | int, name: str) -> bool:
        reminder_id = self.get_smart_reminder_id(source=source, lead_key=lead_key, name=name)
        return await self._cancel_job(reminder_id)

    def get_smart_reminder_id(self, *, source: str, lead_key: str | int, name: str) -> str:
        source_norm = self._normalize_source(source)
        lead_int = self._safe_int(lead_key)
        if not source_norm or lead_int is None:
            return self._build_reminder_id(source=source or "unknown", lead_key=str(lead_key), name=name)
        return self._build_reminder_id(source=source_norm, lead_key=lead_int, name=name)

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stop_event.clear()
        self._worker = asyncio.create_task(self._run_loop())
        ic("Reminder scheduler worker started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        if self.redis:
            await self.redis.aclose()
            self.redis = None
        ic("Reminder scheduler worker stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                redis = await self._get_redis()
                now_ts = time.time()
                due_ids = await redis.zrangebyscore(
                    self.schedule_key,
                    min="-inf",
                    max=now_ts,
                    start=0,
                    num=self.batch_size,
                )
                if not due_ids:
                    await asyncio.sleep(self.poll_interval)
                    continue

                for reminder_id in due_ids:
                    claimed = await redis.zrem(self.schedule_key, reminder_id)
                    if not claimed:
                        continue
                    payload_key = self._payload_key(reminder_id)
                    payload_raw = await redis.get(payload_key)
                    if not payload_raw:
                        continue
                    try:
                        payload = json.loads(payload_raw)
                    except json.JSONDecodeError:
                        await redis.delete(payload_key)
                        continue

                    success = await self._dispatch(payload)
                    if success:
                        await redis.delete(payload_key)
                        kind = str(payload.get("kind") or "smart").strip().lower()
                        if kind == "smart":
                            lead_key = self._safe_int(payload.get("lead_key"))
                            if lead_key is not None:
                                await self.on_smart_removed(
                                    source=str(payload.get("source") or ""),
                                    lead_key=lead_key,
                                    reminder_id=str(payload.get("id") or reminder_id),
                                )
                    else:
                        await self._reschedule_on_failure(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ic(f"Reminder scheduler loop error: {exc}")
                await asyncio.sleep(max(self.poll_interval, 1.0))

    async def _dispatch(self, payload: Dict[str, Any]) -> bool:
        kind = str(payload.get("kind") or "smart").strip().lower()
        if kind == "fixed":
            if not self.fixed_touches_enabled:
                return True
            return await self._dispatch_fixed(payload)
        return await self._dispatch_smart(payload)

    async def _dispatch_smart(self, payload: Dict[str, Any]) -> bool:
        source = self._normalize_source(payload.get("source"))
        if not self.supports_source(source):
            ic(f"Reminder dropped: unsupported source '{source}'")
            return True

        message = str(payload.get("message") or "").strip()
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        messages = self._split_message_on_tilde(message)
        if not messages:
            return True

        try:
            lead_key = self._safe_int(payload.get("lead_key"))
            if lead_key is None:
                raise ValueError("smart reminder missing lead_key")
            await self._send_messages(
                source=source,
                context=context,
                lead_key=lead_key,
                messages=messages,
            )
            return True
        except UnsupportedReminderSourceError as exc:
            ic(f"Reminder dispatch skipped id={payload.get('id')} source={source}: {exc}")
            return True
        except Exception as exc:
            ic(f"Reminder dispatch failed id={payload.get('id')} source={source}: {exc}")
            return False

    async def _dispatch_fixed(self, payload: Dict[str, Any]) -> bool:
        source = self._normalize_source(payload.get("source"))
        if not self.supports_source(source):
            ic(f"Fixed reminder dropped: unsupported source '{source}'")
            return True

        lead_key = self._safe_int(payload.get("lead_key"))
        touch_index = self._safe_int(payload.get("touch_index"))
        if not source or lead_key is None or touch_index is None:
            return True

        try:
            state = await self._load_fixed_state(
                source=source,
                lead_key=lead_key,
                context={},
                thread_id=None,
            )
            if state.get("call_me_done") or state.get("completed"):
                await self._cancel_fixed_job(source, lead_key)
                return True

            current_index = int(state.get("touch_index") or 0)
            if current_index != int(touch_index):
                return True

            if await self._is_fixed_paused(source, lead_key):
                return True

            if current_index >= len(self.fixed_touches):
                state["completed"] = True
                await self._save_fixed_state(source, lead_key, state)
                return True

            text = await self._generate_fixed_touch_message(
                source=source,
                lead_key=lead_key,
                touch_index=current_index,
                state=state,
            )
            messages = self._split_message_on_tilde(text)
            if not messages:
                return False

            context = state.get("context") if isinstance(state.get("context"), dict) else {}
            await self._send_messages(
                source=source,
                context=context,
                lead_key=lead_key,
                messages=messages,
            )

            state["touch_index"] = current_index + 1
            if state["touch_index"] >= len(self.fixed_touches):
                state["completed"] = True
            await self._save_fixed_state(source, lead_key, state)

            if not state.get("completed") and not await self._is_fixed_paused(source, lead_key):
                await self._schedule_next_fixed_touch(source, lead_key, state)

            return True
        except UnsupportedReminderSourceError as exc:
            ic(f"Fixed reminder dispatch skipped id={payload.get('id')} source={source}: {exc}")
            return True
        except Exception as exc:
            ic(f"Fixed reminder dispatch failed id={payload.get('id')} source={source}: {exc}")
            return False

    async def _reschedule_on_failure(self, payload: Dict[str, Any]) -> None:
        redis = await self._get_redis()
        reminder_id = str(payload.get("id") or "")
        if not reminder_id:
            return

        attempts = int(payload.get("attempts") or 0) + 1
        kind = str(payload.get("kind") or "smart").strip().lower()
        if attempts > self.max_attempts:
            ic(f"Reminder dropped after max attempts: {reminder_id}")
            await redis.delete(self._payload_key(reminder_id))
            if kind == "smart":
                lead_key = self._safe_int(payload.get("lead_key"))
                if lead_key is not None:
                    await self.on_smart_removed(
                        source=str(payload.get("source") or ""),
                        lead_key=lead_key,
                        reminder_id=reminder_id,
                    )
            return

        payload["attempts"] = attempts
        retry_score = time.time() + float(self.retry_delay_sec)
        await self._schedule_payload(
            reminder_id=reminder_id,
            run_ts=retry_score,
            payload=payload,
        )


reminder_scheduler = ReminderSchedulerService()
