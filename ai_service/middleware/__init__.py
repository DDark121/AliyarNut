from ai_service.config.settings import settings
from .ContentFilter import ContentFilterMiddleware
from .SafetyGuardrail import InputSafetyMiddleware

ALL_MIDDLEWARE = [
        ContentFilterMiddleware(banned_keywords=settings.security.banned_keywords), 
      #  InputSafetyMiddleware(),
]
