from telethon import events, client, TelegramClient,functions, types
from telegram_service.filters import filter_chat_type
from asyncio import sleep, create_task, CancelledError
from collections import defaultdict
from icecream import ic
import random
from telethon.tl.types import MessageMediaGeo, MessageMediaGeoLive
from telethon.tl.types import MessageMediaContact
from telegram_service.agent_service import agent_service, _get_entity_dict
import base64
from io import BytesIO
user_timers = {}
user_rating_timers = {}
user_buffers = defaultdict(list)
send_rating_filter = {}

BUFFER_DELAY_MIN_SEC = 3.0
BUFFER_DELAY_MAX_SEC = 4.0
SPLIT_MESSAGE_DELAY_SEC = 0.4




def register(client: TelegramClient):
    @client.on(events.NewMessage)
    @filter_chat_type(["private"])

    async def message_handler(event):
        try:
            user_id = event.sender_id
            entity = await event.get_sender()

            if event.text == "/delete_my_thread":
                try:
                    user_id_str = str(user_id)
                    
                    # Отправляем запрос на удаление в микросервис
                    success = await agent_service.delete_thread(user_id_str)
                    
                    if success:
                         await client.send_message(user_id, "Миссия выполнена 🫡 я теперь тебя не знаю 😁")
                         # Очищаем локальные буферы бота, чтобы старые сообщения не улетели в новый диалог
                         user_buffers.pop(user_id, None)
                    else:
                         await client.send_message(user_id, "История уже пуста или произошла ошибка 🤷‍♂️")
                         
                except Exception as ex:
                    # sentry_sdk.capture_exception(ex)
                    ic(f"Ошибка удаления: {ex}")
                    await client.send_message(user_id, "я короче чет пошаманил и не работает. Сори лапки 🐾")
                
                return
            
            if event.out:
                return 

            if user_id in user_timers:
                user_timers[user_id].cancel()
            if user_id in user_rating_timers:
                user_rating_timers[user_id].cancel()
            if event.text:
                reply_text = ""
                if event.reply_to_msg_id:  
                    me = await client.get_me()
                    reply_msg = await event.get_reply_message()
                    if reply_msg:
                        reply_author = "меня" if reply_msg.sender_id == me.id else "клиента"
                        reply_content = reply_msg.raw_text or "[вложение/медиа]"
                        reply_text = f"(в ответ на сообщение от {reply_author}: {reply_content}) "

                user_buffers[user_id].append(reply_text + event.raw_text + f", message_id:{event.id}")
                ic(f"Добавлен текст от {user_id}: {reply_text}{event.raw_text}, message_id:{event.id}")
            

            if event.photo:
                try:
                    # 1. Скачиваем фото в память
                    photo_stream = BytesIO()
                    await client.download_media(event.photo, file=photo_stream)
                    photo_stream.seek(0)
                    
                    # 2. Кодируем байты в строку Base64
                    b64_image = base64.b64encode(photo_stream.read()).decode('utf-8')
                    photo_stream.close()

                    # 3. Отправляем строку в сервис
                    # ВАЖНО: передаем user_id с префиксом, чтобы не было коллизий
                    response = await agent_service.get_photo_description(b64_image, str(user_id))
                    
                    # 4. Достаем текст из ответа
                    photo_descr = response.get("message", "Нет описания")
                    
                    user_buffers[user_id].append(f"[Система: Пользователь прислал фото. Описание: {photo_descr}]")
                    ic(f"Фото описано: {photo_descr}")
                except Exception as ex:
                    ic(f"Ошибка обработки фото: {ex}")


            # === ОБРАБОТКА ГОЛОСОВЫХ ===
            # Проверяем наличие голосового (Telethon удобен тем, что event.voice работает и для event.message.voice)
            if event.voice: 
                try:
                    # 1. Скачиваем голосовое в память
                    voice_stream = BytesIO()
                    await client.download_media(event.voice, file=voice_stream)
                    voice_stream.seek(0)
                    
                    # 2. Кодируем в строку Base64
                    b64_voice = base64.b64encode(voice_stream.read()).decode('utf-8')
                    voice_stream.close()

                    # 3. Отправляем строку в сервис
                    response = await agent_service.stt(b64_voice, str(user_id))
                    
                    # 4. Достаем текст
                    voice_text = response.get("message", "Не удалось распознать")
                    
                    user_buffers[user_id].append(f"[Система: Пользователь прислал голосовое сообщение. Текст: {voice_text}]")
                    ic(f"Голосовое переведено: {voice_text}")
                except Exception as ex:
                    ic(f"Ошибка обработки голосового: {ex}")


            if isinstance(event.media, MessageMediaContact):
                contact = event.media
                user_buffers[user_id].append(f"Получен контакт: {contact.first_name}, {contact.last_name}, {contact.phone_number}")

            if isinstance(event.media, MessageMediaGeo):
                geo = event.media.geo
                lat = geo.lat
                lon = geo.long
                user_buffers[user_id].append(f"Получена геолокация: {lat}, {lon},")


            elif isinstance(event.media, MessageMediaGeoLive):
                geo = event.media.geo
                lat = geo.lat
                lon = geo.long
                ic("Живая геолокация:", lat, lon)


            await event.mark_read()
            thread_id = f"telegram_{user_id}"
            context = _get_entity_dict(entity)
            context["source"] = "telegram"
            context["source_id"] = user_id

            user_timers[user_id] = create_task(
                start_processing(
                    entity,
                    user_id,
                    client,
                    immediate=False,
                    thread_id=thread_id,
                    context=context,
                )
            )


        except Exception as ex:
            ic(ex)



async def start_processing(
    entity,
    user_id,
    client: TelegramClient,
    *,
    immediate: bool = False,
    thread_id: str | None = None,
    context: dict | None = None,
):
    cleanup_batch = False
    cancelled = False
    try:
        if not immediate:
            buffer_delay_sec = random.uniform(BUFFER_DELAY_MIN_SEC, BUFFER_DELAY_MAX_SEC)
            async with client.action(entity, "typing"):
                await sleep(buffer_delay_sec)

        combined_data = "\n".join(user_buffers[user_id])
        if not combined_data.strip():
            return
        cleanup_batch = True

        if not thread_id:
            thread_id = f"telegram_{user_id}"
        if context is None:
            context = _get_entity_dict(entity)
            context["source"] = "telegram"
            context["source_id"] = user_id

        reply = await agent_service.ask(combined_data, thread_id, context)
        ic(reply)

        message_text = reply.get("message", "")
        if not message_text:
            return

        if "~" in message_text:
            replies = [r.strip() for r in message_text.split("~") if r.strip()]
        else:
            replies = [message_text.strip()]

        if replies:
            for idx, msg in enumerate(replies):
                await client.send_message(user_id, msg)
                if idx < len(replies) - 1:
                    await sleep(SPLIT_MESSAGE_DELAY_SEC)
    except CancelledError:
        # Таймер мог быть отменен новым сообщением: буфер сохраняем для следующего батча.
        cancelled = True
        raise

    except Exception as e:
        ic(f"Ошибка обработки для {user_id}: {e}")
    finally:
        if cleanup_batch and not cancelled:
            user_buffers.pop(user_id, None)
            user_timers.pop(user_id, None)



async def SendTelegramMessage(client,user_id, message):
        if "~" in message:
            replies = message.split("~")
        else:
            replies = [message]

        for idx, line in enumerate(replies):
            line = line.strip()
            if line:  # Проверяем, что строка не пустая
                await client.send_message(user_id, line)
                if idx < len(replies) - 1:
                    await sleep(SPLIT_MESSAGE_DELAY_SEC)
                
