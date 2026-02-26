import os
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from dotenv import load_dotenv

from app.bot.handlers import user
from app.db.database import init_db, get_all_sessions
from app.userbot.client import start_active_client


load_dotenv() # загружаем переменные окружения из .env файла


async def set_commands(bot: Bot):
    commands = [
        BotCommand(command='/start', description='Перезапуск бота'),
        BotCommand(command='/help', description='Список команд'),
        BotCommand(command='/cancel', description='Отмена команды'),
        BotCommand(command='/auth', description='Авторизация юзербота'),
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


async def main():
    await init_db()
    
    sessions = await get_all_sessions() # получаем все сохранённые сессии из БД
    for tg_id, session_str in sessions:
        await start_active_client(tg_id, session_str) # запускаем активного клиента для каждой сессии
    
    bot = Bot(token=os.getenv('BOT_TOKEN'), 
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    dp = Dispatcher()
    dp.include_router(user)
    
    await set_commands(bot)
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nBye!')