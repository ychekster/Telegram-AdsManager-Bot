import os
import asyncio
import time

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from dotenv import load_dotenv


load_dotenv('.env') # загружаем переменные окружения из .env файла


# Use your own values from my.telegram.org
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')

PENDING_TTL_SECONDS = 15 * 60
PENDING_CLEANUP_INTERVAL = 30
MAX_PARALLEL_AUTH_REQUESTS = 200


_pending_clients: dict[int, tuple[TelegramClient, float]] = {} # словарь для хранения клиентов, ожидающих авторизации
_auth_semaphore = asyncio.Semaphore(MAX_PARALLEL_AUTH_REQUESTS) # семафор для ограничения количества параллельных запросов авторизации

_active_clients: dict[int, TelegramClient] = {} # словарь для хранения авторизованных клиентов
_active_tasks: dict[int, asyncio.Task] = {} # словарь для хранения задач, связанных с клиентами (например, прослушивание сообщений)


def get_auth_semaphore() -> asyncio.Semaphore:
    return _auth_semaphore


async def create_client(user_id: int) -> TelegramClient:
    await delete_client(user_id) # удаляем старый клиент, если он есть (на случай повторной авторизации)
    
    client = TelegramClient(StringSession(), api_id, api_hash) # создаём клиента с пустой сессией
    await client.connect()
    _pending_clients[user_id] = (client, time.monotonic() + PENDING_TTL_SECONDS) # сохраняем клиента в словаре
    return client


async def get_client(user_id: int) -> TelegramClient | None:
    item = _pending_clients.get(user_id)
    if not item:
        return None
    
    client, expiry_at = item
    if time.monotonic() >= expiry_at:
        await delete_client(user_id)
        return None
    
    return client # получаем клиента из словаря


async def touch_client(user_id: int):
    item = _pending_clients.get(user_id)
    if not item:
        return
    client, _ = item
    _pending_clients[user_id] = (client, time.monotonic() + PENDING_TTL_SECONDS)


async def delete_client(user_id: int):
    item = _pending_clients.pop(user_id, None)
    if not item:
        return
    client, _ = item
    if client.is_connected():
        await client.disconnect()


async def cleanup_pending_clients_loop():
    while True:
        now = time.monotonic()
        expired_ids = [uid for uid, (_, exp) in _pending_clients.items() if exp <= now]
        for uid in expired_ids:
            await delete_client(uid)
        await asyncio.sleep(PENDING_CLEANUP_INTERVAL)


def register_handlers(client: TelegramClient):
    @client.on(events.NewMessage(from_users='nayarek')) 
    async def handler(event): 
        await event.reply("Принял сообщение")


async def start_active_client(tg_user_id: int, session_str: str):
    if tg_user_id in _active_clients:
        return _active_clients[tg_user_id]

    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    register_handlers(client)
    await client.connect()

    task = asyncio.create_task(client.run_until_disconnected())
    _active_clients[tg_user_id] = client
    _active_tasks[tg_user_id] = task
    return client


async def stop_active_client(tg_user_id: int):
    client = _active_clients.pop(tg_user_id, None) # удаляем клиента из словаря
    task = _active_tasks.pop(tg_user_id, None) # удаляем задачу из словаря
    
    if client:
        await client.disconnect() # отключаем клиента
    if task:
        task.cancel() # отменяем задачу