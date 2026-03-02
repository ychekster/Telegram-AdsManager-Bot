import asyncio
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand
from dotenv import load_dotenv

from app.bot.handlers import routers
from app.core.logging import configure_logging
from app.db.database import init_db
from app.db.services import get_user_session_service
from app.userbot.client import (
    InvalidStoredSessionError,
    bind_delivery_bot,
    cleanup_pending_clients_loop,
    close_delivery_bot,
    start_active_client,
)


RESTORE_CONCURRENCY = 10
RESTORE_MAX_ATTEMPTS = 3
RESTORE_INITIAL_DELAY = 2

logger = logging.getLogger(__name__)

load_dotenv()


async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="/start", description="Запуск бота"),
        BotCommand(command="/help", description="Список команд"),
        BotCommand(command="/cancel", description="Отмена команды"),
        BotCommand(command="/auth", description="Авторизация юзербота"),
        BotCommand(command="/numauth", description="Авторизация по номеру"),
        BotCommand(command="/qrauth", description="Авторизация по QR-коду"),
        BotCommand(command="/settings", description="Настройки юзербота"),
        BotCommand(command="/setfeed", description="Установить канал-сборник рекламы"),
        BotCommand(command="/delfeed", description="Удалить канал-сборник рекламы"),
        BotCommand(command="/monitors", description="Список отслеживаемых каналов"),
        BotCommand(command="/addmonitor", description="Добавить каналы для мониторинга"),
        BotCommand(command="/delmonitor", description="Удалить мониторы"),
        BotCommand(command="/deauth", description="Деавторизация юзербота"),
        BotCommand(command="/mychannels", description="Управление каналами"),
        BotCommand(command="/addchannel", description="Добавить канал"),
        BotCommand(command="/delchannel", description="Удалить канал"),
        BotCommand(command="/channels", description="Каналы рекламодателей"),
        BotCommand(command="/banned", description="Заблокированные каналы"),
        BotCommand(command="/muted", description="Замьюченные каналы"),
        BotCommand(command="/ban", description="Заблокировать канал"),
        BotCommand(command="/mute", description="Замьютить канал"),
    ]
    await bot.set_my_commands(commands)


async def set_commands_with_retry(
    bot: Bot,
    max_attempts: int = 5,
    initial_delay: int = 3,
    max_delay: int = 30,
):
    delay = initial_delay

    for attempt in range(1, max_attempts + 1):
        try:
            await set_commands(bot)
            return
        except TelegramNetworkError as exc:
            if attempt == max_attempts:
                raise

            logger.warning(
                "No connection to api.telegram.org (attempt %s/%s): %s. Retry in %ss",
                attempt,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


async def restore_active_clients_on_startup(
    concurrency: int = RESTORE_CONCURRENCY,
):
    user_session_service = get_user_session_service()
    sessions = await user_session_service.get_all_sessions()
    sessions = [(tg_id, s) for tg_id, s in sessions if s and s.strip()]

    sem = asyncio.Semaphore(concurrency)
    stats = {"restored": 0, "cleared": 0, "network_failed": 0, "failed": 0}

    async def worker(tg_id: int, session_str: str):
        async with sem:
            result = await restore_one_session(tg_id, session_str)
            stats[result] = stats.get(result, 0) + 1

    tasks = [
        asyncio.create_task(worker(tg_id, session_str))
        for tg_id, session_str in sessions
    ]
    if tasks:
        await asyncio.gather(*tasks)

    logger.info(
        "Restore summary: restored=%s, cleared=%s, network_failed=%s, failed=%s",
        stats["restored"],
        stats["cleared"],
        stats["network_failed"],
        stats["failed"],
    )


async def restore_one_session(
    tg_id: int,
    session_str: str,
    max_attempts: int = RESTORE_MAX_ATTEMPTS,
    initial_delay: int = RESTORE_INITIAL_DELAY,
) -> str:
    delay = initial_delay

    for attempt in range(1, max_attempts + 1):
        try:
            await start_active_client(tg_id, session_str)
            return "restored"

        except InvalidStoredSessionError as exc:
            await get_user_session_service().clear_user_session(tg_id)
            logger.warning("Cleared invalid session for user %s: %s", tg_id, exc)
            return "cleared"

        except (OSError, asyncio.TimeoutError) as exc:
            if attempt == max_attempts:
                logger.warning(
                    "Network failure for user %s after %s attempts: %s",
                    tg_id,
                    attempt,
                    exc,
                )
                return "network_failed"

            logger.warning(
                "Network issue while restoring user %s (%s/%s), retry in %ss: %s",
                tg_id,
                attempt,
                max_attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)

        except Exception as exc:
            logger.exception("Unexpected restore error for user %s: %s", tg_id, exc)
            return "failed"

    return "failed"


async def main():
    logger.info("Starting application")
    await init_db()
    logger.info("Database initialized")

    await restore_active_clients_on_startup()
    logger.info("Active clients restore finished")

    bot = None
    cleanup_task = asyncio.create_task(cleanup_pending_clients_loop())

    try:
        bot = Bot(
            token=os.getenv("BOT_TOKEN"),
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
                link_preview_is_disabled=True,
            ),
        )
        bind_delivery_bot(bot)

        dp = Dispatcher()
        for router in routers:
            dp.include_router(router)

        await set_commands_with_retry(bot)
        logger.info("Bot commands configured, starting polling")
        await dp.start_polling(bot)

    finally:
        logger.info("Shutting down application")
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task

        if bot is not None:
            with suppress(Exception):
                await bot.session.close()
        with suppress(Exception):
            await close_delivery_bot()


if __name__ == "__main__":
    configure_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by keyboard interrupt")
