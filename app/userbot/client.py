import asyncio
import binascii
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from aiogram.types.input_file import BufferedInputFile
from telethon import TelegramClient
from telethon import events
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import Channel
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
RUNTIME_SYNC_INTERVAL_SECONDS = 20
LINK_RESOLVE_CACHE_TTL_SECONDS = 30 * 60
DELIVERY_QUEUE_MAXSIZE = 2000
DELIVERY_WORKERS = 4
DELIVERY_RETRY_ATTEMPTS = 5
LINK_RESOLVE_RETRY_ATTEMPTS = 3
STALE_MESSAGE_MAX_AGE_SECONDS = 180

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
_user_runtimes: dict[int, "UserRuntime"] = {}
_delivery_bot: Bot | None = None
_delivery_bot_owned = False
_delivery_queue: asyncio.Queue["DeliveryJob"] = asyncio.Queue(maxsize=DELIVERY_QUEUE_MAXSIZE)
_delivery_workers: list[asyncio.Task] = []
_resolved_tg_links_cache: dict[str, tuple[int | None, float]] = {}

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


@dataclass
class UserRuntime:
    tg_user_id: int
    client: TelegramClient
    monitor_ids: set[int] = field(default_factory=set)
    feed_chat_id: int | None = None
    handler: object | None = None
    sync_task: asyncio.Task | None = None
    accepted_from_ts: float = field(default_factory=lambda: time.time() - 5)


@dataclass
class DeliveryJob:
    feed_chat_id: int
    text: str
    entities: list[MessageEntity]
    keyboard: InlineKeyboardMarkup
    photo_bytes: bytes | None = None


def get_auth_semaphore() -> asyncio.Semaphore:
    return _auth_semaphore


def bind_delivery_bot(bot: Bot) -> None:
    global _delivery_bot, _delivery_bot_owned
    _delivery_bot = bot
    _delivery_bot_owned = False
    _ensure_delivery_workers()


async def _get_delivery_bot() -> Bot | None:
    global _delivery_bot, _delivery_bot_owned
    if _delivery_bot is not None:
        _ensure_delivery_workers()
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
    _ensure_delivery_workers()
    return _delivery_bot


async def close_delivery_bot() -> None:
    global _delivery_bot, _delivery_bot_owned
    for worker in _delivery_workers:
        worker.cancel()
    for worker in _delivery_workers:
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Delivery worker shutdown failed")
    _delivery_workers.clear()

    if _delivery_bot is None:
        return
    if _delivery_bot_owned:
        try:
            await _delivery_bot.session.close()
        except Exception:
            logger.exception("Failed to close owned delivery bot session")
    _delivery_bot = None
    _delivery_bot_owned = False


def _ensure_delivery_workers() -> None:
    if _delivery_workers:
        return
    for idx in range(DELIVERY_WORKERS):
        task = asyncio.create_task(_delivery_worker_loop(idx))
        _delivery_workers.append(task)


async def _send_job_with_retries(bot: Bot, job: DeliveryJob) -> None:
    attempt = 0
    while True:
        attempt += 1
        try:
            if job.photo_bytes is not None:
                filename = f"ad_{int(time.time())}.jpg"
                caption = job.text[:1024] if job.text else None
                caption_entities = [
                    e
                    for e in job.entities
                    if int(getattr(e, "offset", 0)) + int(getattr(e, "length", 0)) <= len(caption or "")
                ]
                await bot.send_photo(
                    chat_id=job.feed_chat_id,
                    photo=BufferedInputFile(job.photo_bytes, filename=filename),
                    caption=caption,
                    caption_entities=caption_entities or None,
                    parse_mode=None,
                    reply_markup=job.keyboard,
                )
            else:
                await bot.send_message(
                    chat_id=job.feed_chat_id,
                    text=job.text,
                    entities=job.entities or None,
                    parse_mode=None,
                    reply_markup=job.keyboard,
                )
            return
        except TelegramRetryAfter as exc:
            if attempt >= DELIVERY_RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(float(getattr(exc, "retry_after", 1)) + 0.25)
        except (TelegramNetworkError, TelegramServerError):
            if attempt >= DELIVERY_RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
        except TelegramBadRequest:
            # Fallback for invalid entities/caption_entities.
            if job.photo_bytes is not None:
                filename = f"ad_{int(time.time())}.jpg"
                await bot.send_photo(
                    chat_id=job.feed_chat_id,
                    photo=BufferedInputFile(job.photo_bytes, filename=filename),
                    caption=(job.text[:1024] if job.text else None),
                    parse_mode=None,
                    reply_markup=job.keyboard,
                )
            else:
                await bot.send_message(
                    chat_id=job.feed_chat_id,
                    text=job.text,
                    parse_mode=None,
                    reply_markup=job.keyboard,
                )
            return
        except TelegramForbiddenError:
            # Permanent for this feed chat until user fixes permissions/settings.
            raise


async def _delivery_worker_loop(worker_idx: int) -> None:
    while True:
        job = await _delivery_queue.get()
        try:
            bot = await _get_delivery_bot()
            if bot is None:
                logger.warning("Delivery bot unavailable, dropping queued job")
                continue
            await _send_job_with_retries(bot, job)
        except Exception:
            logger.exception(
                "Delivery worker %s failed sending job to feed %s",
                worker_idx,
                job.feed_chat_id,
            )
        finally:
            _delivery_queue.task_done()


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


def _convert_entities_to_bot_api(entities: list[object] | None) -> list[MessageEntity]:
    if not entities:
        return []

    type_map = {
        "MessageEntityBold": "bold",
        "MessageEntityItalic": "italic",
        "MessageEntityUnderline": "underline",
        "MessageEntityStrike": "strikethrough",
        "MessageEntitySpoiler": "spoiler",
        "MessageEntityCode": "code",
        "MessageEntityPre": "pre",
        "MessageEntityTextUrl": "text_link",
        "MessageEntityUrl": "url",
        "MessageEntityMention": "mention",
        "MessageEntityHashtag": "hashtag",
        "MessageEntityCashtag": "cashtag",
        "MessageEntityBotCommand": "bot_command",
        "MessageEntityEmail": "email",
        "MessageEntityPhone": "phone_number",
        "MessageEntityBlockquote": "blockquote",
        "MessageEntityCustomEmoji": "custom_emoji",
    }

    result: list[MessageEntity] = []
    for entity in entities:
        cls_name = entity.__class__.__name__
        ent_type = type_map.get(cls_name)
        if not ent_type:
            if cls_name == "MessageEntityMentionName":
                ent_type = "text_link"
            else:
                continue

        kwargs: dict[str, object] = {
            "type": ent_type,
            "offset": int(getattr(entity, "offset", 0)),
            "length": int(getattr(entity, "length", 0)),
        }
        if ent_type == "text_link":
            if cls_name == "MessageEntityMentionName":
                user_id = getattr(entity, "user_id", None)
                if user_id is None:
                    continue
                kwargs["url"] = f"tg://user?id={int(user_id)}"
            else:
                url = getattr(entity, "url", None)
                if not url:
                    continue
                kwargs["url"] = str(url)
        if ent_type == "pre":
            language = getattr(entity, "language", None)
            if language:
                kwargs["language"] = str(language)
        if ent_type == "custom_emoji":
            doc_id = getattr(entity, "document_id", None)
            if doc_id is None:
                continue
            kwargs["custom_emoji_id"] = str(int(doc_id))

        result.append(MessageEntity(**kwargs))

    return result


def _is_telegram_link_like(link: str) -> bool:
    raw = (link or "").strip()
    if raw.startswith("@"):
        return True
    if _TG_USERNAME_LINK_RE.fullmatch(raw):
        return True
    if _TG_INVITE_LINK_RE.fullmatch(raw):
        return True
    if _TG_C_LINK_RE.fullmatch(raw):
        return True
    return False


def _cleanup_resolved_link_cache() -> None:
    if len(_resolved_tg_links_cache) < 5000:
        return
    now = time.monotonic()
    stale = [key for key, (_, exp) in _resolved_tg_links_cache.items() if exp <= now]
    for key in stale:
        _resolved_tg_links_cache.pop(key, None)


async def _resolve_telegram_link_channel_id(client: TelegramClient, link: str) -> int | None:
    raw = (link or "").strip()
    if not raw:
        return None
    cache_key = raw.lower()
    now = time.monotonic()
    cached = _resolved_tg_links_cache.get(cache_key)
    if cached and cached[1] > now:
        return cached[0]

    result: int | None = None
    for attempt in range(1, LINK_RESOLVE_RETRY_ATTEMPTS + 1):
        try:
            if raw.startswith("@") or _TG_USERNAME_LINK_RE.fullmatch(raw):
                entity = await client.get_entity(raw)
                if isinstance(entity, Channel):
                    result = int(entity.id)
            elif _TG_INVITE_LINK_RE.fullmatch(raw):
                invite_match = _TG_INVITE_LINK_RE.fullmatch(raw)
                if invite_match:
                    invite = await client(CheckChatInviteRequest(invite_match.group(1)))
                    chat = getattr(invite, "chat", None)
                    if isinstance(chat, Channel):
                        result = int(chat.id)
            elif _TG_C_LINK_RE.fullmatch(raw):
                c_match = _TG_C_LINK_RE.fullmatch(raw)
                if c_match:
                    result = int(c_match.group(1))
            break
        except FloodWaitError as exc:
            if attempt >= LINK_RESOLVE_RETRY_ATTEMPTS:
                return None
            wait_for = max(int(getattr(exc, "seconds", 1)), 1)
            await asyncio.sleep(min(wait_for, 3))
        except (RPCError, asyncio.TimeoutError, OSError):
            if attempt >= LINK_RESOLVE_RETRY_ATTEMPTS:
                return None
            await asyncio.sleep(min(0.2 * attempt, 0.8))
        except Exception:
            return None

    if result is not None:
        _resolved_tg_links_cache[cache_key] = (result, now + LINK_RESOLVE_CACHE_TTL_SECONDS)
        _cleanup_resolved_link_cache()
    return result


async def _is_external_link(
    client: TelegramClient,
    link: str,
    source_channel_id: int,
    source_username: str | None,
) -> bool:
    if _is_same_channel_link(link, source_channel_id, source_username):
        return False

    if not _is_telegram_link_like(link):
        return True

    resolved = await _resolve_telegram_link_channel_id(client, link)
    if resolved is None:
        return True
    return int(resolved) != int(source_channel_id)


async def _is_same_channel_link_strict(
    client: TelegramClient,
    link: str,
    source_channel_id: int,
    source_username: str | None,
) -> bool:
    if _is_same_channel_link(link, source_channel_id, source_username):
        return True

    if not _is_telegram_link_like(link):
        return False

    resolved = await _resolve_telegram_link_channel_id(client, link)
    if resolved is None:
        return False
    return int(resolved) == int(source_channel_id)


async def _refresh_runtime_state(runtime: UserRuntime, force_rebind: bool = False) -> None:
    monitors = await get_monitored_channel_service().get_user_monitors(runtime.tg_user_id)
    monitor_ids = {int(row[2]) for row in monitors if int(row[2]) > 0}
    feed = await get_user_session_service().get_user_feed(runtime.tg_user_id)
    feed_chat_id = int(feed[0]) if feed is not None else None

    need_rebind = force_rebind or monitor_ids != runtime.monitor_ids
    runtime.monitor_ids = monitor_ids
    runtime.feed_chat_id = feed_chat_id

    if not need_rebind:
        return

    if runtime.handler is not None:
        try:
            runtime.client.remove_event_handler(runtime.handler)
        except Exception:
            logger.exception("Failed removing old monitor handler for user %s", runtime.tg_user_id)
        runtime.handler = None

    if not runtime.monitor_ids:
        logger.info("Monitor handler disabled for user %s (no monitors)", runtime.tg_user_id)
        return

    chats = [PeerChannel(channel_id=i) for i in sorted(runtime.monitor_ids)]

    @runtime.client.on(events.NewMessage(chats=chats))
    async def _on_new_message(event):
        await _handle_monitor_message(runtime, event)

    runtime.handler = _on_new_message
    logger.info(
        "Monitor handler updated for user %s (monitors=%s)",
        runtime.tg_user_id,
        len(runtime.monitor_ids),
    )


async def _runtime_sync_loop(runtime: UserRuntime) -> None:
    while True:
        try:
            if not runtime.client.is_connected():
                await runtime.client.connect()
                runtime.accepted_from_ts = time.time() - 2
                await _refresh_runtime_state(runtime, force_rebind=True)
            await _refresh_runtime_state(runtime)
        except Exception:
            logger.exception("Runtime sync failed for user %s", runtime.tg_user_id)
        await asyncio.sleep(RUNTIME_SYNC_INTERVAL_SECONDS)


async def _enqueue_delivery(job: DeliveryJob) -> None:
    await _delivery_queue.put(job)


async def _handle_monitor_message(runtime: UserRuntime, event) -> None:
    try:
        msg = event.message
        if msg is None:
            return
        if not isinstance(getattr(msg, "peer_id", None), PeerChannel):
            return

        msg_date = getattr(msg, "date", None)
        msg_ts = None
        if isinstance(msg_date, datetime):
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            msg_ts = msg_date.timestamp()
        if msg_ts is not None:
            # Drop stale backlog after sleep/resume and very old updates.
            if msg_ts < runtime.accepted_from_ts:
                return
            if (time.time() - msg_ts) > STALE_MESSAGE_MAX_AGE_SECONDS:
                return

        source_channel_id = int(msg.peer_id.channel_id)
        if source_channel_id not in runtime.monitor_ids:
            return

        feed_chat_id = runtime.feed_chat_id
        if feed_chat_id is None:
            return

        chat = event.chat or await event.get_chat()
        source_username = str(getattr(chat, "username", "") or "").strip() or None

        plain_text = str(getattr(msg, "message", "") or "")
        raw_entities = list(getattr(msg, "entities", None) or [])
        bot_entities = _convert_entities_to_bot_api(raw_entities)
        links = _extract_links(plain_text, raw_entities)

        # If post references its own source channel, skip it entirely.
        for link in links:
            if await _is_same_channel_link_strict(runtime.client, link, source_channel_id, source_username):
                return

        external_link: str | None = None
        keyboard: InlineKeyboardMarkup | None = None
        post_link = _build_post_link(source_channel_id, source_username, int(msg.id))
        for candidate in links:
            if not await _is_external_link(runtime.client, candidate, source_channel_id, source_username):
                continue
            candidate_kb = _build_forward_keyboard(post_link, candidate)
            if candidate_kb is None:
                continue
            external_link = candidate
            keyboard = candidate_kb
            break
        if not external_link:
            return
        if keyboard is None:
            return

        has_photo = getattr(msg, "photo", None) is not None
        if has_photo:
            image_bytes = await runtime.client.download_media(msg, file=bytes)
            if image_bytes:
                await _enqueue_delivery(
                    DeliveryJob(
                        feed_chat_id=feed_chat_id,
                        text=plain_text or external_link,
                        entities=bot_entities,
                        keyboard=keyboard,
                        photo_bytes=image_bytes,
                    )
                )
                return

        payload_plain = plain_text or external_link
        await _enqueue_delivery(
            DeliveryJob(
                feed_chat_id=feed_chat_id,
                text=payload_plain,
                entities=bot_entities,
                keyboard=keyboard,
            )
        )
    except Exception:
        logger.exception("Failed to handle monitor message for user %s", runtime.tg_user_id)


def _attach_monitor_handler(tg_user_id: int, client: TelegramClient) -> None:
    if tg_user_id in _user_runtimes:
        return

    runtime = UserRuntime(tg_user_id=tg_user_id, client=client)
    _user_runtimes[tg_user_id] = runtime

    async def _bootstrap() -> None:
        try:
            await _refresh_runtime_state(runtime, force_rebind=True)
        except Exception:
            logger.exception("Failed bootstrap runtime state for user %s", tg_user_id)
        runtime.sync_task = asyncio.create_task(_runtime_sync_loop(runtime))

    asyncio.create_task(_bootstrap())


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
        if tg_user_id not in _user_runtimes:
            _attach_monitor_handler(tg_user_id, _active_clients[tg_user_id])
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
    runtime = _user_runtimes.pop(tg_user_id, None)

    if runtime is not None:
        if runtime.sync_task is not None:
            runtime.sync_task.cancel()
            try:
                await runtime.sync_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Failed stopping runtime sync task for user %s", tg_user_id)
        if client is not None and runtime.handler is not None:
            try:
                client.remove_event_handler(runtime.handler)
            except Exception:
                logger.exception("Failed to remove monitor handler for user %s", tg_user_id)

    if client:
        await client.disconnect()
        logger.info("Stopped active userbot client for user %s", tg_user_id)


async def logout_userbot_session(tg_user_id: int, session_str: str | None) -> bool:
    # 1) Use active in-memory client if available.
    client = _active_clients.pop(tg_user_id, None)
    runtime = _user_runtimes.pop(tg_user_id, None)

    if runtime is not None and runtime.sync_task is not None:
        runtime.sync_task.cancel()
        try:
            await runtime.sync_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Failed stopping runtime sync task for user %s", tg_user_id)

    if client:
        try:
            if runtime is not None and runtime.handler is not None:
                try:
                    client.remove_event_handler(runtime.handler)
                except Exception:
                    logger.exception("Failed removing runtime handler for user %s", tg_user_id)
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
