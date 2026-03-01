from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from app.bot.handlers.auth_helpers import delete_2fa_prompt_msg, is_userbot_already_authorized
from app.bot.handlers.common_helpers import safe_html
from app.bot.texts.common_texts import (
    CANCEL_TEXT,
    HELP_TEXT,
    SETTINGS_LOAD_FAILED,
    SETTINGS_TEXT,
    SETTINGS_UNAVAILABLE,
)
from app.db.services import get_user_session_service
from app.userbot.client import InvalidStoredSessionError, delete_client, start_active_client


common_router = Router(name="common")
user_session_service = get_user_session_service()


@common_router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await delete_client(message.from_user.id)
    data = await state.get_data()
    qr_msg_id = data.get("qr_message_id")
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
    await user_session_service.add_user_if_not_exists(message.from_user.id)
    await message.answer(HELP_TEXT)


@common_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@common_router.message(Command("settings"))
async def cmd_settings(message: Message):
    user_id = message.from_user.id

    if not await is_userbot_already_authorized(user_id):
        await message.answer(SETTINGS_UNAVAILABLE)
        return

    session_str = await user_session_service.get_user_session(user_id)

    try:
        client = await start_active_client(user_id, session_str)
        me = await client.get_me()
    except InvalidStoredSessionError:
        await user_session_service.clear_user_session(user_id)
        await message.answer(SETTINGS_UNAVAILABLE)
        return
    except Exception:
        await message.answer(SETTINGS_LOAD_FAILED)
        return

    if not me:
        await message.answer(SETTINGS_LOAD_FAILED)
        return

    username = f"@{me.username}" if me.username else "не указано"
    premium = "да" if getattr(me, "premium", False) else "нет"

    text = SETTINGS_TEXT.format(
        id=safe_html(me.id),
        first_name=safe_html(me.first_name),
        last_name=safe_html(me.last_name),
        username=safe_html(username),
        phone=safe_html(me.phone),
        premium=safe_html(premium),
    )
    await message.answer(text)
