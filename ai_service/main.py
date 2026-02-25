from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from icecream import ic
from pydantic import BaseModel, Field

from ai_service.memory.database import ensure_db_ready
from ai_service.services.chat_service import chat_service
from ai_service.services.amocrm_service import amocrm_service
from ai_service.services.media_service import media_service
from ai_service.services.reminder_scheduler import reminder_scheduler


class MediaRequest(BaseModel):
    file_base64: str
    thread_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)


class AnswerRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Текст от пользователя.")
    thread_id: Optional[str] = Field(None, description="ID сессии для продолжения диалога.")
    context: Dict[str, Any] = Field(default_factory=dict, description="Контекст с данными для ContextSchema.")


class AnswerResponse(BaseModel):
    thread_id: str
    message: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ensure_db_ready()
    except Exception as exc:
        ic(f"Ошибка подключения к БД: {exc}")

    try:
        from ai_service.services.rag_knowledge_service import RagKnowledgeError, rag_knowledge_service

        rag_state = await rag_knowledge_service.ensure_index()
        if rag_state.get("rebuilt"):
            ic(
                "RAG индекс подготовлен при старте "
                f"(файлов: {rag_state.get('files_count')}, чанков: {rag_state.get('chunks_count')})."
            )
        else:
            ic(
                "RAG индекс уже готов при старте "
                f"(файлов: {rag_state.get('files_count')})."
            )
    except (ValueError, RagKnowledgeError) as exc:
        ic(f"RAG warmup skipped: {exc}")
    except Exception as exc:
        ic(f"Ошибка прогрева RAG индекса при старте: {exc}")

    try:
        await reminder_scheduler.start()
    except Exception as exc:
        ic(f"Ошибка запуска reminder scheduler: {exc}")

    yield

    try:
        await reminder_scheduler.stop()
    except Exception as exc:
        ic(f"Ошибка остановки reminder scheduler: {exc}")

    try:
        await amocrm_service.aclose()
    except Exception as exc:
        ic(f"Ошибка остановки amoCRM client: {exc}")


app = FastAPI(
    title="LangGraph Agent",
    version="0.1.0",
    description="Сервис, который управляет LangGraph агентом и историями из Postgres.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/answer", response_model=AnswerResponse)
async def answer(request: AnswerRequest) -> AnswerResponse:
    try:
        thread_id, response_text = await chat_service.process_message(
            message=request.message,
            thread_id=request.thread_id,
            context=request.context,
        )
        return AnswerResponse(thread_id=thread_id, message=response_text)
    except Exception as exc:
        ic(f"Chat Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/stt_description", response_model=AnswerResponse)
async def stt_description(request: MediaRequest) -> AnswerResponse:
    try:
        text = await media_service.transcribe_audio(request.file_base64)
        return AnswerResponse(thread_id=request.thread_id or "audio", message=text)
    except Exception as exc:
        ic(f"STT Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/photo_description", response_model=AnswerResponse)
async def photo_description(request: MediaRequest) -> AnswerResponse:
    try:
        description = await media_service.describe_photo(request.file_base64)
        return AnswerResponse(thread_id=request.thread_id or "photo", message=description)
    except Exception as exc:
        ic(f"Photo Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/delete_thread", response_model=bool)
async def delete_thread(request: AnswerRequest) -> bool:
    try:
        if not request.thread_id:
            return False
        return await chat_service.delete_history(request.thread_id)
    except Exception as exc:
        ic(f"Delete Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
