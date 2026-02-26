import os
import asyncio

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from dotenv import load_dotenv


load_dotenv('.env') # загружаем переменные окружения из .env файла


# Use your own values from my.telegram.org
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')


_pending_clients: dict[int, TelegramClient] = {} # словарь для хранения клиентов, ожидающих авторизации

_active_clients: dict[int, TelegramClient] = {} # словарь для хранения авторизованных клиентов
_active_tasks: dict[int, asyncio.Task] = {} # словарь для хранения задач, связанных с клиентами (например, прослушивание сообщений)


async def create_client(user_id: int) -> TelegramClient:
    client = TelegramClient(StringSession(), api_id, api_hash) # создаём клиента с пустой сессией
    await client.connect()
    _pending_clients[user_id] = client # сохраняем клиента в словаре
    return client


def get_client(user_id: int) -> TelegramClient | None:
    return _pending_clients.get(user_id) # получаем клиента из словаря


async def delete_client(user_id: int):  
    client = _pending_clients.pop(user_id, None) # удаляем клиента из словаря
    if client:
        await client.disconnect() # отключаем клиента


def register_handlers(client: TelegramClient):
    @client.on(events.NewMessage(from_users='nayarek')) 
    async def handler(event): 
        await event.reply("Принял сообщение")


async def start_active_client(tg_user_id: int, session_str: str):
    if tg_user_id in _active_clients:
        return _active_clients[tg_user_id] # если клиент уже активен, возвращаем его
    
    client = TelegramClient(StringSession(session_str), api_id, api_hash) # создаём клиента с сохранённой сессией
    register_handlers(client) # регистрируем обработчики событий для клиента
    await client.connect()
    
    task = asyncio.create_task(client.run_until_disconnected()) # запускаем клиента в отдельной задаче
    
    _active_clients[tg_user_id] = client # сохраняем активного клиента в словаре
    _active_tasks[tg_user_id] = task # сохраняем задачу в словаре
    return client


async def stop_active_client(tg_user_id: int):
    client = _active_clients.pop(tg_user_id, None) # удаляем клиента из словаря
    task = _active_tasks.pop(tg_user_id, None) # удаляем задачу из словаря
    
    if client:
        await client.disconnect() # отключаем клиента
    if task:
        task.cancel() # отменяем задачу