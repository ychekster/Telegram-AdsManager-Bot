import os
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramNetworkError

from dotenv import load_dotenv

from contextlib import suppress

from app.bot.handlers import routers
from app.db.database import init_db, get_all_sessions, clear_user_session
from app.userbot.client import (
    start_active_client,
    cleanup_pending_clients_loop,
    InvalidStoredSessionError,
)


RESTORE_CONCURRENCY = 10
RESTORE_MAX_ATTEMPTS = 3
RESTORE_INITIAL_DELAY = 2


load_dotenv() # загружаем переменные окружения из .env файла


async def set_commands(bot: Bot):
    commands = [
        BotCommand(command='/start', description='Перезапуск бота'),
        BotCommand(command='/help', description='Список команд'),
        BotCommand(command='/cancel', description='Отмена команды'),
        BotCommand(command='/auth', description='Авторизация юзербота'),
        BotCommand(command='/numauth', description='Авторизация по номеру'),
        BotCommand(command='/qrauth', description='Авторизация по QR-коду'),
        BotCommand(command='/settings', description='Настройки юзербота'),
        BotCommand(command='/deauth', description='Деавторизация юзербота'),
        BotCommand(command='/mychannels', description='Управление каналами'),
        BotCommand(command='/addchannel', description='Добавить канал'),
        BotCommand(command='/delchannel', description='Удалить канал'),
        BotCommand(command='/channels', description='Каналы рекламодателей'),
        BotCommand(command='/banned', description='Заблокированные каналы'),
        BotCommand(command='/muted', description='Замьюченные каналы'),
        BotCommand(command='/ban', description='Заблокировать канал'),
        BotCommand(command='/mute', description='Замьютить канал'),
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
        except TelegramNetworkError as e:
            if attempt == max_attempts:
                raise

            print(
                f"[startup] Нет соединения с api.telegram.org "
                f"(попытка {attempt}/{max_attempts}): {e}. "
                f"Повтор через {delay} сек..."
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


async def restore_active_clients_on_startup(
    concurrency: int = RESTORE_CONCURRENCY,
):
    sessions = await get_all_sessions()
    sessions = [(tg_id, s) for tg_id, s in sessions if s and s.strip()]

    sem = asyncio.Semaphore(concurrency)
    stats = {"restored": 0, "cleared": 0, "network_failed": 0, "failed": 0}

    async def worker(tg_id: int, session_str: str):
        async with sem:
            result = await restore_one_session(tg_id, session_str)
            stats[result] = stats.get(result, 0) + 1

    tasks = [asyncio.create_task(worker(tg_id, session_str)) for tg_id, session_str in sessions]
    if tasks:
        await asyncio.gather(*tasks)

    print(
        "[startup] restore summary: "
        f"restored={stats['restored']}, "
        f"cleared={stats['cleared']}, "
        f"network_failed={stats['network_failed']}, "
        f"failed={stats['failed']}"
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

        except InvalidStoredSessionError as e:
            await clear_user_session(tg_id)
            print(f"[startup] Cleared invalid session for user {tg_id}: {e}")
            return "cleared"

        except (OSError, asyncio.TimeoutError) as e:
            if attempt == max_attempts:
                print(f"[startup] Network fail for {tg_id} after {attempt} attempts: {e}")
                return "network_failed"

            print(
                f"[startup] Network issue restoring {tg_id} "
                f"({attempt}/{max_attempts}), retry in {delay}s: {e}"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)

        except Exception as e:
            print(f"[startup] Unexpected restore error for {tg_id}: {e}")
            return "failed"

    return "failed"


async def main():
    await init_db()

    await restore_active_clients_on_startup()

    bot = None
    cleanup_task = asyncio.create_task(cleanup_pending_clients_loop())

    try:
        bot = Bot(
            token=os.getenv('BOT_TOKEN'),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        dp = Dispatcher()
        for router in routers:
            dp.include_router(router)

        await set_commands_with_retry(bot)
        await dp.start_polling(bot)

    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task

        if bot is not None:
            with suppress(Exception):
                await bot.session.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nBye!')