from aiogram import Router
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from app.bot.handlers.auth_helpers import delete_2fa_prompt_msg
from app.db.database import add_user_if_not_exists
from app.userbot.client import delete_client
from app.bot.texts.common_texts import CANCEL_TEXT, HELP_TEXT


common_router = Router(name='common')


@common_router.message(Command('cancel'))
async def cancel(message: Message, state: FSMContext):
    await delete_client(message.from_user.id)
    data = await state.get_data()
    qr_msg_id = data.get('qr_message_id')
    if qr_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=qr_msg_id)
        except Exception:
            pass
    await delete_2fa_prompt_msg(message, state)
    await state.clear()
    await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())


@common_router.message(CommandStart())
async def cmd_start(message: Message):
    await add_user_if_not_exists(message.from_user.id) # добавляем пользователя в БД, если его там нет
    await message.answer(HELP_TEXT)


@common_router.message(Command('help'))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)