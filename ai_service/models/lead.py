from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class LeadCard(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Contact (required at payment-final step)
    name: Optional[str] = Field(None, description="Имя клиента")
    phone: Optional[str] = Field(None, description="Телефон клиента")
    language: Optional[str] = Field(None, description="Язык общения: ru/uz/en")
    preferred_channel: Optional[str] = Field(None, description="Канал: telegram/whatsapp/phone")

    # Funnel qualification
    needs_text: Optional[str] = Field(None, description="Главный запрос клиента")
    need_category: Optional[str] = Field(None, description="Категория потребности")
    urgency: Optional[str] = Field(None, description="Срочность запроса")
    objection: Optional[str] = Field(None, description="Текущее возражение")

    # Payment data (mandatory for call_me success)
    payment_check: Optional[str] = Field(None, description="Ссылка/описание/ID чека")
    payment_transaction_id: Optional[str] = Field(None, description="ID транзакции")
    payment_amount_sum: Optional[int] = Field(None, description="Сумма оплаты в сумах")
    payment_method: Optional[str] = Field(None, description="Способ оплаты")
    payment_datetime: Optional[datetime] = Field(None, description="Дата и время оплаты")

    # CRM optional fields
    marathon_start_date: Optional[str] = Field(None, description="Дата старта марафона")
    region: Optional[str] = Field(None, description="Регион")
    gender: Optional[str] = Field(None, description="Пол")
    age_range: Optional[str] = Field(None, description="Возрастная категория")
    course_type: Optional[str] = Field(None, description="Тип курса")
    summary: Optional[str] = Field(None, description="Краткое summary для CRM")
    source: Optional[str] = Field(None, description="Источник лида")
    utm_id: Optional[str] = Field(None, description="UTM ID")
    form_id: Optional[str] = Field(None, description="Form ID")

    # Extra metadata
    raw_transcript: Optional[str] = None
