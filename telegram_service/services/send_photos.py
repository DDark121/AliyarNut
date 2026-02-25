import base64
import io

from icecream import ic
from telethon import TelegramClient
from telethon.tl.types import InputPeerUser

from telegram_service.models.model import PhotoSendRequest


async def send_photos_task(client: TelegramClient, data: PhotoSendRequest) -> int:
    """
    Отправляет набор фото пользователю.
    Возвращает количество успешно отправленных файлов.
    """
    entity = InputPeerUser(user_id=data.user_id, access_hash=data.access_hash)
    photos = list(data.photos or [])
    if not photos:
        return 0

    streams: list[io.BytesIO] = []
    for photo in photos:
        raw = base64.b64decode(photo.content_base64)
        stream = io.BytesIO(raw)
        stream.name = photo.file_name
        streams.append(stream)

    sent = 0
    async with client.action(entity, "photo"):
        if len(streams) == 1:
            await client.send_file(entity=entity, file=streams[0], caption=data.description)
            sent = 1
        else:
            # Передаем список файлов одним запросом, чтобы Telegram отправил их альбомом.
            await client.send_file(entity=entity, file=streams, caption=data.description)
            sent = len(streams)

    ic(f"Services: Отправлено фото: {sent} для user_id={data.user_id}")
    return sent
