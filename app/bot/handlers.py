from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from app.bot import keyboards as kb
from app.bot.states import Auth
from app.userbot.client import create_client, get_client, delete_client
from app.db.database import add_user_if_not_exists, set_user_session

from telethon.errors import SessionPasswordNeededError


user = Router()


@user.message(Command('cancel'))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('Текущее действие отменено. Я больше ничего не ожидаю.\n\n'
                         'Отправь /help для получения списка команд.',
                         reply_markup=ReplyKeyboardRemove())


@user.message(CommandStart())
@user.message(Command('help'))
async def cmd_start(message: Message):
    await add_user_if_not_exists(message.from_user.id) # добавляем пользователя в БД, если его там нет
    text = ('Привет! Я помогу тебе управлять твоим юзерботом.\n\n'
            'Ты можешь взаимодействовать со мной, используя следующие команды:\n\n'
            '/auth - авторизация юзербота\n'
            '/deauth - деавторизация юзербота\n\n'
            '<b>Инструменты</b>\n'
            '/help - список команд\n'
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


@user.message(Command('auth'))
async def cmd_start(message: Message, state: FSMContext):
    await create_client(message.from_user.id) # создаём клиента и сохраняем его в словаре
    await state.set_state(Auth.choosing_method)
    await message.answer(text='Выбери способ авторизации юзербота.', 
                         reply_markup=kb.auth_kb)


@user.message(Auth.choosing_method)
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


@user.message(Auth.waiting_phone)
async def auth_number(message: Message, state: FSMContext):
    phone = message.text.strip()
    client = get_client(message.from_user.id) # получаем клиента из словаря по ID пользователя
    
    if not client:
        await message.answer('Произошла ошибка при создании клиента, опробуй снова.')
        await state.clear()
        return
    
    sent = await client.send_code_request(phone) # отправляем код на номер телефона
    await state.update_data(phone=phone, phone_code_hash=sent.phone_code_hash) # сохраняем номер и код в состоянии
    
    await state.set_state(Auth.waiting_code)
    await message.answer('Введи код, который был отправлен на твой аккаунт.')


@user.message(Auth.waiting_code)
async def auth_code(message: Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data() # получаем сохранённые данные из состояния
    phone = data.get('phone')
    phone_code_hash = data.get('phone_code_hash')
    
    client = get_client(message.from_user.id)
    if not client:
        await message.answer('Произошла ошибка при получении клиента, попробуй снова.')
        await state.clear()
        return
    
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash) # авторизуемся, вводя код из SMS
    except SessionPasswordNeededError as e:
        await state.set_state(Auth.waiting_password) # если требуется двухфакторная аутентификация, переходим в состояние ожидания пароля
        await message.answer('Введи пароль от твоего аккаунта.')
        return
    
    session_str = client.session.save() # сохраняем сессию в строку
    await set_user_session(message.from_user.id, session_str) # сохраняем строку сессии в БД
    
    await state.clear()
    await message.answer('Авторизация завершена. Вы можете использовать бота.')

    
@user.message(Auth.waiting_password)
async def auth_password(message: Message, state: FSMContext):
    password = message.text.strip()
    
    client = get_client(message.from_user.id)
    if not client:
        await message.answer('Произошла ошибка при получении клиента, попробуй снова.')
        await state.clear()
        return
    
    await client.sign_in(password=password) # авторизуемся, вводя пароль от аккаунта
    
    session_str = client.session.save() # сохраняем сессию в строку
    await set_user_session(message.from_user.id, session_str)

    await state.clear()
    await message.answer('Авторизация завершена. Вы можете использовать бота.')


@user.message(Auth.qr)
async def auth_qr(message: Message, state: FSMContext):
    await state.set_state(Auth.choosing_method)
    await message.answer('Авторизация по QR-коду пока не реализована. '
                         'Пожалуйста, выбери другой способ авторизации или отправь /cancel для отмены.',
                         reply_markup=kb.auth_kb)