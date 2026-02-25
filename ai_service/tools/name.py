
def _calculate_safe_time(self, minutes: float) -> datetime:
    """Рассчитывает безопасное время (8:00-22:00) для отправки."""
    tz = ZoneInfo("Asia/Tashkent")
    now = datetime.now(tz)
    planned = now + timedelta(minutes=float(minutes))

    earliest = planned.replace(hour=8, minute=0, second=0, microsecond=0)
    latest = planned.replace(hour=22, minute=0, second=0, microsecond=0)
    
    # Если поздно, переносим на 8 утра завтра
    if planned > latest:
        planned = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    # Если слишком рано, ставим на 8 утра сегодня
    elif planned < earliest:
        planned = earliest
        
    return planned

async def _run_trigger(self, chat_id: str, message: str):
    """
    Асинхронная задача, которую выполняет планировщик.
    Отправляет сообщение в телеграм.
    """
    try:
        ic(f"Сработал триггер напоминания для {chat_id}: {message}")
        await self.telegram_client.send_message(int(chat_id), message)
    except (ValueError, TypeError):
            await self.telegram_client.send_message(chat_id, message)
    except Exception as e:
        ic(f"Ошибка выполнения триггера _run_trigger для {chat_id}: {e}")
        self.sentry_sdk.capture_exception(e)


# --- НОВЫЕ ИНСТРУМЕНТЫ АГЕНТА ---

async def schedule_reminder(
    self,
    minutes: int,
    message: str,
    name: str, 
    state: Annotated[State, InjectedState] # <--- ИСПРАВЛЕН ТИП (State)
) -> str:
    """Запланируй напоминание. Используй всегда, когда клиент:

    – говорит, что уточнит у кого-то (например, у мужа, мамы, друга и т.д.);
    – сообщает, что напишет позже или через какое-то время;
    – просит подождать, потому что занят, думает, или советуется;
    
    ⏰ Это поможет клиенту не забыть о нашем разговоре.

    Обязательно подбирай УНИКАЛЬНОЕ имя напоминания (`name`), чтобы потом можно было его удалить при необходимости. Оно может быть связано с событием (например, `topchan_10min`, `uznat_u_muza`, `vernyus_posle_vstrechi`).

    ✍️ В сообщении (`message`) НЕ ИСПОЛЬЗУЙ слова 'добрый день/утро/вечер'- только здравствуйте. "напоминание", "напоминаю", "не забудьте" и т.д. 
    Текст должен звучать ЕСТЕСТВЕННО и ПОБУЖДАТЬ к действию, продолжая диалог Ты задаешь уточняющие вопросы .
    
    Формат использования:
    - `minutes`: через сколько минут сработает напоминание. Должны быть не раньше 8:00 утра и не позднее 22:00 ночи. Если не укладываешь в это время увеличивай время но не ставь на ночь.
    - `message`: текст, который будет отображаться/отправляться
    - `name`: уникальное название напоминания
    """
    
    # --- ИСПРАВЛЕНИЕ: Доступ к state через точку ---
    chat_id = state.entity["peer_id"]

    if not chat_id:
        return "Ошибка: не удалось определить chat_id."
        
    full_name = f"{chat_id}_{name}" # Уникальный ID задачи
    
    try:
        run_time = self._calculate_safe_time(minutes)
        self.scheduler.add_job(
            func=self._run_trigger,
            trigger="date",
            run_date=run_time,
            id=full_name,
            args=[chat_id, message], # Аргументы для _run_trigger
            replace_existing=True,
            misfire_grace_time=60*10 # 10 минут
        )
        return f"📅 Напоминание '{name}' успешно запланировано. будет отправлено {run_time.isoformat()}"
    
    except Exception as ex:
        self.sentry_sdk.capture_exception(ex)
        return "❌ Ошибка при создании напоминания"


async def Delreminder(
    self,
    name: str,
    state: Annotated[State, InjectedState] # <--- ИСПРАВЛЕН ТИП (State)
) -> str:
    """ ВСЕГДА удаляешь напоминание когда это уже не нужно и не актуально. 
    Если клиент вернулся с ответом на тот message которое стояло наопминание то удали его
    
    name:str "название напоминания которое нужно удалить, внимательно выбирай ведь с помощью нее ты сможешь удалять напоминания.. Пишешь только название например five_min
    """
    
    # --- ИСПРАВЛЕНИЕ: Доступ к state через точку ---
    chat_id = state.entity["peer_id"]
    if not chat_id:
        ic("Ошибка Delreminder: chat_id не найден в state.")
        return "Ошибка: не удалось определить chat_id."

    full_name = f"{chat_id}_{name}"

    try:
        self.scheduler.remove_job(full_name)
        ic(f"🗑️ Напоминание '{full_name}' удалено.")
        return f"🗑️ Напоминание '{name}' успешно удалено."
    except JobLookupError:
        ic(f"⚠️ Напоминание '{full_name}' не найдено (возможно, уже выполнено или удалено).")
        return f"❌ Напоминание '{name}' не найдено."
    except Exception as e:
        ic(f"❗ Ошибка при удалении напоминания '{full_name}': {e}")
        self.sentry_sdk.capture_exception(e)
        return f"❌ Ошибка при удалении напоминания '{name}'"
