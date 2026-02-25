#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

def _load_env_fallback(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except Exception:
        return


if load_dotenv is not None:
    load_dotenv()
else:
    _load_env_fallback(".env")


TASHKENT_TZ = ZoneInfo("Asia/Tashkent")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    value: Any
    aliases: Tuple[str, ...] = ()


class AmoApiError(RuntimeError):
    pass


class AmoClient:
    def __init__(self, *, subdomain: str, access_token: str, timeout_sec: float = 30.0) -> None:
        self.subdomain = subdomain.strip()
        self.base_url = f"https://{self.subdomain}.amocrm.ru"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        )
        self.timeout_sec = timeout_sec

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Any] = None,
        expected: Iterable[int] = (200,),
    ) -> Any:
        url = f"{self.base_url}{path}"
        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=payload,
            timeout=self.timeout_sec,
        )
        if response.status_code not in set(expected):
            raise AmoApiError(
                f"{method} {path} failed with {response.status_code}\n"
                f"{response.text}"
            )

        if response.status_code == 204:
            return None
        if not response.text.strip():
            return None
        return response.json()

    def list_pipelines(self) -> List[Dict[str, Any]]:
        data = self._request("GET", "/api/v4/leads/pipelines")
        return list((data or {}).get("_embedded", {}).get("pipelines", []) or [])

    def list_lead_custom_fields(self) -> List[Dict[str, Any]]:
        page = 1
        limit = 250
        all_fields: List[Dict[str, Any]] = []

        while True:
            data = self._request(
                "GET",
                "/api/v4/leads/custom_fields",
                params={"page": page, "limit": limit, "order[sort]": "asc"},
            )
            current = list((data or {}).get("_embedded", {}).get("custom_fields", []) or [])
            if not current:
                break
            all_fields.extend(current)
            if len(current) < limit:
                break
            page += 1

        return all_fields

    def create_lead(self, lead_payload: Dict[str, Any]) -> Dict[str, Any]:
        data = self._request(
            "POST",
            "/api/v4/leads",
            payload=[lead_payload],
            expected=(200,),
        )
        leads = list((data or {}).get("_embedded", {}).get("leads", []) or [])
        if not leads:
            raise AmoApiError(f"POST /api/v4/leads returned unexpected payload: {json.dumps(data, ensure_ascii=False)}")
        return leads[0]

    def get_lead(self, lead_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/api/v4/leads/{int(lead_id)}")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "", str(text or "").strip().lower())


def _extract_sub_from_jwt(token: str) -> Optional[int]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        data = json.loads(decoded)
    except Exception:
        return None
    value = data.get("sub")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_label() -> str:
    return datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _optional_env_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Переменная окружения {name} должна быть числом, получено: {raw!r}") from exc


def _to_unix_ts(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return int(raw)
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Невалидный формат даты: {value!r}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TASHKENT_TZ)
        return int(dt.timestamp())

    raise ValueError(f"Не удалось преобразовать значение времени: {value!r}")


def _default_specs() -> List[FieldSpec]:
    now = datetime.now(TASHKENT_TZ)
    return [
        FieldSpec(name="UTM_ID", value=f"utm_api_{now.strftime('%Y%m%d_%H%M%S')}"),
        FieldSpec(name="FORMID", value="api_form_001", aliases=("FORM_ID",)),
        FieldSpec(name="TRANID", value=f"txn_{now.strftime('%Y%m%d%H%M%S')}", aliases=("TRAN_ID", "TRNID")),
        FieldSpec(name="Остаток", value="0"),
        FieldSpec(name="Дата и время оплаты", value=int((now + timedelta(hours=1)).timestamp())),
        FieldSpec(name="Способ оплаты", value="Наличные"),
        FieldSpec(name="Регион", value="Ташкент"),
        FieldSpec(name="Источник", value="Instagram"),
        FieldSpec(name="Дата начала марафона", value=int((now + timedelta(days=3)).timestamp())),
        FieldSpec(name="Потребность", value="Марафон"),
        FieldSpec(name="Пол", value="Женский"),
        FieldSpec(name="Тип курса", value="Марафон"),
        FieldSpec(name="Возраст", value="18-24"),
        FieldSpec(name="Саммари и ссылка на тг", value="Лид создан через API. TG: https://t.me/farowayschool"),
    ]


def _find_field(fields: List[Dict[str, Any]], spec: FieldSpec) -> Optional[Dict[str, Any]]:
    normalized = [_normalize(spec.name), *[_normalize(alias) for alias in spec.aliases]]
    normalized = [x for x in normalized if x]

    for field in fields:
        candidate = _normalize(field.get("name") or "")
        if candidate in normalized:
            return field

    for key in normalized:
        for field in fields:
            candidate = _normalize(field.get("name") or "")
            if not candidate:
                continue
            if key in candidate or candidate in key:
                return field

    return None


def _resolve_enum(field: Dict[str, Any], desired: Any) -> Tuple[int, str, bool]:
    enums = list(field.get("enums") or [])
    if not enums:
        raise ValueError(f"Поле '{field.get('name')}' не содержит enum значений")

    enum_by_id = {int(item["id"]): item for item in enums if isinstance(item.get("id"), int)}

    if isinstance(desired, int) and desired in enum_by_id:
        item = enum_by_id[desired]
        return int(item["id"]), str(item.get("value") or ""), False

    desired_norm = _normalize(str(desired or ""))
    for item in enums:
        value_norm = _normalize(item.get("value") or "")
        code_norm = _normalize(item.get("code") or "")
        if desired_norm and (desired_norm == value_norm or desired_norm == code_norm):
            return int(item["id"]), str(item.get("value") or ""), False

    for item in enums:
        value_norm = _normalize(item.get("value") or "")
        if desired_norm and desired_norm in value_norm:
            return int(item["id"]), str(item.get("value") or ""), False

    fallback = enums[0]
    return int(fallback["id"]), str(fallback.get("value") or ""), True


def _build_field_values(field: Dict[str, Any], raw_value: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    field_type = str(field.get("type") or "").strip().lower()

    if field_type in {"select", "multiselect", "radiobutton", "category"}:
        enum_id, enum_value, used_fallback = _resolve_enum(field, raw_value)
        return (
            [{"enum_id": enum_id}],
            {
                "kind": "enum",
                "field_type": field_type,
                "expected": enum_id,
                "resolved": enum_value,
                "fallback": used_fallback,
            },
        )

    if field_type in {"date", "date_time", "birthday"}:
        ts = _to_unix_ts(raw_value)
        return (
            [{"value": ts}],
            {
                "kind": "value",
                "field_type": field_type,
                "expected": ts,
                "resolved": ts,
                "fallback": False,
            },
        )

    if field_type == "checkbox":
        value = bool(raw_value)
        return (
            [{"value": value}],
            {
                "kind": "value",
                "field_type": field_type,
                "expected": value,
                "resolved": value,
                "fallback": False,
            },
        )

    if field_type in {"numeric", "price"} and isinstance(raw_value, str) and raw_value.strip().isdigit():
        value = int(raw_value.strip())
        return (
            [{"value": value}],
            {
                "kind": "value",
                "field_type": field_type,
                "expected": value,
                "resolved": value,
                "fallback": False,
            },
        )

    return (
        [{"value": raw_value}],
        {
            "kind": "value",
            "field_type": field_type,
            "expected": raw_value,
            "resolved": raw_value,
            "fallback": False,
        },
    )


def build_custom_fields_payload(
    *,
    fields: List[Dict[str, Any]],
    specs: List[FieldSpec],
    strict: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    payload: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for spec in specs:
        field = _find_field(fields, spec)
        if field is None:
            message = f"Поле не найдено в amoCRM: {spec.name}"
            if strict:
                raise ValueError(message)
            warnings.append(message)
            continue

        values, meta = _build_field_values(field, spec.value)
        payload.append({"field_id": int(field["id"]), "values": values})

        check = {
            "field_id": int(field["id"]),
            "field_name": str(field.get("name") or spec.name),
            "kind": meta["kind"],
            "field_type": meta.get("field_type") or str(field.get("type") or "").strip().lower(),
            "expected": meta["expected"],
        }
        checks.append(check)

        if meta.get("fallback"):
            warnings.append(
                f"Для поля '{field.get('name')}' значение '{spec.value}' не найдено. "
                f"Использован fallback enum: '{meta.get('resolved')}' (id={meta.get('expected')})."
            )

    return payload, checks, warnings


def verify_created_lead(lead: Dict[str, Any], checks: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    cf_values = list(lead.get("custom_fields_values") or [])
    by_field_id: Dict[int, Dict[str, Any]] = {}
    for item in cf_values:
        if not isinstance(item, dict):
            continue
        field_id = item.get("field_id")
        if isinstance(field_id, int):
            by_field_id[field_id] = item

    ok = True
    lines: List[str] = []

    for check in checks:
        field_id = int(check["field_id"])
        field_name = str(check["field_name"])
        expected = check["expected"]
        kind = check["kind"]
        field_type = str(check.get("field_type") or "").strip().lower()

        item = by_field_id.get(field_id)
        if item is None:
            ok = False
            lines.append(f"FAIL {field_name} (id={field_id}): поле отсутствует в созданной сделке")
            continue

        values = list(item.get("values") or [])
        if kind == "enum":
            enum_ids = [v.get("enum_id") for v in values if isinstance(v, dict) and "enum_id" in v]
            if expected not in enum_ids:
                ok = False
                lines.append(
                    f"FAIL {field_name} (id={field_id}): ожидался enum_id={expected}, получено {enum_ids}"
                )
                continue
            lines.append(f"OK   {field_name} (id={field_id}): enum_id={expected}")
            continue

        matched = False
        for value in values:
            if not isinstance(value, dict):
                continue
            if "value" not in value:
                continue
            got = value["value"]
            if field_type in {"date", "date_time", "birthday"}:
                try:
                    got_dt = datetime.fromtimestamp(int(got), tz=TASHKENT_TZ).date()
                    exp_dt = datetime.fromtimestamp(int(expected), tz=TASHKENT_TZ).date()
                    matched = got_dt == exp_dt
                except Exception:
                    matched = False
            elif isinstance(expected, (int, float)) and isinstance(got, (int, float)):
                matched = int(got) == int(expected)
            else:
                matched = str(got).strip() == str(expected).strip()
            if matched:
                break

        if not matched:
            ok = False
            got_values = [v.get("value") for v in values if isinstance(v, dict) and "value" in v]
            lines.append(
                f"FAIL {field_name} (id={field_id}): ожидалось value={expected!r}, получено {got_values!r}"
            )
            continue

        lines.append(f"OK   {field_name} (id={field_id})")

    return ok, lines


def print_pipelines(pipelines: List[Dict[str, Any]]) -> None:
    print("=== Доступные воронки и этапы ===")
    for pipeline in pipelines:
        print(f"\nВоронка: {pipeline.get('name')} (ID: {pipeline.get('id')})")
        for status in list(pipeline.get("_embedded", {}).get("statuses", []) or []):
            print(f"  - Этап: {status.get('name')} (ID: {status.get('id')})")


def print_fields(fields: List[Dict[str, Any]]) -> None:
    print("\n=== Кастомные поля сделок ===")
    for field in sorted(fields, key=lambda item: str(item.get("name") or "").lower()):
        enums = list(field.get("enums") or [])
        enum_preview = ""
        if enums:
            names = [str(item.get("value") or "") for item in enums[:6]]
            suffix = " ..." if len(enums) > 6 else ""
            enum_preview = f" | options: {', '.join(names)}{suffix}"
        print(
            f"- {field.get('name')} "
            f"(id={field.get('id')}, type={field.get('type')}){enum_preview}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Создание сделки в amoCRM с автозаполнением custom fields")
    parser.add_argument("--subdomain", default=os.getenv("AMO_SUBDOMAIN", "farowayschool"))
    parser.add_argument("--token", default=os.getenv("AMO_ACCESS_TOKEN"))
    parser.add_argument("--pipeline-id", type=int, default=int(os.getenv("AMO_PIPELINE_ID", "9873894")))
    parser.add_argument("--status-id", type=int, default=int(os.getenv("AMO_STATUS_ID", "78511606")))
    parser.add_argument("--price", type=int, default=120000)
    parser.add_argument("--lead-name", default=f"API Сделка {_now_label()}")
    parser.add_argument("--list-only", action="store_true", help="Только вывести воронки/этапы и выйти")
    parser.add_argument("--list-fields", action="store_true", help="Вывести custom fields сделок")
    parser.add_argument("--strict-fields", action="store_true", help="Падать, если не найдено хоть одно поле")
    parser.add_argument("--no-verify", action="store_true", help="Не выполнять проверку после создания")
    parser.add_argument("--responsible-user-id", type=int, default=_optional_env_int("AMO_RESPONSIBLE_USER_ID"))
    args = parser.parse_args()

    if not args.token:
        print("Ошибка: передайте токен через --token или AMO_ACCESS_TOKEN", file=sys.stderr)
        return 2

    client = AmoClient(subdomain=args.subdomain, access_token=args.token)

    try:
        pipelines = client.list_pipelines()
        print_pipelines(pipelines)

        fields = client.list_lead_custom_fields()
        print(f"\nНайдено custom fields для сделок: {len(fields)}")
        if args.list_fields:
            print_fields(fields)

        if args.list_only:
            return 0

        specs = _default_specs()
        custom_fields_values, checks, warnings = build_custom_fields_payload(
            fields=fields,
            specs=specs,
            strict=args.strict_fields,
        )

        if warnings:
            print("\nПредупреждения по маппингу полей:")
            for warning in warnings:
                print(f"- {warning}")

        payload = {
            "name": args.lead_name,
            "price": args.price,
            "pipeline_id": args.pipeline_id,
            "status_id": args.status_id,
            "created_by": 0,
            "custom_fields_values": custom_fields_values,
            "tags_to_add": [
                {"name": "API_Тест"},
                {"name": "CALL_ME_PREP"},
            ],
        }

        expected_responsible = args.responsible_user_id or _extract_sub_from_jwt(args.token)
        if expected_responsible is not None:
            payload["responsible_user_id"] = int(expected_responsible)
            print(f"Ответственный установлен: responsible_user_id={expected_responsible}")

        created = client.create_lead(payload)
        lead_id = int(created["id"])
        print(f"\nСделка создана: ID={lead_id}")

        if args.no_verify:
            return 0

        saved = client.get_lead(lead_id)
        ok, report_lines = verify_created_lead(saved, checks)

        print("\nПроверка заполнения полей:")
        for line in report_lines:
            print(line)

        if expected_responsible is not None:
            current_responsible = saved.get("responsible_user_id")
            if int(current_responsible or 0) == int(expected_responsible):
                print(f"OK   Ответственный: responsible_user_id={current_responsible}")
            else:
                ok = False
                print(
                    "FAIL Ответственный: "
                    f"ожидался responsible_user_id={expected_responsible}, "
                    f"получено {current_responsible}"
                )

        if ok:
            print("\nИтог: все целевые поля заполнены успешно.")
            return 0

        print("\nИтог: есть незаполненные/некорректные поля.")
        return 1

    except AmoApiError as exc:
        print(f"\nОшибка API:\n{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nНепредвиденная ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
