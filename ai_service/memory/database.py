import psycopg
from psycopg_pool import ConnectionPool, AsyncConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from ai_service.config.settings import settings
import time
_async_connection_pool = None

# --- СИНХРОННАЯ ЧАСТЬ (для db-init) ---
def wait_for_postgres(uri: str, retries: int = 30, delay: int = 2):
    for i in range(retries):
        try:
            with psycopg.connect(uri, autocommit=True) as conn:
                print("Postgres is ready")
                return
        except psycopg.OperationalError:
            print(f"Waiting for Postgres ({i+1}/{retries})...")
            time.sleep(delay)
    raise RuntimeError("Postgres not available")

def init_db():
    print("Проверка и создание таблиц БД...")
    uri = str(settings.postgres.db_uri)
    wait_for_postgres(uri)
    # Требуется autocommit для CREATE INDEX CONCURRENTLY внутри setup()
    with psycopg.connect(uri, autocommit=True) as conn:
        checkpointer = PostgresSaver(conn)
        checkpointer.setup()
    print("Таблицы чекпоинтов созданы.")

# --- АСИНХРОННАЯ ЧАСТЬ (для Агента) ---
def get_async_pool():
    global _async_connection_pool
    if _async_connection_pool is None:
        _async_connection_pool = AsyncConnectionPool(
            conninfo=str(settings.postgres.db_uri),
            max_size=settings.postgres.max_size,
            kwargs={"autocommit": True},
            open=False # 👈 ГЛАВНОЕ ИЗМЕНЕНИЕ: Не открывать сразу
        )
    return _async_connection_pool

def get_checkpointer():
    pool = get_async_pool()
    return AsyncPostgresSaver(pool)

# 👇 НОВАЯ ФУНКЦИЯ для main.py
async def ensure_db_ready():
    """Явно открывает пул соединений при старте FastAPI"""
    pool = get_async_pool()
    await pool.open()
    await pool.wait()
    print("✅ Async Connection Pool opened.")
    
    
async def delete_thread_data(thread_id: str) -> bool:
    """
    Асинхронно удаляет всю историю для указанного thread_id из Postgres.
    """
    pool = get_async_pool()
    
    # SQL запросы для очистки таблиц LangGraph
    # Обычно LangGraph создает таблицы: checkpoints, checkpoint_blobs, checkpoint_writes
    queries = [
        "DELETE FROM checkpoint_writes WHERE thread_id = %s",
        "DELETE FROM checkpoints WHERE thread_id = %s"
    ]
    
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                for query in queries:
                    await cur.execute(query, (thread_id,))
            # Не забываем закоммитить, если автокоммит не сработал (но у нас он включен в пуле)
            # await conn.commit() 
            return True
    except Exception as e:
        print(f"❌ Ошибка при удалении истории: {e}")
        return False
