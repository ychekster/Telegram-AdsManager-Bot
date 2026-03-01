from app.bot.handlers.common_handlers import common_router
from app.bot.handlers.auth_handlers import auth_router

routers = (common_router, auth_router)

__all__ = ('common_router', 'auth_router', 'routers')
