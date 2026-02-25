# telegram_client.py

import os
import asyncio
from telethon import TelegramClient
from icecream import ic
from pathlib import Path
from telegram_service.core.uptime import uptime_monitor

from telegram_service.handlers import register_handlers


API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

SESSION_DIR = Path("/data/telegram_sessions")
SESSION_DIR.mkdir(parents=True, exist_ok=True)

session_path = SESSION_DIR / os.getenv("TELEGRAM_SESSION")


async def start_telegram_service() -> TelegramClient:
    
    client = None
    try:
        ic("🚀 Запуск Telethon-сервиса...")
        ic(session_path, API_HASH, API_ID)
        client = TelegramClient(str(session_path), API_ID, API_HASH)

        await client.start()
        register_handlers(client)
        me = await client.get_me()
        ic(f"🤖 Telegram запущен как @{me.username} ({me.id})")

        asyncio.create_task(uptime_monitor.start(interval_sec=10))

        return client



    except Exception as ex:
        ic("❌ Ошибка Telethon", ex)
        if client and client.is_connected():
            await client.disconnect()
        await uptime_monitor.stop()
        raise
