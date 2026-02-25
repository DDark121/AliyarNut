from __future__ import annotations

import os

from langchain_openai import ChatOpenAI
from langmem import create_memory_manager

from ai_service.models.lead import LeadCard


def get_openrouter_llm(
    model: str = "google/gemini-2.5-flash-preview-09-2025",
    temperature: float = 0.2,
) -> ChatOpenAI:
    """Создает экземпляр LLM для OpenRouter."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "Нет OPENROUTER_API_KEY в .env"

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=temperature,
    )


def create_langmem_manager(schemas: list) -> "MemoryManager":
    """Создает менеджер памяти (langmem) для извлечения структуры лида."""
    model_structured_output = get_openrouter_llm(
        model="google/gemini-2.5-flash-preview-09-2025"
    )

    manager = create_memory_manager(
        model_structured_output,
        schemas=schemas,
        instructions=(
            "Extract and update lead memory for nutrition sales funnel. "
            "Always prioritize exact extraction of required fields: "
            "name, phone, payment_check. "
            "If payment info appears, capture payment_transaction_id, payment_amount_sum, "
            "payment_method, payment_datetime. "
            "Capture optional CRM details when present: marathon_start_date, region, "
            "need_category, gender, age_range, source, summary, utm_id, form_id. "
            "Do not invent values. Keep existing known values unless user explicitly changes them. "
            "Language can be Russian, Uzbek, or English."
        ),
        enable_inserts=True,
        enable_updates=True,
        enable_deletes=True,
    )
    return manager


langmem_manager = create_langmem_manager(schemas=[LeadCard])
