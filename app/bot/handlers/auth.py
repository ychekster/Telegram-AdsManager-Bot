import re

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.userbot.client import start_active_client
from app.bot import keyboards as kb
from app.bot.states import Auth
from app.bot.handlers.auth_helpers import (
    set_2fa_prompt_msg_id, 
    delete_2fa_prompt_msg, 
    is_userbot_already_authorized
)
from app.db.database import (
    set_user_session,
    get_user_session,
    clear_user_session
)
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


@auth_router.message(Command('auth'))
async def cmd_auth(message: Message, state: FSMContext):
    if await is_userbot_already_authorized(message.from_user.id):
        await state.clear()
        await message.answer('Юзер бот уже успешно авторизован.')
        return

    try:
        await create_client(message.from_user.id) # создаём клиента и сохраняем его в словаре
    except Exception:
        await state.clear() # при ошибке очищаем состояние
        await message.answer('Не удалось начать авторизацию. Попробуй снова: /auth')
        return
    
    await state.set_state(Auth.choosing_method)
    await message.answer(
        text='Выбери способ авторизации юзербота.', 
        reply_markup=kb.auth_kb
    )


@auth_router.message(Command('numauth'))
async def cmd_numauth(message: Message, state: FSMContext):
    if await is_userbot_already_authorized(message.from_user.id):
        await state.clear()
        await message.answer('Юзер бот уже успешно авторизован.')
        return
    
    try:
        await create_client(message.from_user.id)
    except Exception:
        await state.clear()
        await message.answer('Не удалось начать авторизацию. Попробуй снова: /auth')
        return

    await state.set_state(Auth.waiting_phone)
    await message.answer(
        'Авторизация по номеру телефона. Введи номер телефона аккаунта в формате <code>+1234567890</code>:',
        reply_markup=ReplyKeyboardRemove()
    )


@auth_router.message(Auth.choosing_method)
async def auth_state(message: Message, state: FSMContext):
    if message.text == 'Номер':
        
        await state.set_state(Auth.waiting_phone)
        await message.answer('Авторизация по номеру телефона. Введи номер телефона аккаунта '
                             'в формате <code>+1234567890</code>:',
                             reply_markup=ReplyKeyboardRemove())
        
        return
        
    if message.text == 'QR-код':
        
        await state.set_state(Auth.qr)
        await message.answer('Авторизация по QR-коду. Отсканируй QR-код с аккаунта, который хочешь авторизовать, '
                             'используя приложение Telegram на другом устройстве.',
                             reply_markup=ReplyKeyboardRemove())
        
        return
        
    await message.answer(text='Отправь /cancel для отмены авторизации или выбери способ авторизации, '
                         'воспользовавшись клавиатурой ниже.',
                         reply_markup=kb.auth_kb)


@auth_router.message(Auth.waiting_phone)
async def auth_number(message: Message, state: FSMContext):
    phone = message.text.strip()
    
    if not re.fullmatch(r'^\+\d{10,15}$', phone):
        await message.answer('Номер в формате +1234567890')
        return
    
    client = await get_client(message.from_user.id) # получаем клиента из словаря по ID пользователя
    if not client:
        await message.answer('Сессия авторизации истекла. Начни заново: /auth')
        await state.clear()
        return

    
    try:
        async with get_auth_semaphore(): # используем семафор для ограничения количества параллельных авторизаций
            sent = await client.send_code_request(phone) # отправляем код на номер телефона
    except PhoneNumberInvalidError:
        await message.answer('Неверный номер телефона. Убедись, что ты ввёл его в правильном формате и попробуй снова.')
        return
    except FloodWaitError as e:
        await message.answer(f'Слишком много попыток авторизации. Подожди {e.seconds} секунд и попробуй снова.')
        return
    except Exception:
        await delete_client(message.from_user.id) # при любой ошибке удаляем клиента из словаря
        await state.clear()
        await message.answer('Произошла ошибка при отправке кода. Попробуй снова.')
        return
    
    await state.update_data(
        phone=phone,
        phone_code_hash=sent.phone_code_hash,
        code_attempts=0,
        password_attempts=0,
    )
    await touch_client(message.from_user.id)
    await state.set_state(Auth.waiting_code)
    await message.answer('Введи код, который был отправлен на твой аккаунт.')


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
        await message.answer('Сессия авторизации истекла. Начни заново: /auth')
        return

    try:
        async with get_auth_semaphore():
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        await touch_client(message.from_user.id)
        await state.set_state(Auth.waiting_password)
        prompt = await message.answer('Введи пароль от твоего аккаунта.')
        await set_2fa_prompt_msg_id(state, prompt.message_id)

        return
    except PhoneCodeInvalidError:
        code_attempts += 1
        await state.update_data(code_attempts=code_attempts)
        if code_attempts >= 5:
            await delete_client(message.from_user.id)
            await state.clear()
            await message.answer('Слишком много неверных попыток. Начни заново: /auth')
        else:
            await message.answer('Неверный код. Попробуй ещё раз.')
        return
    except PhoneCodeExpiredError:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer('Код истёк. Начни заново: /auth')
        return
    except FloodWaitError as e:
        await message.answer(f'Слишком много попыток. Подожди {e.seconds} сек.')
        return
    except Exception:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer('Ошибка авторизации. Начни заново: /auth')
        return

    session_str = client.session.save()
    await set_user_session(message.from_user.id, session_str)
    await start_active_client(message.from_user.id, session_str)

    await delete_client(message.from_user.id)
    await state.clear()
    await message.answer('Авторизация завершена.')

    
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
        await message.answer('Сессия авторизации истекла. Начни заново: /auth')
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
            await message.answer('Слишком много неверных попыток. Начни заново: /auth')
        else:
            err = await message.answer('Неверный пароль. Попробуй ещё раз.')
            await set_2fa_prompt_msg_id(state, err.message_id)
            return
        return
    except FloodWaitError as e:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer(f'Слишком много попыток. Подожди {e.seconds} секунд и начни заново: /auth')
        return
    except Exception:
        await delete_client(message.from_user.id)
        await state.clear()
        await message.answer('Ошибка авторизации. Начни заново: /auth')
        return

    session_str = client.session.save()
    await set_user_session(message.from_user.id, session_str)
    await start_active_client(message.from_user.id, session_str)

    await delete_client(message.from_user.id)
    await state.clear()
    await message.answer('Авторизация завершена.')


@auth_router.message(Auth.qr)
async def auth_qr(message: Message, state: FSMContext):
    await state.set_state(Auth.choosing_method)
    await message.answer('Авторизация по QR-коду пока не реализована. '
                         'Пожалуйста, выбери другой способ авторизации или отправь /cancel для отмены.',
                         reply_markup=kb.auth_kb)


@auth_router.message(Command('deauth'))
async def cmd_deauth(message: Message, state: FSMContext):
    if not await is_userbot_already_authorized(message.from_user.id):
        await message.answer(
            'Деавторизация невозможна. В данный момент ты не авторизован. Отправь /auth для авторизации'
        )
        return

    await message.answer(
        'Ты уверен, что хочешь выйти?',
        reply_markup=kb.deauth_confirm_kb
    )


@auth_router.callback_query(F.data == 'deauth_cancel')
async def deauth_cancel(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text('Выход из аккаунта отменён.')


@auth_router.callback_query(F.data == 'deauth_confirm')
async def deauth_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    user_id = callback.from_user.id
    
    if not await is_userbot_already_authorized(user_id):
        if callback.message:
            await callback.message.edit_text(
                'Деавторизация невозможна. В данный момент ты не авторизован. Отправь /auth для авторизации'
            )
        return

    # на всякий случай: закрыть незавершённую auth-сессию
    if callback.message:
        await delete_2fa_prompt_msg(callback.message, state)
    await delete_client(user_id)
    await state.clear()

    session_str = await get_user_session(user_id)
    logout_ok = await logout_userbot_session(user_id, session_str)

    if not logout_ok:
        if callback.message:
            await callback.message.edit_text(
                'Не удалось выйти из аккаунта. Попробуйте позже.'
            )
        return

    await clear_user_session(user_id)

    if callback.message:
        await callback.message.edit_text(
            'Готово! Вы вышли из аккаунта. Для повторной авторизации отправьте /auth'
        )