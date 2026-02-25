from telethon import TelegramClient
from telethon.tl.types import InputPeerUser, InputMediaGeoPoint, InputGeoPoint
from telegram_service.models.model import GeoRequest
from icecream import ic

# Координаты офиса (хардкодим здесь, или выносим в .env)
OFFICE_LAT = 41.320007
OFFICE_LONG = 69.289234
OFFICE_ADDRESS = "Абдулла Кадыри 58А, Ташкент, Узбекистан"

async def send_office_location_task(client: TelegramClient, data: GeoRequest):
    """
    Отправляет геолокацию офиса конкретному пользователю.
    """
    try:
        entity = InputPeerUser(
            user_id=data.user_id, 
            access_hash=data.access_hash
        )

        ic(f"Services: Отправка гео для {data.user_id}")

        latitude = float(data.latitude) if data.latitude is not None else OFFICE_LAT
        longitude = float(data.longitude) if data.longitude is not None else OFFICE_LONG
        caption = str(data.caption or OFFICE_ADDRESS)

        # Эмуляция действия "send location..." (надпись в чате)
        async with client.action(entity, "location"):
            await client.send_file(
                entity=entity, 
                file=InputMediaGeoPoint(
                    geo_point=InputGeoPoint(
                        lat=latitude,
                        long=longitude
                    )
                ),
                caption=caption
            )
        
        return True

    except Exception as e:
        ic(f"Services Error (Geo): {e}")
        raise e
