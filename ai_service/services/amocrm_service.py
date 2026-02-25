from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

import httpx
from icecream import ic


class AmoCrmServiceError(RuntimeError):
    pass


class AmoCrmNotConfiguredError(AmoCrmServiceError):
    pass


@dataclass
class AmoUpsertResult:
    lead_id: int
    contact_id: int
    lead_created: bool
    contact_created: bool
    warnings: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    group_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_id": int(self.lead_id),
            "contact_id": int(self.contact_id),
            "lead_created": bool(self.lead_created),
            "contact_created": bool(self.contact_created),
            "warnings": list(self.warnings),
            "skipped_fields": list(self.skipped_fields),
            "group_link": self.group_link,
        }


class AmoCrmService:
    CLOSED_STATUS_IDS = {142, 143}

    def __init__(self) -> None:
        self.subdomain = str(os.getenv("AMO_SUBDOMAIN", "") or "").strip()
        self.access_token = str(os.getenv("AMO_ACCESS_TOKEN", "") or "").strip()
        self.base_url = f"https://{self.subdomain}.amocrm.ru" if self.subdomain else ""
        self.pipeline_id = self._safe_int(os.getenv("AMO_PIPELINE_ID")) or 9873894
        self.status_id = self._safe_int(os.getenv("AMO_STATUS_ID")) or 78511606
        self.responsible_user_id = self._safe_int(os.getenv("AMO_RESPONSIBLE_USER_ID"))
        self.price_fallback_sum = self._safe_int(os.getenv("AMO_PRICE_FALLBACK_SUM")) or 125000
        self.source_default = str(os.getenv("AMO_SOURCE_DEFAULT", "ТГ") or "ТГ").strip()
        self.group_link = str(os.getenv("PAID_GROUP_LINK", "") or "").strip()
        self.timeout_sec = max(5.0, float(os.getenv("AMO_TIMEOUT_SEC", "20")))
        self.meta_ttl_sec = max(30.0, float(os.getenv("AMO_METADATA_TTL_SEC", "600")))
        self.timezone = ZoneInfo(os.getenv("REMINDER_TZ", "Asia/Tashkent"))

        self._client: httpx.AsyncClient | None = None
        self._meta_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.subdomain and self.access_token)

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            if isinstance(value, bool):
                return int(value)
            text = str(value).strip()
            if not text:
                return None
            return int(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return re.sub(r"[^a-z0-9а-яё]+", "", str(value or "").strip().lower())

    @classmethod
    def _find_field(cls, fields: list[dict[str, Any]], *names: str) -> dict[str, Any] | None:
        targets = [cls._normalize_text(name) for name in names if str(name or "").strip()]
        targets = [target for target in targets if target]
        if not targets:
            return None

        for field in fields:
            candidate = cls._normalize_text(field.get("name"))
            if candidate in targets:
                return field

        for target in targets:
            for field in fields:
                candidate = cls._normalize_text(field.get("name"))
                if not candidate:
                    continue
                if target in candidate or candidate in target:
                    return field
        return None

    @staticmethod
    def _normalize_phone(value: Any) -> str:
        digits = re.sub(r"\D+", "", str(value or ""))
        if digits.startswith("00"):
            digits = digits[2:]
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        return digits

    @staticmethod
    def _phone_display(raw_phone: Any, normalized_phone: str) -> str:
        raw = str(raw_phone or "").strip()
        if raw.startswith("+"):
            return raw
        if normalized_phone:
            return f"+{normalized_phone}"
        return raw

    @staticmethod
    def _extract_embedded_items(payload: Any, key: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        embedded = payload.get("_embedded")
        if not isinstance(embedded, dict):
            return []
        items = embedded.get(key)
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    async def _get_client(self) -> httpx.AsyncClient:
        if not self.enabled:
            raise AmoCrmNotConfiguredError(
                "amoCRM не настроен: задайте AMO_SUBDOMAIN и AMO_ACCESS_TOKEN."
            )
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_sec,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Any] = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        client = await self._get_client()
        try:
            response = await client.request(
                method=method,
                url=path,
                params=params,
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise AmoCrmServiceError(f"{method} {path} network error: {exc}") from exc

        if response.status_code not in expected:
            raise AmoCrmServiceError(
                f"{method} {path} failed with {response.status_code}: {response.text}"
            )

        if response.status_code == 204:
            return None
        body = response.text.strip()
        if not body:
            return None
        try:
            return response.json()
        except Exception:
            return None

    async def _fetch_all(
        self,
        *,
        path: str,
        embedded_key: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        page = 1
        limit = 250
        all_items: list[dict[str, Any]] = []
        base = dict(params or {})

        while True:
            req_params = dict(base)
            req_params["page"] = page
            req_params["limit"] = limit
            data = await self._request("GET", path, params=req_params, expected=(200,))
            items = self._extract_embedded_items(data, embedded_key)
            if not items:
                break
            all_items.extend(items)
            if len(items) < limit:
                break
            page += 1
        return all_items

    async def _cached_list(
        self,
        key: str,
        loader: Callable[[], Any],
    ) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._meta_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        loaded = await loader()
        if not isinstance(loaded, list):
            loaded = []
        self._meta_cache[key] = (now + self.meta_ttl_sec, loaded)
        return loaded

    async def _load_lead_fields(self) -> list[dict[str, Any]]:
        return await self._fetch_all(path="/api/v4/leads/custom_fields", embedded_key="custom_fields")

    async def _load_contact_fields(self) -> list[dict[str, Any]]:
        return await self._fetch_all(path="/api/v4/contacts/custom_fields", embedded_key="custom_fields")

    async def _lead_fields(self) -> list[dict[str, Any]]:
        return await self._cached_list("lead_fields", self._load_lead_fields)

    async def _contact_fields(self) -> list[dict[str, Any]]:
        return await self._cached_list("contact_fields", self._load_contact_fields)

    async def _phone_field_id(self) -> Optional[int]:
        fields = await self._contact_fields()
        for field in fields:
            code = str(field.get("code") or "").strip().upper()
            if code == "PHONE":
                return self._safe_int(field.get("id"))
        fallback = self._find_field(fields, "Телефон", "Phone")
        if fallback:
            return self._safe_int(fallback.get("id"))
        return None

    def _contact_phones(self, contact: dict[str, Any], phone_field_id: Optional[int]) -> set[str]:
        values = list(contact.get("custom_fields_values") or [])
        phones: set[str] = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            field_code = str(item.get("field_code") or "").strip().upper()
            field_id = self._safe_int(item.get("field_id"))
            if field_code != "PHONE" and (phone_field_id is None or field_id != phone_field_id):
                continue
            for entry in list(item.get("values") or []):
                if not isinstance(entry, dict):
                    continue
                normalized = self._normalize_phone(entry.get("value"))
                if normalized:
                    phones.add(normalized)
        return phones

    async def _search_contacts(self, query: str) -> list[dict[str, Any]]:
        q = str(query or "").strip()
        if not q:
            return []
        return await self._fetch_all(path="/api/v4/contacts", embedded_key="contacts", params={"query": q})

    async def _find_contact_by_phone(self, phone: str) -> Optional[dict[str, Any]]:
        normalized_phone = self._normalize_phone(phone)
        if not normalized_phone:
            return None

        phone_field_id = await self._phone_field_id()
        queries = [normalized_phone, f"+{normalized_phone}"]
        seen: set[int] = set()
        for query in queries:
            contacts = await self._search_contacts(query)
            for contact in contacts:
                contact_id = self._safe_int(contact.get("id"))
                if contact_id is None or contact_id in seen:
                    continue
                seen.add(contact_id)
                if normalized_phone in self._contact_phones(contact, phone_field_id):
                    return contact
        return None

    async def _create_contact(self, *, name: str, phone_raw: str) -> dict[str, Any]:
        phone_field_id = await self._phone_field_id()
        normalized_phone = self._normalize_phone(phone_raw)
        payload: dict[str, Any] = {
            "name": name or f"Клиент {normalized_phone}",
        }
        if phone_field_id and normalized_phone:
            payload["custom_fields_values"] = [
                {
                    "field_id": int(phone_field_id),
                    "values": [
                        {
                            "value": self._phone_display(phone_raw, normalized_phone),
                            "enum_code": "WORK",
                        }
                    ],
                }
            ]

        data = await self._request("POST", "/api/v4/contacts", payload=[payload], expected=(200,))
        contacts = self._extract_embedded_items(data, "contacts")
        if not contacts:
            raise AmoCrmServiceError("amoCRM не вернул созданный контакт.")
        return contacts[0]

    async def _patch_contact(
        self,
        *,
        contact_id: int,
        name: str,
        add_phone: str,
    ) -> dict[str, Any]:
        phone_field_id = await self._phone_field_id()
        normalized_phone = self._normalize_phone(add_phone)
        patch: dict[str, Any] = {"id": int(contact_id)}
        if name:
            patch["name"] = name
        if phone_field_id and normalized_phone:
            patch["custom_fields_values"] = [
                {
                    "field_id": int(phone_field_id),
                    "values": [
                        {
                            "value": self._phone_display(add_phone, normalized_phone),
                            "enum_code": "WORK",
                        }
                    ],
                }
            ]

        data = await self._request("PATCH", "/api/v4/contacts", payload=[patch], expected=(200,))
        contacts = self._extract_embedded_items(data, "contacts")
        if contacts:
            return contacts[0]
        return {"id": int(contact_id), "name": name}

    async def _upsert_contact(self, *, name: str, phone: str) -> tuple[dict[str, Any], bool]:
        normalized_phone = self._normalize_phone(phone)
        existing = await self._find_contact_by_phone(normalized_phone)
        if existing is None:
            created = await self._create_contact(name=name, phone_raw=phone)
            return created, True

        phone_field_id = await self._phone_field_id()
        current_phones = self._contact_phones(existing, phone_field_id)
        need_add_phone = normalized_phone not in current_phones
        current_name = str(existing.get("name") or "").strip()
        need_update_name = bool(name and (not current_name or current_name.lower() != name.lower()))

        if not need_add_phone and not need_update_name:
            return existing, False

        patched = await self._patch_contact(
            contact_id=int(existing["id"]),
            name=name if need_update_name else "",
            add_phone=phone if need_add_phone else "",
        )
        return patched, False

    async def _leads_by_contact(self, contact_id: int) -> list[dict[str, Any]]:
        params_variants = [
            {"filter[contacts]": int(contact_id)},
            {"filter[contacts][0]": int(contact_id)},
        ]
        for params in params_variants:
            try:
                leads = await self._fetch_all(path="/api/v4/leads", embedded_key="leads", params=params)
            except AmoCrmServiceError as exc:
                ic(f"amo leads by contact fallback: {exc}")
                continue
            if leads:
                return leads
        return []

    def _pick_active_lead(self, leads: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not leads:
            return None

        def sort_key(lead: dict[str, Any]) -> int:
            return self._safe_int(lead.get("updated_at")) or self._safe_int(lead.get("created_at")) or 0

        ordered = sorted(leads, key=sort_key, reverse=True)
        for lead in ordered:
            pipeline_id = self._safe_int(lead.get("pipeline_id"))
            status_id = self._safe_int(lead.get("status_id"))
            if pipeline_id != self.pipeline_id:
                continue
            if status_id in self.CLOSED_STATUS_IDS:
                continue
            return lead
        return None

    @classmethod
    def _resolve_enum_id(cls, field: dict[str, Any], raw_value: Any) -> Optional[int]:
        enums = list(field.get("enums") or [])
        if not enums:
            return None

        desired = str(raw_value or "").strip()
        if not desired:
            return None

        desired_id = cls._safe_int(desired)
        if desired_id is not None:
            for enum in enums:
                enum_id = cls._safe_int(enum.get("id"))
                if enum_id == desired_id:
                    return enum_id

        desired_norm = cls._normalize_text(desired)
        for enum in enums:
            value_norm = cls._normalize_text(enum.get("value"))
            code_norm = cls._normalize_text(enum.get("code"))
            if desired_norm and (desired_norm == value_norm or desired_norm == code_norm):
                return cls._safe_int(enum.get("id"))

        for enum in enums:
            value_norm = cls._normalize_text(enum.get("value"))
            if desired_norm and desired_norm in value_norm:
                return cls._safe_int(enum.get("id"))
        return None

    def _to_unix_ts(self, raw_value: Any) -> Optional[int]:
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, (int, float)):
            return int(raw_value)
        if isinstance(raw_value, datetime):
            dt = raw_value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.timezone)
            return int(dt.timestamp())

        text = str(raw_value).strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.timezone)
        return int(dt.timestamp())

    @staticmethod
    def _to_int(raw_value: Any) -> Optional[int]:
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, (int, float)):
            return int(raw_value)
        text = str(raw_value).strip()
        if not text:
            return None
        clean = re.sub(r"[^\d-]+", "", text)
        if clean in {"", "-"}:
            return None
        try:
            return int(clean)
        except ValueError:
            return None

    def _resolve_price(self, payload: dict[str, Any]) -> int:
        direct = self._to_int(payload.get("payment_amount_sum"))
        if direct and direct > 0:
            return direct

        text = str(payload.get("payment_check") or "").strip()
        if text:
            for token in re.findall(r"\d[\d\s.,]{3,}", text):
                numeric = self._to_int(token)
                if numeric and 5000 <= numeric <= 2_000_000:
                    return numeric

        return int(self.price_fallback_sum)

    @staticmethod
    def _truncate(value: Any, limit: int = 900) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _derive_transaction_id(self, payload: dict[str, Any]) -> str:
        explicit = str(payload.get("payment_transaction_id") or "").strip()
        if explicit:
            return explicit[:255]

        check = str(payload.get("payment_check") or "").strip()
        if not check:
            return ""

        match = re.search(r"(?:id|txn|tran|чек|операц)[^A-Za-z0-9]*([A-Za-z0-9_-]{6,})", check, flags=re.I)
        if match:
            return match.group(1)[:255]

        digest = hashlib.sha1(check.encode("utf-8")).hexdigest()[:12]
        return f"chk_{digest}"

    def _build_summary(
        self,
        *,
        payload: dict[str, Any],
        context: dict[str, Any],
        telegram_user: Optional[int],
    ) -> str:
        parts: list[str] = []
        summary = str(payload.get("summary") or "").strip()
        if summary:
            parts.append(summary)

        needs_text = str(payload.get("needs_text") or "").strip()
        if needs_text:
            parts.append(f"Запрос: {needs_text}")

        check = str(payload.get("payment_check") or "").strip()
        if check:
            parts.append(f"Чек: {check}")

        if telegram_user is not None:
            parts.append(f"TG: tg://user?id={telegram_user}")

        user_name = str(context.get("user_name") or "").strip()
        if user_name:
            parts.append(f"Клиент: {user_name}")

        source = str(payload.get("source") or context.get("source") or self.source_default).strip()
        if source:
            parts.append(f"Источник: {source}")

        return self._truncate("\n".join(parts), limit=2500)

    async def _build_lead_custom_fields(
        self,
        *,
        payload: dict[str, Any],
        context: dict[str, Any],
        telegram_user: Optional[int],
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        fields = await self._lead_fields()
        warnings: list[str] = []
        skipped: list[str] = []
        values_map: dict[int, dict[str, Any]] = {}

        def put_field(field_names: tuple[str, ...], raw_value: Any) -> None:
            label = field_names[0] if field_names else "unknown"
            if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                return

            field = self._find_field(fields, *field_names)
            if field is None:
                skipped.append(label)
                return

            field_id = self._safe_int(field.get("id"))
            field_type = str(field.get("type") or "").strip().lower()
            if field_id is None:
                skipped.append(label)
                return

            parsed_values: Optional[list[dict[str, Any]]] = None
            if field_type in {"select", "radiobutton", "category", "multiselect"}:
                enum_id = self._resolve_enum_id(field, raw_value)
                if enum_id is None:
                    warnings.append(f"Пропущено поле '{label}': не найден enum для значения '{raw_value}'.")
                    return
                parsed_values = [{"enum_id": int(enum_id)}]
            elif field_type in {"date", "date_time", "birthday"}:
                ts = self._to_unix_ts(raw_value)
                if ts is None:
                    warnings.append(f"Пропущено поле '{label}': не удалось распознать дату '{raw_value}'.")
                    return
                parsed_values = [{"value": int(ts)}]
            elif field_type in {"numeric", "price"}:
                numeric = self._to_int(raw_value)
                if numeric is None:
                    warnings.append(f"Пропущено поле '{label}': не удалось распознать число '{raw_value}'.")
                    return
                parsed_values = [{"value": int(numeric)}]
            elif field_type == "checkbox":
                parsed_values = [{"value": bool(raw_value)}]
            else:
                parsed_values = [{"value": self._truncate(raw_value, limit=2000)}]

            values_map[int(field_id)] = {
                "field_id": int(field_id),
                "values": parsed_values,
            }

        source_value = str(payload.get("source") or context.get("source") or self.source_default).strip()
        course_type = str(payload.get("course_type") or "Марафон").strip()
        payment_datetime = payload.get("payment_datetime") or datetime.now(self.timezone)
        summary_value = self._build_summary(payload=payload, context=context, telegram_user=telegram_user)
        transaction_id = self._derive_transaction_id(payload)

        put_field(("UTM_ID", "utm_id"), payload.get("utm_id"))
        put_field(("FORMID", "FORM_ID"), payload.get("form_id"))
        put_field(("TRANID", "TRAN_ID", "TRNID"), transaction_id)
        put_field(("Остаток",), payload.get("remainder"))
        put_field(("Дата и время оплаты",), payment_datetime)
        put_field(("Способ оплаты",), payload.get("payment_method"))
        put_field(("Регион",), payload.get("region"))
        put_field(("Источник",), source_value)
        put_field(("Дата начала марафона",), payload.get("marathon_start_date"))
        put_field(("Потребность",), payload.get("need_category"))
        put_field(("Пол",), payload.get("gender"))
        put_field(("Тип курса",), course_type)
        put_field(("Возраст",), payload.get("age_range"))
        put_field(("Саммари и ссылка на тг",), summary_value)

        return list(values_map.values()), warnings, skipped

    async def _upsert_lead(
        self,
        *,
        contact_id: int,
        lead_payload: dict[str, Any],
    ) -> tuple[int, bool]:
        leads = await self._leads_by_contact(contact_id)
        active = self._pick_active_lead(leads)

        if active is None:
            create_payload = dict(lead_payload)
            create_payload["_embedded"] = {"contacts": [{"id": int(contact_id)}]}
            data = await self._request("POST", "/api/v4/leads", payload=[create_payload], expected=(200,))
            created = self._extract_embedded_items(data, "leads")
            if not created:
                raise AmoCrmServiceError("amoCRM не вернул созданную сделку.")
            lead_id = self._safe_int(created[0].get("id"))
            if lead_id is None:
                raise AmoCrmServiceError("amoCRM вернул сделку без id.")
            return int(lead_id), True

        lead_id = self._safe_int(active.get("id"))
        if lead_id is None:
            raise AmoCrmServiceError("Найденная сделка не содержит id.")
        patch_payload = dict(lead_payload)
        patch_payload["id"] = int(lead_id)
        await self._request("PATCH", "/api/v4/leads", payload=[patch_payload], expected=(200,))
        return int(lead_id), False

    async def upsert_paid_lead(
        self,
        *,
        lead_payload: dict[str, Any],
        context: Optional[dict[str, Any]] = None,
        telegram_user: Optional[int] = None,
    ) -> dict[str, Any]:
        if not isinstance(lead_payload, dict):
            raise AmoCrmServiceError("lead_payload должен быть словарем.")
        context_data = dict(context or {})

        name = str(lead_payload.get("name") or "").strip()
        phone = str(lead_payload.get("phone") or "").strip()
        payment_check = str(lead_payload.get("payment_check") or "").strip()
        missing = [key for key, val in (("name", name), ("phone", phone), ("payment_check", payment_check)) if not val]
        if missing:
            raise ValueError(f"Недостаточно данных для call_me: {', '.join(missing)}")

        contact, contact_created = await self._upsert_contact(name=name, phone=phone)
        contact_id = self._safe_int(contact.get("id"))
        if contact_id is None:
            raise AmoCrmServiceError("Не удалось определить id контакта после upsert.")

        custom_fields, warnings, skipped_fields = await self._build_lead_custom_fields(
            payload=lead_payload,
            context=context_data,
            telegram_user=telegram_user,
        )

        lead_name = str(lead_payload.get("lead_name") or "").strip()
        if not lead_name:
            lead_name = f"{name} | оплата подтверждена"

        lead_payload_out: dict[str, Any] = {
            "name": self._truncate(lead_name, limit=230),
            "pipeline_id": int(self.pipeline_id),
            "status_id": int(self.status_id),
            "price": int(self._resolve_price(lead_payload)),
            "tags_to_add": [
                {"name": "CALL_ME_PAID"},
                {"name": "NUTRITION"},
            ],
        }
        if custom_fields:
            lead_payload_out["custom_fields_values"] = custom_fields
        if self.responsible_user_id is not None:
            lead_payload_out["responsible_user_id"] = int(self.responsible_user_id)

        lead_id, lead_created = await self._upsert_lead(
            contact_id=int(contact_id),
            lead_payload=lead_payload_out,
        )

        result = AmoUpsertResult(
            lead_id=int(lead_id),
            contact_id=int(contact_id),
            lead_created=bool(lead_created),
            contact_created=bool(contact_created),
            warnings=warnings,
            skipped_fields=skipped_fields,
            group_link=self.group_link,
        )
        return result.to_dict()


amocrm_service = AmoCrmService()
