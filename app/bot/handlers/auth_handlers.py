import re

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.texts.auth_texts import *
from app.bot.texts.keyboards_texts import AUTH_METHOD_NUMBER, AUTH_METHOD_QR
from app.userbot.client import start_active_client
from app.bot import keyboards as kb
from app.bot.states import Auth
from app.bot.handlers.auth_helpers import (
    set_2fa_prompt_msg_id,
    delete_2fa_prompt_msg,
    is_userbot_already_authorized,
    start_qr_auth_flow,
)
from app.db.services import get_user_session_service
from app.userbot.client import (
    create_client,
    get_client,
    delete_client,
    touch_client,
    get_auth_semaphore,
    logout_userbot_session
)

from telethon.errors import (
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
)


auth_router = Router(name='auth')
user_session_service = get_user_session_service()


@auth_router.message(Command('auth'))
async def cmd_auth(message: Message, state: FSMContext):
    if await is_userbot_already_authorized(message.from_user.id):
        await state.clear()
        await message.answer(AUTH_ALREADY_AUTHORIZED)
        return

    try:
        await create_client(message.from_user.id) # создаём клиента и сохраняем его в словаре
    except Exception:
        await state.clear() # при ошибке очищаем состояние
        await message.answer(AUTH_START_FAILED)
        return
    
    await state.set_state(Auth.choosing_method)
    await message.answer(
        text=AUTH_METHOD_CHOOSE, 
        reply_markup=kb.auth_kb
    )


@auth_router.message(Command('numauth'))
async def cmd_numauth(message: Message, state: FSMContext):
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

    await state.set_state(Auth.waiting_phone)
    await message.answer(
        AUTH_PHONE_PROMPT,
        reply_markup=ReplyKeyboardRemove()
    )


@auth_router.message(Command('qrauth'))
async def cmd_qrauth(message: Message, state: FSMContext):
    await start_qr_auth_flow(message, state)


@auth_router.message(Auth.choosing_method)
async def auth_state(message: Message, state: FSMContext):
    if message.text == AUTH_METHOD_NUMBER:
        
        await state.set_state(Auth.waiting_phone)
        await message.answer(AUTH_PHONE_PROMPT, reply_markup=ReplyKeyboardRemove())
        
        return
        
    if message.text == AUTH_METHOD_QR:
        await start_qr_auth_flow(message, state)
        return
        
    await message.answer(text=AUTH_CANCEL_OR_CHOOSE, reply_markup=kb.auth_kb)


@auth_router.message(Auth.scanning_qr, ~F.text.startswith('/'))
async def remind_scan_qr(message: Message):
    await message.answer(AUTH_SCAN_QR_ABOVE)


@auth_router.message(Auth.waiting_phone)
async def auth_number(message: Message, state: FSMContext):
    phone = message.text.strip()
    
    if not re.fullmatch(r'^\+\d{10,15}$', phone):
        await message.answer(AUTH_PHONE_FORMAT) # отправляется если номер не соответствует формату +1234567890
        return
    
    client = await get_client(message.from_user.id) # получаем клиента из словаря по ID пользователя
    if not client:
        await message.answer(AUTH_SESSION_EXPIRED)
        await state.clear()
        return

    
    try:
        async with get_auth_semaphore(): # используем семафор для ограничения количества параллельных авторизаций
            sent = await client.send_code_request(phone) # отправляем код на номер телефона
    except PhoneNumberInvalidError:
        await message.answer(AUTH_PHONE_FORMAT) # отправляется если номер не соответствует формату или недействителен
        return
    except FloodWaitError as e:
        await message.answer(AUTH_FLOOD_WAIT_AUTH.format(seconds=e.seconds))
        return
    except Exception:
        await delete_client(message.from_user.id) # при любой ошибке удаляем клиента из словаря
        await state.clear()
        await message.answer(AUTH_SEND_CODE_ERROR)
        return
    
    await state.update_data(
        phone=phone,
        phone_code_hash=sent.phone_code_hash,
        code_attempts=0,
        password_attempts=0,
    )
    await touch_client(message.from_user.id)
    await state.set_state(Auth.waiting_code)
    await message.answer(AUTH_CODE_PROMPT)


@auth_router.message(Auth.waiting_code)
async def auth_code(message: Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    phone = data.get('phone')
    phone_code_hash = data.get('phone_code_hash')
    code_attempts = int(data.get('code_attempts', 0))

    client = await get_client(message.from_user.id)
    if not client or not phone or not phone_code_hash:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(AUTH_SESSION_EXPIRED)
        return

    try:
        async with get_auth_semaphore():
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        await touch_client(message.from_user.id)
        await state.set_state(Auth.waiting_password)
        prompt = await message.answer(AUTH_PASSWORD_PROMPT)
        await set_2fa_prompt_msg_id(state, prompt.message_id)

        return
    except PhoneCodeInvalidError:
        code_attempts += 1
        await state.update_data(code_attempts=code_attempts)
        if code_attempts >= 5:
            await delete_client(message.from_user.id)
            await state.clear()
            await message.answer(AUTH_TOO_MANY_WRONG_ATTEMPTS)
        else:
            await message.answer(AUTH_INVALID_CODE)
        return
    except PhoneCodeExpiredError:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(AUTH_CODE_EXPIRED)
        return
    except FloodWaitError as e:
        await message.answer(AUTH_FLOOD_WAIT_SHORT.format(seconds=e.seconds))
        return
    except Exception:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(AUTH_GENERIC_ERROR)
        return

    session_str = client.session.save()
    await user_session_service.set_user_session(message.from_user.id, session_str)
    await start_active_client(message.from_user.id, session_str)

    await delete_client(message.from_user.id)
    await state.clear()
    await message.answer(AUTH_SUCCESS)

    
@auth_router.message(Auth.waiting_password)
async def auth_password(message: Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    password_attempts = int(data.get('password_attempts', 0))
    
    # Удаляем сообщение пользователя с паролем
    try:
        await message.delete()
    except Exception:
        pass
    
    # Удаляем предыдущее сообщение бота (промпт/ошибка)
    await delete_2fa_prompt_msg(message, state)

    client = await get_client(message.from_user.id)
    if not client:
        await state.clear()
        await message.answer(AUTH_SESSION_EXPIRED)
        return

    try:
        async with get_auth_semaphore():
            await client.sign_in(password=password)
    except PasswordHashInvalidError:
        password_attempts += 1
        await state.update_data(password_attempts=password_attempts)
        if password_attempts >= 7:
            await delete_client(message.from_user.id)
            await state.clear()
            await message.answer(AUTH_TOO_MANY_WRONG_ATTEMPTS)
        else:
            err = await message.answer(AUTH_INVALID_PASSWORD)
            await set_2fa_prompt_msg_id(state, err.message_id)
            return
        return
    except FloodWaitError as e:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(AUTH_FLOOD_WAIT_RESTART.format(seconds=e.seconds))
        return
    except Exception:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(AUTH_GENERIC_ERROR)
        return

    session_str = client.session.save()
    await user_session_service.set_user_session(message.from_user.id, session_str)
    await start_active_client(message.from_user.id, session_str)

    await delete_client(message.from_user.id)
    await state.clear()
    await message.answer(AUTH_SUCCESS)


@auth_router.message(Command('deauth'))
async def cmd_deauth(message: Message, state: FSMContext):
    if not await is_userbot_already_authorized(message.from_user.id):
        await message.answer(DEAUTH_UNAVAILABLE)
        return

    await message.answer(DEAUTH_CONFIRM, reply_markup=kb.deauth_confirm_kb)


@auth_router.callback_query(F.data == 'deauth_cancel')
async def deauth_cancel(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(DEAUTH_CANCELLED)


@auth_router.callback_query(F.data == 'deauth_confirm')
async def deauth_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    user_id = callback.from_user.id
    
    if not await is_userbot_already_authorized(user_id):
        if callback.message:
            await callback.message.edit_text(DEAUTH_UNAVAILABLE)
        return

    # на всякий случай: закрыть незавершённую auth-сессию
    if callback.message:
        await delete_2fa_prompt_msg(callback.message, state)
    await delete_client(user_id)
    await state.clear()

    session_str = await user_session_service.get_user_session(user_id)
    logout_ok = await logout_userbot_session(user_id, session_str)

    if not logout_ok:
        if callback.message:
            await callback.message.edit_text(DEAUTH_FAILED)
        return

    await user_session_service.clear_user_session(user_id)

    if callback.message:
        await callback.message.edit_text(DEAUTH_DONE)
