from app.bot.handlers.common import common_router
from app.bot.handlers.auth import auth_router

routers = (common_router, auth_router)

__all__ = ('common_router', 'auth_router', 'routers')
