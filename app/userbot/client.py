import asyncio
import binascii
import logging
import os
import time

from telethon import TelegramClient
from telethon.sessions import StringSession

from dotenv import load_dotenv


load_dotenv(".env")

# Use your own values from my.telegram.org
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")

PENDING_TTL_SECONDS = 15 * 60
PENDING_CLEANUP_INTERVAL = 30
MAX_PARALLEL_AUTH_REQUESTS = 200

logger = logging.getLogger(__name__)


class InvalidStoredSessionError(Exception):
    pass


def _is_invalid_session_exception(exc: Exception) -> bool:
    """Return True only for errors that indicate an invalid saved session string."""
    text = str(exc).lower().strip()

    # Session string format/decoding errors.
    if isinstance(exc, (ValueError, TypeError, binascii.Error)):
        return True

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


_pending_clients: dict[int, tuple[TelegramClient, float]] = {}
_auth_semaphore = asyncio.Semaphore(MAX_PARALLEL_AUTH_REQUESTS)

_active_clients: dict[int, TelegramClient] = {}


def get_auth_semaphore() -> asyncio.Semaphore:
    return _auth_semaphore


async def create_client(user_id: int) -> TelegramClient:
    await delete_client(user_id)

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    _pending_clients[user_id] = (client, time.monotonic() + PENDING_TTL_SECONDS)
    logger.info("Created pending userbot client for user %s", user_id)
    return client


async def get_client(user_id: int) -> TelegramClient | None:
    item = _pending_clients.get(user_id)
    if not item:
        return None

    client, expiry_at = item
    if time.monotonic() >= expiry_at:
        await delete_client(user_id)
        return None

    return client


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
    logger.info("Deleted pending userbot client for user %s", user_id)


async def cleanup_pending_clients_loop():
    while True:
        now = time.monotonic()
        expired_ids = [uid for uid, (_, exp) in _pending_clients.items() if exp <= now]
        for uid in expired_ids:
            await delete_client(uid)
        if expired_ids:
            logger.info("Cleaned up %s expired pending clients", len(expired_ids))
        await asyncio.sleep(PENDING_CLEANUP_INTERVAL)


async def start_active_client(tg_user_id: int, session_str: str):
    if tg_user_id in _active_clients:
        logger.debug("Active client already running for user %s", tg_user_id)
        return _active_clients[tg_user_id]

    try:
        string_session = StringSession(session_str)
    except Exception as exc:
        raise InvalidStoredSessionError(f"Invalid stored session format: {exc}") from exc

    client = TelegramClient(string_session, api_id, api_hash)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            raise InvalidStoredSessionError("Stored session is not authorized")

        _active_clients[tg_user_id] = client
        logger.info("Started active userbot client for user %s", tg_user_id)
        return client

    except InvalidStoredSessionError:
        if client.is_connected():
            await client.disconnect()
        raise

    except (OSError, asyncio.TimeoutError):
        if client.is_connected():
            await client.disconnect()
        raise

    except Exception as exc:
        if client.is_connected():
            await client.disconnect()

        if _is_invalid_session_exception(exc):
            raise InvalidStoredSessionError(f"Stored session is invalid: {exc}") from exc

        raise


async def stop_active_client(tg_user_id: int):
    client = _active_clients.pop(tg_user_id, None)

    if client:
        await client.disconnect()
        logger.info("Stopped active userbot client for user %s", tg_user_id)


async def logout_userbot_session(tg_user_id: int, session_str: str | None) -> bool:
    # 1) Use active in-memory client if available.
    client = _active_clients.pop(tg_user_id, None)

    if client:
        try:
            if not client.is_connected():
                await client.connect()
            await client.log_out()
            logger.info("Logged out active userbot session for user %s", tg_user_id)
            return True
        except Exception:
            logger.exception("Failed to logout active userbot session for user %s", tg_user_id)
            return False
        finally:
            if client.is_connected():
                await client.disconnect()

    # 2) If no in-memory client and no session string, treat as already logged out.
    if not session_str:
        return True

    # 3) Fallback: temporary client created from stored session string.
    temp_client = TelegramClient(StringSession(session_str), api_id, api_hash)
    try:
        await temp_client.connect()
        await temp_client.log_out()
        logger.info("Logged out userbot session via temporary client for user %s", tg_user_id)
        return True
    except Exception:
        logger.exception("Failed to logout userbot session via temporary client for user %s", tg_user_id)
        return False
    finally:
        if temp_client.is_connected():
            await temp_client.disconnect()
