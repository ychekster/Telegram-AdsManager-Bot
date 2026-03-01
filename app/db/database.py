import asyncio
import importlib
from pathlib import Path

from app.db.base import Base, engine
from app.db import models  # noqa: F401
from app.db.services.user_session_service import get_user_session_service


def _alembic_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def _upgrade_schema_to_head() -> None:
    try:
        command_module = importlib.import_module("alembic.command")
        config_module = importlib.import_module("alembic.config")
    except Exception:
        raise RuntimeError("Alembic is not available")

    upgrade = getattr(command_module, "upgrade")
    config_cls = getattr(config_module, "Config")

    config = config_cls(str(_alembic_config_path()))
    upgrade(config, "head")


async def _create_schema_fallback() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def init_db() -> None:
    try:
        await asyncio.to_thread(_upgrade_schema_to_head)
    except Exception:
        await _create_schema_fallback()


async def add_user_if_not_exists(tg_id: int) -> None:
    await get_user_session_service().add_user_if_not_exists(tg_id)


async def set_user_session(tg_id: int, session_str: str) -> None:
    await get_user_session_service().set_user_session(tg_id, session_str)


async def get_user_session(tg_id: int) -> str | None:
    return await get_user_session_service().get_user_session(tg_id)


async def get_all_sessions() -> list[tuple[int, str]]:
    return await get_user_session_service().get_all_sessions()


async def clear_user_session(tg_id: int) -> None:
    await get_user_session_service().clear_user_session(tg_id)
