from icecream import ic
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from telethon import TelegramClient
from telethon import types, functions
from telethon.tl.types import InputPeerUser
from telegram_service.models.model import ReactionRequest
from fastapi import Request
from telegram_service.models.model import GeoRequest
from telegram_service.services.send_geo import send_office_location_task # Импортируем функцию
from telegram_service.models.model import PhotoSendRequest
from telegram_service.models.model import SendMessageRequest
from telegram_service.models.model import ResolveUserRequest
from telegram_service.services.send_photos import send_photos_task
from telegram_service.telegram_client import start_telegram_service
from telegram_service.core.uptime import uptime_monitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    ic("FastAPI Lifespan: ЗАПУСК...")
    telegram_client: TelegramClient | None = None
    try:
        telegram_client = await start_telegram_service()

        app.state.telegram_client = telegram_client

        yield  
        
    finally:
        ic("FastAPI Lifespan: ОСТАНОВКА...")
        if telegram_client and telegram_client.is_connected():
            await telegram_client.disconnect()
        await uptime_monitor.stop()

app = FastAPI(lifespan=lifespan)



@app.post("/send-reaction")
async def send_reaction_endpoint(payload: ReactionRequest, request: Request):
    """
    Эндпоинт, который принимает команду от Агента и ставит реакцию через Telethon.
    """
    ic(f"API: Запрос на реакцию для {payload.user_id}, msg={payload.message_id}, emo={payload.emotion}")
    
    client: TelegramClient = request.app.state.telegram_client

    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client not connected")

    try:
        # 1. Создаем объект пользователя (кому ставим реакцию)
        # Важно: access_hash обязателен, иначе Telethon не найдет юзера
        entity = InputPeerUser(
            user_id=payload.user_id, 
            access_hash=payload.access_hash
        )

        # 2. Отправляем запрос в Telegram
        await client(functions.messages.SendReactionRequest(
            peer=entity,
            msg_id=payload.message_id,
            reaction=[types.ReactionEmoji(emoticon=payload.emotion)],
            big=False, # True для большой анимации, False для обычной
            add_to_recent=True
        ))
        
        ic("API: Реакция успешно поставлена")
        return {"status": "ok", "detail": f"Reaction {payload.emotion} set"}

    except Exception as e:
        ic(f"API Error sending reaction: {e}")
        # Возвращаем 500, чтобы агент знал, что что-то пошло не так
        raise HTTPException(status_code=500, detail=str(e))
    


@app.post("/send-office-location")
async def send_office_location_endpoint(payload: GeoRequest, request: Request):
    client: TelegramClient = request.app.state.telegram_client

    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client not connected")

    try:
        await send_office_location_task(client, payload)
        return {"status": "ok", "msg": "Location sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send-photos")
async def send_photos_endpoint(payload: PhotoSendRequest, request: Request):
    client: TelegramClient = request.app.state.telegram_client

    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client not connected")

    try:
        sent = await send_photos_task(client, payload)
        return {"status": "ok", "sent": sent}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send-message")
async def send_message_endpoint(payload: SendMessageRequest, request: Request):
    client: TelegramClient = request.app.state.telegram_client

    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client not connected")

    try:
        await client.send_message(int(payload.user_id), payload.message)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/resolve-user-name")
async def resolve_user_name_endpoint(payload: ResolveUserRequest, request: Request):
    client: TelegramClient = request.app.state.telegram_client

    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client not connected")

    try:
        entity = await client.get_entity(int(payload.user_id))
        username = str(getattr(entity, "username", "") or "").strip()
        if username:
            return {"status": "ok", "display_name": f"@{username}"}

        first = str(getattr(entity, "first_name", "") or "").strip()
        last = str(getattr(entity, "last_name", "") or "").strip()
        title = str(getattr(entity, "title", "") or "").strip()
        full = " ".join(part for part in (first, last) if part).strip()
        display_name = full or title or str(payload.user_id)
        return {"status": "ok", "display_name": display_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
