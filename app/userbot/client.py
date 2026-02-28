import os
import asyncio
import time
import binascii

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError


from dotenv import load_dotenv


load_dotenv('.env') # загружаем переменные окружения из .env файла


# Use your own values from my.telegram.org
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')

PENDING_TTL_SECONDS = 15 * 60
PENDING_CLEANUP_INTERVAL = 30
MAX_PARALLEL_AUTH_REQUESTS = 200

MESSAGE_INTERVAL_SECONDS = 10
FAVORITES_TEXT = "Привет, сосед!"


class InvalidStoredSessionError(Exception):
    pass


def _is_invalid_session_exception(exc: Exception) -> bool:
    """
    Возвращает True только для ошибок, которые с высокой вероятностью
    означают, что сохранённая строка сессии действительно невалидна.
    """
    text = str(exc).lower().strip()

    # Формат/декодинг строки сессии
    if isinstance(exc, (ValueError, TypeError, binascii.Error)):
        return True

    # Точные маркеры, без общего "session"
    markers = (
        "not a valid string session",
        "incorrect padding",
        "invalid base64",
        "auth key",
        "key not found",
        "user is deactivated",
        "user deactivated",
        "unregistered",
    )

    return any(marker in text for marker in markers)


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


async def favorites_sender_loop(client: TelegramClient):
    while client.is_connected():
        try:
            await client.send_message("me", FAVORITES_TEXT)  # "me" = Избранное
            await asyncio.sleep(MESSAGE_INTERVAL_SECONDS)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception:
            # чтобы цикл не умер от разовой ошибки сети
            await asyncio.sleep(MESSAGE_INTERVAL_SECONDS)


async def start_active_client(tg_user_id: int, session_str: str):
    if tg_user_id in _active_clients:
        return _active_clients[tg_user_id]

    try:
        string_session = StringSession(session_str)
    except Exception as e:
        raise InvalidStoredSessionError(f"Invalid stored session format: {e}") from e

    client = TelegramClient(string_session, api_id, api_hash)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            raise InvalidStoredSessionError("Stored session is not authorized")

        task = asyncio.create_task(favorites_sender_loop(client))
        _active_clients[tg_user_id] = client
        _active_tasks[tg_user_id] = task
        return client

    except InvalidStoredSessionError:
        if client.is_connected():
            await client.disconnect()
        raise

    except (OSError, asyncio.TimeoutError):
        if client.is_connected():
            await client.disconnect()
        raise

    except Exception as e:
        if client.is_connected():
            await client.disconnect()

        if _is_invalid_session_exception(e):
            raise InvalidStoredSessionError(f"Stored session is invalid: {e}") from e

        raise


async def stop_active_client(tg_user_id: int):
    client = _active_clients.pop(tg_user_id, None) # удаляем клиента из словаря
    task = _active_tasks.pop(tg_user_id, None) # удаляем задачу из словаря
    
    if client:
        await client.disconnect() # отключаем клиента
    if task:
        task.cancel() # отменяем задачу


async def logout_userbot_session(tg_user_id: int, session_str: str | None) -> bool:
    # 1) Если активный клиент есть в памяти
    client = _active_clients.pop(tg_user_id, None)
    task = _active_tasks.pop(tg_user_id, None)

    if task:
        task.cancel()

    if client:
        try:
            if not client.is_connected():
                await client.connect()
            await client.log_out()
            return True
        except Exception:
            return False
        finally:
            if client.is_connected():
                await client.disconnect()

    # 2) Если активного клиента в памяти нет:
    # если session_str пустая, считаем, что пользователь уже разлогинен (idempotent поведение)
    if not session_str:
        return True

    # 3) Fallback: создаём временный клиент из session_str и делаем log_out()
    temp_client = TelegramClient(StringSession(session_str), api_id, api_hash)
    try:
        await temp_client.connect()
        await temp_client.log_out()
        return True
    except Exception:
        return False
    finally:
        if temp_client.is_connected():
            await temp_client.disconnect()