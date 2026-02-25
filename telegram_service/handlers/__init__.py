from icecream import ic
try:
    from .main_handler import register as main_register
except Exception as e:
    ic(f"[Telegram init skipped]: {e}")
    main_register = None


def register_handlers(client):
    main_register(client)

    
    
