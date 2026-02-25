import asyncio
import aiohttp  # Убедитесь, что он у вас установлен: pip install aiohttp
from icecream import ic
import os
import time
from dotenv import load_dotenv
load_dotenv()

ic(os.getenv("UPTIME_URL", ""))


def _env_enabled(var_name: str, default: str = "1") -> bool:
    raw = str(os.getenv(var_name, default)).strip().lower()
    return raw not in {"0", "false", "no", "off"}


class UptimeMonitor:
    """
    Класс-синглтон для управления фоновой задачей пинга Uptime Kuma.
    
    Управляет состоянием (вкл/выкл) и жизненным циклом (start/stop)
    асинхронной задачи.
    """
    
    def __init__(self, initial_state: bool = True):
        self.UPTIME_URL = str(os.getenv("UPTIME_URL", "")).strip()
        self._enabled = bool(initial_state and _env_enabled("UPTIME_ENABLED", "1") and self.UPTIME_URL)
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._last_error_log_at = 0.0
        ic(f"Uptime Monitor инициализирован. Cостояние: {self._enabled}")
        if not self.UPTIME_URL:
            ic("Uptime: UPTIME_URL не задан, отправка пингов отключена.")

    def _log_ping_error(self, message: str):
        # Ограничиваем одинаковые ошибки пинга, чтобы не засорять логи.
        now = time.monotonic()
        if (now - self._last_error_log_at) >= 60:
            ic(message)
            self._last_error_log_at = now

    def set_enabled(self, status: bool):
        """
        Главный метод для управления флагом.
        Вызывайте его из любого места кода.
        """
        if status:
            ic("Uptime: Включение отправки пингов.")
        else:
            ic("Uptime: Отключение отправки пингов.")
        self._enabled = bool(status and self.UPTIME_URL)

    async def _send_ping(self):
        """Асинхронно отправляет один GET-запрос (пинг)."""
        if not self._enabled or not self.UPTIME_URL:
            return
        if not self._session:
            self._log_ping_error("Uptime ОШИБКА: Попытка пинга без запущенной сессии!")
            return
            
        try:
            # Используем session.get()
            async with self._session.get(self.UPTIME_URL) as response:
           #     ic(f"Uptime: Пинг отправлен. Статус ответа: {response.status}")
               pass
        except aiohttp.ClientConnectorError as e:
            self._log_ping_error(f"Uptime ОШИБКА: Ошибка подключения: {e}")
        except aiohttp.ClientError as e:
            self._log_ping_error(f"Uptime ОШИБКА: Ошибка клиента: {e}")
        except asyncio.TimeoutError as e:
            self._log_ping_error(f"Uptime ОШИБКА: Таймаут пинга: {e}")
        except Exception as e:
            self._log_ping_error(f"Uptime ОШИБКА: Непредвиденная ошибка при пинге: {e}")

    async def _periodic_task(self, interval_sec: int):
        """
        Асинхронная "задача", которая выполняется в цикле каждые N секунд.
        """
        ic(f"Uptime: Фоновая задача запущена. Интервал: {interval_sec} сек.")
        
        while True:
            try:
                # 1. Проверяем флаг
                if self._enabled:
                    await self._send_ping()
                # else:
                #    ic("Uptime: Отправка пропущена (флаг False)") # Можно раскомментировать для отладки
                
                # 2. Ждем N секунд до следующей проверки
                await asyncio.sleep(interval_sec)
                
            except asyncio.CancelledError:
                ic("Uptime: Фоновая задача остановлена (CancelledError).")
                break  # Выходим из цикла while True
            except Exception as e:
                ic(f"Uptime ОШИБКА: в цикле задачи: {e}")
                # Ждем перед повторной попыткой, чтобы не спамить ошибками
                await asyncio.sleep(interval_sec)

    async def start(self, interval_sec: int = 50):
        """
        Запускает фоновую задачу и создает HTTP-сессию.
        Вызывается один раз при старте приложения.
        """
        if self._task and not self._task.done():
            ic("Uptime: Задача уже запущена.")
            return
        if not self._enabled:
            ic("Uptime: Монитор отключен через конфиг, задача не запущена.")
            return
            
        # Создаем одну сессию для всех запросов
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
        
        # 3. Создаем фоновую задачу
        self._task = asyncio.create_task(self._periodic_task(interval_sec))
        ic("Uptime: Монитор запущен.")

    async def stop(self):
        """
        Корректно останавливает задачу и закрывает сессию.
        Вызывается при завершении работы приложения.
        """
        ic("Uptime: Остановка монитора...")
        # 1. Отменяем задачу
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task  # Ждем, пока задача действительно отменится
            except asyncio.CancelledError:
                pass  # Это ожидаемое исключение
        
        # 2. Закрываем HTTP-сессию
        if self._session:
            await self._session.close()
            self._session = None
            
        ic("Uptime: Монитор полностью остановлен.")

uptime_monitor = UptimeMonitor(initial_state=True)
