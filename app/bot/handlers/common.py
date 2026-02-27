from aiogram import Router
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from app.bot.handlers.auth_helpers import delete_2fa_prompt_msg
from app.db.database import add_user_if_not_exists
from app.userbot.client import delete_client


common_router = Router(name='common')


@common_router.message(Command('cancel'))
async def cancel(message: Message, state: FSMContext):
    await delete_client(message.from_user.id)
    await delete_2fa_prompt_msg(message, state)
    await state.clear()
    await message.answer('Текущее действие отменено. Я больше ничего не ожидаю.\n\n'
                         'Отправь /help для получения списка команд.',
                         reply_markup=ReplyKeyboardRemove())


@common_router.message(CommandStart())
async def cmd_start(message: Message):
    await add_user_if_not_exists(message.from_user.id) # добавляем пользователя в БД, если его там нет
    text = ('Привет! Я помогу тебе управлять твоим юзерботом.\n\n'
            'Ты можешь взаимодействовать со мной, используя следующие команды:\n\n'
            '/auth - авторизация юзербота\n'
            '/deauth - деавторизация юзербота\n\n'
            '<b>Инструменты</b>\n'
            '/help - список команд\n'
            '/numauth - авторизация по номеру\n'
            '/qrauth - авторизация по QR-коду\n'
            '/cancel - отмена команды\n\n'
            # '/settings - настройки бота\n\n'
            '<b>Мои каналы</b>\n'
            '/mychannels - управление каналами\n'
            '/addchannel - добавить канал\n'
            '/delchannel -  удалить канал\n\n'
            '<b>Все каналы</b>\n'
            '/channels - каналы рекламодателей\n'
            '/banned - заблокированные каналы\n'
            '/muted - замьюченные каналы\n'
            '/ban - заблокировать канал\n'
            '/mute - замьютить канал\n\n'
            'Всё, больше нихуя нет 🙌')
    await message.answer(text)


@common_router.message(Command('help'))
async def cmd_help(message: Message):
    text = ('Привет! Я помогу тебе управлять твоим юзерботом.\n\n'
            'Ты можешь взаимодействовать со мной, используя следующие команды:\n\n'
            '/auth - авторизация юзербота\n'
            '/deauth - деавторизация юзербота\n\n'
            '<b>Инструменты</b>\n'
            '/help - список команд\n'
            '/numauth - авторизация по номеру\n'
            '/qrauth - авторизация по QR-коду\n'
            '/cancel - отмена команды\n\n'
            # '/settings - настройки бота\n\n'
            '<b>Мои каналы</b>\n'
            '/mychannels - управление каналами\n'
            '/addchannel - добавить канал\n'
            '/delchannel -  удалить канал\n\n'
            '<b>Все каналы</b>\n'
            '/channels - каналы рекламодателей\n'
            '/banned - заблокированные каналы\n'
            '/muted - замьюченные каналы\n'
            '/ban - заблокировать канал\n'
            '/mute - замьютить канал\n\n'
            'Всё, больше нихуя нет 🙌')
    await message.answer(text)