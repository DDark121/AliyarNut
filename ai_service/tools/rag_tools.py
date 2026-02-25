from __future__ import annotations

from icecream import ic
from langchain_core.tools import tool

from ai_service.services.rag_knowledge_service import RagKnowledgeError, rag_knowledge_service


def _short_text(value: str, limit: int = 800) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


@tool
async def search_docs_knowledge(query: str, top_k: int = 4) -> str:
    """Поиск по локальной базе документов (RAG) из data/docs.

    Используй этот tool, когда вопрос требует факты из документов:
    - описание тренингов и программ;
    - методика, шаги, кейсы, возражения;
    - экспертность и позиционирование Умиды;
    - отеки, акне, пигментация, уходовые рецепты.
    """
    try:
        ic(f"RAG CALL search_docs_knowledge query={query!r} top_k={top_k}")
        result = await rag_knowledge_service.search(query=query, top_k=top_k)
        ic(f"RAG RESULT search_docs_knowledge: {_short_text(result)}")
        return result
    except ValueError as exc:
        ic(f"RAG RESULT search_docs_knowledge error: {exc}")
        return f"Не удалось выполнить поиск по базе знаний: {exc}"
    except RagKnowledgeError as exc:
        ic(f"RAG RESULT search_docs_knowledge error: {exc}")
        return str(exc)
    except Exception as exc:
        ic(f"search_docs_knowledge failed: {exc}")
        return "Не удалось выполнить поиск по базе знаний из-за внутренней ошибки."


@tool
async def rebuild_docs_knowledge_index() -> str:
    """Принудительно пересобрать RAG-индекс из data/docs.

    Используй только когда пользователь явно просит переиндексировать документы.
    """
    try:
        return await rag_knowledge_service.rebuild()
    except RagKnowledgeError as exc:
        return f"Не удалось пересобрать индекс: {exc}"
    except Exception as exc:
        ic(f"rebuild_docs_knowledge_index failed: {exc}")
        return "Не удалось пересобрать индекс базы знаний из-за внутренней ошибки."
