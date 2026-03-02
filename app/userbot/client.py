import asyncio
import binascii
import logging
import os
import re
import time

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types.input_file import BufferedInputFile
from telethon import TelegramClient
from telethon import events
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityMention, MessageEntityTextUrl, MessageEntityUrl, PeerChannel

from dotenv import load_dotenv

from app.db.services import get_monitored_channel_service, get_user_session_service


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
_monitor_handlers: dict[int, object] = {}
_delivery_bot: Bot | None = None
_delivery_bot_owned = False

_URL_RE = re.compile(
    r"(?i)\b((?:https?://|tg://)[^\s<>\"]+|(?:t\.me|telegram\.me)/[^\s<>\"]+)"
)
_MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z][A-Za-z0-9_]{3,31})\b")

_TG_USERNAME_LINK_RE = re.compile(
    r"(?i)^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,31})(?:[/?#].*)?$"
)
_TG_INVITE_LINK_RE = re.compile(
    r"(?i)^(?:https?://)?(?:t\.me|telegram\.me)/\+([A-Za-z0-9_-]{6,})(?:[/?#].*)?$"
)
_TG_C_LINK_RE = re.compile(
    r"(?i)^(?:https?://)?(?:t\.me|telegram\.me)/c/([0-9]{3,})(?:/[0-9]+)?(?:[/?#].*)?$"
)

_POST_BUTTON_TEXT = "Открыть пост"
_TARGET_BUTTON_TEXT = "Рекламная ссылка"


def get_auth_semaphore() -> asyncio.Semaphore:
    return _auth_semaphore


def bind_delivery_bot(bot: Bot) -> None:
    global _delivery_bot, _delivery_bot_owned
    _delivery_bot = bot
    _delivery_bot_owned = False


async def _get_delivery_bot() -> Bot | None:
    global _delivery_bot, _delivery_bot_owned
    if _delivery_bot is not None:
        return _delivery_bot

    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.warning("BOT_TOKEN is empty; cannot deliver monitor posts")
        return None

    _delivery_bot = Bot(
        token=token,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    _delivery_bot_owned = True
    return _delivery_bot


async def close_delivery_bot() -> None:
    global _delivery_bot, _delivery_bot_owned
    if _delivery_bot is None:
        return
    if _delivery_bot_owned:
        try:
            await _delivery_bot.session.close()
        except Exception:
            logger.exception("Failed to close owned delivery bot session")
    _delivery_bot = None
    _delivery_bot_owned = False


def _extract_links(message_text: str, entities: list[object] | None) -> list[str]:
    if not message_text:
        message_text = ""
    links: list[str] = []
    seen: set[str] = set()

    if entities:
        for entity in entities:
            value: str | None = None
            if isinstance(entity, MessageEntityTextUrl):
                value = entity.url
            elif isinstance(entity, (MessageEntityUrl, MessageEntityMention)):
                start = int(getattr(entity, "offset", 0))
                end = start + int(getattr(entity, "length", 0))
                value = message_text[start:end]

            if value:
                normalized = value.strip().strip("()[]{}<>\"'.,")
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    links.append(normalized)

    for match in _URL_RE.finditer(message_text):
        value = match.group(1).strip().strip("()[]{}<>\"'.,")
        if value and value not in seen:
            seen.add(value)
            links.append(value)

    for match in _MENTION_RE.finditer(message_text):
        value = f"@{match.group(1)}"
        if value not in seen:
            seen.add(value)
            links.append(value)

    return links


def _is_same_channel_link(link: str, source_channel_id: int, source_username: str | None) -> bool:
    raw = (link or "").strip()
    if not raw:
        return False

    username = source_username.lower() if source_username else None
    if raw.startswith("@"):
        return bool(username and raw[1:].lower() == username)

    c_match = _TG_C_LINK_RE.fullmatch(raw)
    if c_match:
        try:
            return int(c_match.group(1)) == int(source_channel_id)
        except ValueError:
            return False

    username_link = _TG_USERNAME_LINK_RE.fullmatch(raw)
    if username_link and username:
        return username_link.group(1).lower() == username

    return False


def _channel_ref_for_tgstat(link: str) -> str | None:
    raw = (link or "").strip()
    if not raw:
        return None

    if raw.startswith("@"):
        username = raw[1:]
        return username if username else None

    username_link = _TG_USERNAME_LINK_RE.fullmatch(raw)
    if username_link:
        return username_link.group(1)

    invite_link = _TG_INVITE_LINK_RE.fullmatch(raw)
    if invite_link:
        return invite_link.group(1)

    return None


def _normalize_outbound_url(link: str) -> str | None:
    raw = (link or "").strip()
    if not raw:
        return None

    if raw.startswith("@"):
        return f"https://t.me/{raw[1:]}"
    if raw.lower().startswith(("http://", "https://")):
        return raw
    if raw.lower().startswith("tg://"):
        return raw
    if raw.lower().startswith(("t.me/", "telegram.me/")):
        return f"https://{raw}"
    return None


def _build_post_link(source_channel_id: int, source_username: str | None, message_id: int) -> str:
    if source_username:
        return f"https://t.me/{source_username}/{message_id}"
    return f"https://t.me/c/{source_channel_id}/{message_id}"


def _build_forward_keyboard(post_link: str, external_link: str) -> InlineKeyboardMarkup | None:
    post_url = _normalize_outbound_url(post_link)
    target_ref = _channel_ref_for_tgstat(external_link)
    target_url = (
        f"https://tgstat.ru/channel/{target_ref}"
        if target_ref
        else _normalize_outbound_url(external_link)
    )

    if not post_url or not target_url:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_POST_BUTTON_TEXT, url=post_url)],
            [InlineKeyboardButton(text=_TARGET_BUTTON_TEXT, url=target_url)],
        ]
    )


async def _handle_monitor_message(tg_user_id: int, client: TelegramClient, event) -> None:
    try:
        msg = event.message
        if msg is None:
            return
        if not isinstance(getattr(msg, "peer_id", None), PeerChannel):
            return

        source_channel_id = int(msg.peer_id.channel_id)
        monitors = await get_monitored_channel_service().get_user_monitors(tg_user_id)
        monitored_channel_ids = {int(row[2]) for row in monitors if int(row[2]) > 0}
        if source_channel_id not in monitored_channel_ids:
            return

        feed = await get_user_session_service().get_user_feed(tg_user_id)
        if feed is None:
            return
        feed_chat_id, _ = feed

        chat = await event.get_chat()
        source_username = str(getattr(chat, "username", "") or "").strip() or None

        text = str(getattr(msg, "message", "") or "")
        links = _extract_links(text, list(getattr(msg, "entities", None) or []))
        external_link = next(
            (lnk for lnk in links if not _is_same_channel_link(lnk, source_channel_id, source_username)),
            None,
        )
        if not external_link:
            return

        post_link = _build_post_link(source_channel_id, source_username, int(msg.id))
        keyboard = _build_forward_keyboard(post_link, external_link)
        if keyboard is None:
            return

        bot = await _get_delivery_bot()
        if bot is None:
            return

        has_photo = getattr(msg, "photo", None) is not None
        if has_photo:
            image_bytes = await client.download_media(msg, file=bytes)
            if image_bytes:
                filename = f"ad_{tg_user_id}_{source_channel_id}_{msg.id}.jpg"
                caption = text[:1024] if text else None
                await bot.send_photo(
                    chat_id=feed_chat_id,
                    photo=BufferedInputFile(image_bytes, filename=filename),
                    caption=caption,
                    parse_mode=None,
                    reply_markup=keyboard,
                )
                return

        payload_text = text or external_link
        await bot.send_message(
            chat_id=feed_chat_id,
            text=payload_text,
            parse_mode=None,
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("Failed to handle monitor message for user %s", tg_user_id)


def _attach_monitor_handler(tg_user_id: int, client: TelegramClient) -> None:
    if tg_user_id in _monitor_handlers:
        return

    @client.on(events.NewMessage())
    async def _on_new_message(event):
        await _handle_monitor_message(tg_user_id, client, event)

    _monitor_handlers[tg_user_id] = _on_new_message
    logger.info("Attached monitor handler for user %s", tg_user_id)


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
        _attach_monitor_handler(tg_user_id, client)
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
        handler = _monitor_handlers.pop(tg_user_id, None)
        if handler is not None:
            try:
                client.remove_event_handler(handler)
            except Exception:
                logger.exception("Failed to remove monitor handler for user %s", tg_user_id)
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
