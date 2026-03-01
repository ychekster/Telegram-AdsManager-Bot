import asyncio
import io
import qrcode

from aiogram.types import Message, ReplyKeyboardRemove, BufferedInputFile
from aiogram.fsm.context import FSMContext

from telethon.errors import SessionPasswordNeededError, FloodWaitError

from app.bot.texts.auth_texts import *
from app.db.services import get_user_session_service
from app.bot.states import Auth
from app.userbot.client import (
    create_client,
    get_client,
    delete_client,
    touch_client,
    get_auth_semaphore,
    start_active_client,
)

user_session_service = get_user_session_service()


async def set_2fa_prompt_msg_id(state: FSMContext, message_id: int | None):
    await state.update_data(twofa_prompt_message_id=message_id)


async def delete_2fa_prompt_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt_id = data.get('twofa_prompt_message_id')
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
    await state.update_data(twofa_prompt_message_id=None)


async def is_userbot_already_authorized(tg_user_id: int) -> bool:
    session_str = await user_session_service.get_user_session(tg_user_id)
    return bool(session_str and session_str.strip())


async def finalize_auth_success(message: Message, state: FSMContext, client):
    session_str = client.session.save()
    await user_session_service.set_user_session(message.from_user.id, session_str)
    await start_active_client(message.from_user.id, session_str)
    await delete_client(message.from_user.id)
    await state.clear()
    await message.answer(AUTH_SUCCESS)


async def delete_qr_message(message: Message, state: FSMContext, qr_msg_id: int | None = None):
    if qr_msg_id is None:
        data = await state.get_data()
        qr_msg_id = data.get('qr_message_id')

    if qr_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=qr_msg_id)
        except Exception:
            pass

    await state.update_data(qr_message_id=None)


async def start_qr_auth_flow(message: Message, state: FSMContext):
    if await is_userbot_already_authorized(message.from_user.id):
        await state.clear()
        await message.answer(AUTH_ALREADY_AUTHORIZED)
        return

    try:
        await create_client(message.from_user.id)
    except Exception:
        await state.clear()
        await message.answer(AUTH_START_FAILED)
        return

    client = await get_client(message.from_user.id)
    if not client:
        await state.clear()
        await message.answer(AUTH_SESSION_EXPIRED)
        return

    await state.set_state(Auth.scanning_qr)
    await state.update_data(password_attempts=0)

    qr_msg_id = None

    try:
        qr_login = await client.qr_login()
        img = qrcode.make(qr_login.url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_photo = BufferedInputFile(buf.getvalue(), filename='qrauth.png')

        qr_msg = await message.answer_photo(
            photo=qr_photo,
            caption=QR_CAPTION,
            reply_markup=ReplyKeyboardRemove(),
        )
        qr_msg_id = qr_msg.message_id
        await state.update_data(qr_message_id=qr_msg_id)
        await touch_client(message.from_user.id)

        async with get_auth_semaphore():
            await qr_login.wait(timeout=120)

        await delete_qr_message(message, state, qr_msg_id=qr_msg_id)
        await finalize_auth_success(message, state, client)

    except SessionPasswordNeededError:
        await delete_qr_message(message, state, qr_msg_id=qr_msg_id)
        await touch_client(message.from_user.id)
        await state.set_state(Auth.waiting_password)
        prompt = await message.answer(AUTH_PASSWORD_PROMPT)
        await set_2fa_prompt_msg_id(state, prompt.message_id)

    except asyncio.TimeoutError:
        await delete_qr_message(message, state, qr_msg_id=qr_msg_id)
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(QR_TIMEOUT)

    except FloodWaitError as e:
        await delete_qr_message(message, state, qr_msg_id=qr_msg_id)
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(AUTH_FLOOD_WAIT_SHORT.format(seconds=e.seconds))

    except Exception:
        await delete_qr_message(message, state, qr_msg_id=qr_msg_id)

        if not await get_client(message.from_user.id):
            return

        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(AUTH_GENERIC_ERROR)
