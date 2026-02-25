from __future__ import annotations

from typing import Optional


def normalize_thread_id(thread_id: Optional[str | int], prefix: str = "telegram") -> Optional[str]:
    if thread_id is None:
        return None
    raw = str(thread_id).strip()
    if not raw:
        return None
    expected_prefix = f"{prefix}_"
    if raw.startswith(expected_prefix):
        return raw
    if raw.isdigit():
        return f"{prefix}_{raw}"
    return f"{prefix}_{raw}"
