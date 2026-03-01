import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.db.services.user_session_service import get_user_session_service


def _alembic_config_path() -> Path:
    # app/db/database.py -> project root
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def _upgrade_schema_to_head() -> None:
    config = Config(str(_alembic_config_path()))
    command.upgrade(config, "head")


async def init_db() -> None:
    # Alembic is synchronous, execute it in a thread to keep event loop responsive.
    await asyncio.to_thread(_upgrade_schema_to_head)


# Backward-compatible API used by bot handlers/startup logic.
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
