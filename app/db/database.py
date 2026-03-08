import asyncio
import importlib
import logging
from pathlib import Path

from sqlalchemy import text

from app.db.base import Base, engine
from app.db import models  # noqa: F401
from app.db.services.user_session_service import get_user_session_service

logger = logging.getLogger(__name__)


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


async def _ensure_monitored_channels_compat() -> None:
    async with engine.begin() as connection:
        dialect = connection.dialect.name

        if dialect == "sqlite":
            table_exists = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='monitored_channels'")
            )
            if table_exists.first() is None:
                return

            cols_result = await connection.execute(text("PRAGMA table_info(monitored_channels)"))
            cols = {row[1] for row in cols_result.fetchall()}

            if "channel_id" not in cols:
                await connection.execute(text("ALTER TABLE monitored_channels ADD COLUMN channel_id BIGINT"))
            if "access_hash" not in cols:
                await connection.execute(text("ALTER TABLE monitored_channels ADD COLUMN access_hash BIGINT"))
            if "channel_title" not in cols:
                await connection.execute(text("ALTER TABLE monitored_channels ADD COLUMN channel_title TEXT"))

            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_monitored_channels_channel_id "
                    "ON monitored_channels(channel_id)"
                )
            )
            await connection.commit()
            return

        table_exists = await connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name='monitored_channels'"
            )
        )
        if table_exists.first() is None:
            return

        cols_result = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='monitored_channels'"
            )
        )
        cols = {row[0] for row in cols_result.fetchall()}

        if "channel_id" not in cols:
            await connection.execute(text("ALTER TABLE monitored_channels ADD COLUMN channel_id BIGINT"))
        if "access_hash" not in cols:
            await connection.execute(text("ALTER TABLE monitored_channels ADD COLUMN access_hash BIGINT"))
        if "channel_title" not in cols:
            await connection.execute(text("ALTER TABLE monitored_channels ADD COLUMN channel_title TEXT"))

        await connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_monitored_channels_channel_id ON monitored_channels(channel_id)")
        )
        await connection.commit()


async def _ensure_users_feed_compat() -> None:
    async with engine.begin() as connection:
        dialect = connection.dialect.name

        if dialect == "sqlite":
            table_exists = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            )
            if table_exists.first() is None:
                return

            cols_result = await connection.execute(text("PRAGMA table_info(users)"))
            cols = {row[1] for row in cols_result.fetchall()}

            if "feed_chat_id" not in cols:
                await connection.execute(text("ALTER TABLE users ADD COLUMN feed_chat_id BIGINT"))
            if "feed_chat_title" not in cols:
                await connection.execute(text("ALTER TABLE users ADD COLUMN feed_chat_title TEXT"))
            await connection.commit()
            return

        table_exists = await connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name='users'"
            )
        )
        if table_exists.first() is None:
            return

        cols_result = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users'"
            )
        )
        cols = {row[0] for row in cols_result.fetchall()}

        if "feed_chat_id" not in cols:
            await connection.execute(text("ALTER TABLE users ADD COLUMN feed_chat_id BIGINT"))
        if "feed_chat_title" not in cols:
            await connection.execute(text("ALTER TABLE users ADD COLUMN feed_chat_title TEXT"))
        await connection.commit()


async def _ensure_muted_post_texts_compat() -> None:
    async with engine.begin() as connection:
        dialect = connection.dialect.name

        if dialect == "sqlite":
            table_exists = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='muted_post_texts'")
            )
            if table_exists.first() is None:
                return

            cols_result = await connection.execute(text("PRAGMA table_info(muted_post_texts)"))
            cols = {row[1] for row in cols_result.fetchall()}
            if "muted_text" not in cols:
                await connection.execute(
                    text("ALTER TABLE muted_post_texts ADD COLUMN muted_text TEXT NOT NULL DEFAULT ''")
                )
            await connection.commit()
            return

        table_exists = await connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name='muted_post_texts'"
            )
        )
        if table_exists.first() is None:
            return

        cols_result = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='muted_post_texts'"
            )
        )
        cols = {row[0] for row in cols_result.fetchall()}
        if "muted_text" not in cols:
            await connection.execute(
                text("ALTER TABLE muted_post_texts ADD COLUMN muted_text TEXT NOT NULL DEFAULT ''")
            )
        await connection.commit()


async def init_db() -> None:
    try:
        logger.info("Applying database migrations to head")
        await asyncio.to_thread(_upgrade_schema_to_head)
        logger.info("Database migrations applied")
    except Exception:
        logger.exception("Alembic upgrade failed; fallback to create_all")
        await _create_schema_fallback()
        logger.info("Database schema created via fallback")

    try:
        await _ensure_monitored_channels_compat()
        logger.info("Database compatibility checks applied")
    except Exception:
        logger.exception("Failed database compatibility checks")

    try:
        await _ensure_users_feed_compat()
        logger.info("Users feed compatibility checks applied")
    except Exception:
        logger.exception("Failed users feed compatibility checks")

    try:
        await _ensure_muted_post_texts_compat()
        logger.info("Muted post texts compatibility checks applied")
    except Exception:
        logger.exception("Failed muted post texts compatibility checks")


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


async def set_user_feed(tg_id: int, feed_chat_id: int, feed_chat_title: str) -> None:
    await get_user_session_service().set_user_feed(tg_id, feed_chat_id, feed_chat_title)


async def get_user_feed(tg_id: int) -> tuple[int, str] | None:
    return await get_user_session_service().get_user_feed(tg_id)


async def clear_user_feed(tg_id: int) -> None:
    await get_user_session_service().clear_user_feed(tg_id)
