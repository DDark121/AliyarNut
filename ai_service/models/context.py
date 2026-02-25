from dataclasses import dataclass
from typing import Optional

@dataclass
class ContextSchema:
    user_name: Optional[str] = None
    peer_id: Optional[int] = None
    access_hash: Optional[int] = None
    lead_id: Optional[int] = None
    chat_id: Optional[int] = None
    source: Optional[str] = None
    source_id: Optional[str] = None
    current_datetime: Optional[str] = None
    current_date: Optional[str] = None
    current_time: Optional[str] = None
    current_timezone: Optional[str] = None
