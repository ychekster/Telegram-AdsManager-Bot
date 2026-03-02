import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberOwner,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    ReplyKeyboardRemove,
)
from telethon.errors import RPCError
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import Channel

from app.bot import keyboards as kb
from app.bot.handlers.auth_helpers import delete_2fa_prompt_msg, is_userbot_already_authorized
from app.bot.handlers.common_helpers import safe_html
from app.bot.states import DelMonitor, Feed, Monitor
from app.bot.texts.common_texts import (
    CANCEL_TEXT,
    DELFEED_CANCELLED,
    DELFEED_CONFIRM,
    DELFEED_FAILED,
    DELFEED_MESSAGE_UNAVAILABLE,
    DELFEED_NOT_SET,
    DELFEED_SUCCESS,
    DELFEED_UNAVAILABLE,
    DELMONITOR_DONE,
    DELMONITOR_FLOW_EXPIRED,
    DELMONITOR_NOT_SELECTED,
    DELMONITOR_PICK,
    HELP_TEXT,
    MONITOR_DELETE_CONFIRM,
    MONITOR_DELETED,
    MONITOR_FLOW_EXPIRED,
    MONITOR_INFO_DETAILS_TEMPLATE,
    MONITOR_NO_VALID_LINKS,
    MONITOR_NOT_FOUND,
    MONITOR_PROMPT,
    MONITOR_PROMPT_MORE,
    MONITOR_RESOLVE_FAILED,
    MONITOR_SAVED_TEMPLATE,
    MONITOR_SAVE_QUESTION,
    MONITOR_UNAVAILABLE,
    MONITORS_EMPTY,
    MONITORS_LOAD_FAILED,
    MONITORS_PICK,
    PAGINATION_END_HINT,
    SETFEED_BOT_NOT_MEMBER,
    SETFEED_BOT_NO_POST_RIGHTS,
    SETFEED_ALREADY_SET,
    SETFEED_CHECK_FAILED,
    SETFEED_FORWARD_REQUIRED,
    SETFEED_NOT_CHANNEL,
    SETFEED_PROMPT,
    SETFEED_SUCCESS,
    SETFEED_UNAVAILABLE,
    SETTINGS_FEED_DETAILS_TEMPLATE,
    SETTINGS_LOAD_FAILED,
    SETTINGS_TEXT,
    SETTINGS_UNAVAILABLE,
)
from app.db.services import get_monitored_channel_service, get_user_session_service
from app.userbot.client import InvalidStoredSessionError, delete_client, start_active_client


common_router = Router(name="common")
user_session_service = get_user_session_service()
monitored_channel_service = get_monitored_channel_service()

CHANNEL_USERNAME_RE = re.compile(r"^@([A-Za-z][A-Za-z0-9_]{3,31})$")
CHANNEL_LINK_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([A-Za-z][A-Za-z0-9_]{3,31})(?:/)?(?:\?.*)?$",
    re.IGNORECASE,
)
INVITE_LINK_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me)/\+([A-Za-z0-9_-]{6,})(?:/)?(?:\?.*)?$",
    re.IGNORECASE,
)
MONITORS_PER_PAGE = 8


def _monitor_button_text(title: str) -> str:
    return title if len(title) <= 32 else f"{title[:29]}..."


def _build_monitors_page_kb(monitors: list[tuple[int, str, int, str, object]], page: int) -> InlineKeyboardMarkup:
    total = len(monitors)
    total_pages = max(1, (total + MONITORS_PER_PAGE - 1) // MONITORS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * MONITORS_PER_PAGE
    end = min(start + MONITORS_PER_PAGE, total)
    page_monitors = monitors[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(page_monitors), 2):
        row: list[InlineKeyboardButton] = []
        for offset in (0, 1):
            real_idx = idx + offset
            if real_idx >= len(page_monitors):
                break
            monitor_id, title, _, _, _ = page_monitors[real_idx]
            row.append(
                InlineKeyboardButton(
                    text=_monitor_button_text(title),
                    callback_data=f"monitor_open:{monitor_id}:{page}",
                )
            )
        rows.append(row)

    prev_callback = f"monitors_page:{page - 1}" if page > 0 else "monitors_nav_noop"
    next_callback = f"monitors_page:{page + 1}" if page < total_pages - 1 else "monitors_nav_noop"
    rows.append(
        [
            InlineKeyboardButton(text="\u2039", callback_data=prev_callback),
            InlineKeyboardButton(text="\u203a", callback_data=next_callback),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_delmonitors_page_kb(
    monitors: list[tuple[int, str, int, str, object]],
    page: int,
    selected_ids: set[int],
) -> InlineKeyboardMarkup:
    total = len(monitors)
    total_pages = max(1, (total + MONITORS_PER_PAGE - 1) // MONITORS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * MONITORS_PER_PAGE
    end = min(start + MONITORS_PER_PAGE, total)
    page_monitors = monitors[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(page_monitors), 2):
        row: list[InlineKeyboardButton] = []
        for offset in (0, 1):
            real_idx = idx + offset
            if real_idx >= len(page_monitors):
                break
            monitor_id, title, _, _, _ = page_monitors[real_idx]
            prefix = "✅ " if monitor_id in selected_ids else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{prefix}{_monitor_button_text(title)}",
                    callback_data=f"monitor_del_toggle:{monitor_id}:{page}",
                )
            )
        rows.append(row)

    prev_callback = f"delmonitors_page:{page - 1}" if page > 0 else "monitors_nav_noop"
    next_callback = f"delmonitors_page:{page + 1}" if page < total_pages - 1 else "monitors_nav_noop"
    rows.append(
        [
            InlineKeyboardButton(text="\u2039", callback_data=prev_callback),
            InlineKeyboardButton(text="\u203a", callback_data=next_callback),
        ]
    )
    rows.append([InlineKeyboardButton(text="Удалить", callback_data="delmonitors_commit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_monitor_card_kb(monitor_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Удалить", callback_data=f"monitor_delete_prompt:{monitor_id}:{page}")],
            [InlineKeyboardButton(text="\u2039 Назад", callback_data=f"monitor_back:{page}")],
        ]
    )


def _build_monitor_delete_confirm_kb(monitor_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data=f"monitor_delete_confirm:{monitor_id}:{page}")],
            [InlineKeyboardButton(text="\u2039 Назад", callback_data=f"monitor_delete_cancel:{monitor_id}:{page}")],
        ]
    )


def _build_monitor_deleted_back_kb(page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="\u2039 Вернуться к мониторам", callback_data=f"monitor_deleted_back:{page}")]
        ]
    )


def _build_delfeed_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отменить", callback_data="delfeed_cancel"),
                InlineKeyboardButton(text="Подтвердить", callback_data="delfeed_confirm"),
            ]
        ]
    )


async def _render_monitors_page(message: Message, user_id: int, page: int):
    try:
        monitors = await monitored_channel_service.get_user_monitors(user_id)
    except Exception:
        await message.answer(MONITORS_LOAD_FAILED)
        return
    if not monitors:
        await message.answer(MONITORS_EMPTY)
        return
    await message.answer(MONITORS_PICK, reply_markup=_build_monitors_page_kb(monitors, page))


async def _edit_monitors_page(callback: CallbackQuery, page: int):
    if not callback.message:
        return

    try:
        monitors = await monitored_channel_service.get_user_monitors(callback.from_user.id)
    except Exception:
        await callback.message.edit_text(MONITORS_LOAD_FAILED)
        return

    if not monitors:
        await callback.message.edit_text(MONITORS_EMPTY)
        return

    await callback.message.edit_text(MONITORS_PICK, reply_markup=_build_monitors_page_kb(monitors, page))


async def _edit_delmonitors_page(callback: CallbackQuery, state: FSMContext, page: int):
    if not callback.message:
        return

    data = await state.get_data()
    selected_ids = set(int(v) for v in data.get("delmonitor_selected_ids", []))

    try:
        monitors = await monitored_channel_service.get_user_monitors(callback.from_user.id)
    except Exception:
        await callback.message.edit_text(MONITORS_LOAD_FAILED)
        return

    if not monitors:
        await state.clear()
        await callback.message.edit_text(MONITORS_EMPTY)
        return

    await state.update_data(delmonitor_page=page)
    await callback.message.edit_text(
        DELMONITOR_PICK,
        reply_markup=_build_delmonitors_page_kb(monitors, page, selected_ids),
    )


async def _render_monitor_card(callback: CallbackQuery, monitor_id: int, page: int):
    if not callback.message:
        return

    monitor = await monitored_channel_service.get_user_monitor_by_id(callback.from_user.id, monitor_id)
    if not monitor:
        await callback.message.edit_text(MONITOR_NOT_FOUND)
        return

    _, channel_title, channel_id, _, channel_link, _ = monitor
    text = MONITOR_INFO_DETAILS_TEMPLATE.format(
        channel_title=safe_html(channel_title),
        channel_id=safe_html(channel_id),
        channel_link=safe_html(channel_link),
    )
    await callback.message.edit_text(text, reply_markup=_build_monitor_card_kb(monitor_id, page))


def _parse_channel_links(text: str) -> list[str]:
    if not text:
        return []

    raw_tokens = re.split(r"[\s,;]+", text)
    parsed_links: list[str] = []
    seen: set[str] = set()

    for token in raw_tokens:
        cleaned = token.strip().strip("()[]{}<>\"'.,")
        if not cleaned:
            continue

        normalized = None

        username_match = CHANNEL_USERNAME_RE.fullmatch(cleaned)
        if username_match:
            normalized = f"@{username_match.group(1).lower()}"
        else:
            invite_match = INVITE_LINK_RE.fullmatch(cleaned)
            if invite_match:
                normalized = f"https://t.me/+{invite_match.group(1)}"
            else:
                link_match = CHANNEL_LINK_RE.fullmatch(cleaned)
                if link_match:
                    normalized = f"@{link_match.group(1).lower()}"

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        parsed_links.append(normalized)

    return parsed_links


async def _resolve_channel_meta(client, source_link: str) -> tuple[int, int, str] | None:
    try:
        entity = await client.get_entity(source_link)
        if isinstance(entity, Channel) and entity.access_hash is not None:
            return int(entity.id), int(entity.access_hash), (entity.title or "Без названия")
    except RPCError:
        pass
    except Exception:
        pass

    invite_match = INVITE_LINK_RE.fullmatch(source_link)
    if not invite_match:
        return None

    invite_hash = invite_match.group(1)
    try:
        invite_info = await client(CheckChatInviteRequest(invite_hash))
        chat = getattr(invite_info, "chat", None)
        if isinstance(chat, Channel) and chat.access_hash is not None:
            return int(chat.id), int(chat.access_hash), (chat.title or "Без названия")
    except RPCError:
        return None
    except Exception:
        return None

    return None


def _extract_forwarded_channel(message: Message) -> tuple[int, str] | None:
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        chat = origin.chat
        if chat.type == ChatType.CHANNEL:
            return int(chat.id), str(chat.title or "Без названия")

    forward_from_chat = getattr(message, "forward_from_chat", None)
    if forward_from_chat and getattr(forward_from_chat, "type", None) == ChatType.CHANNEL:
        return int(forward_from_chat.id), str(getattr(forward_from_chat, "title", "") or "Без названия")

    return None


def _bot_can_post_to_channel(member) -> bool:
    if isinstance(member, ChatMemberOwner):
        return True
    if isinstance(member, ChatMemberAdministrator):
        return bool(member.can_post_messages)
    return False


@common_router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await delete_client(message.from_user.id)
    data = await state.get_data()
    qr_msg_id = data.get("qr_message_id")
    if qr_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=qr_msg_id)
        except Exception:
            pass
    await delete_2fa_prompt_msg(message, state)
    await state.clear()
    await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())


@common_router.message(CommandStart())
async def cmd_start(message: Message):
    await user_session_service.add_user_if_not_exists(message.from_user.id)
    await message.answer(HELP_TEXT)


@common_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@common_router.message(Command("setfeed"))
async def cmd_setfeed(message: Message, state: FSMContext):
    if not await is_userbot_already_authorized(message.from_user.id):
        await message.answer(SETFEED_UNAVAILABLE)
        return

    existing_feed = await user_session_service.get_user_feed(message.from_user.id)
    if existing_feed is not None:
        await message.answer(SETFEED_ALREADY_SET)
        return

    await state.set_state(Feed.waiting_channel_forward)
    await message.answer(SETFEED_PROMPT)


@common_router.message(Command("delfeed"))
async def cmd_delfeed(message: Message):
    if not await is_userbot_already_authorized(message.from_user.id):
        await message.answer(DELFEED_UNAVAILABLE)
        return

    feed = await user_session_service.get_user_feed(message.from_user.id)
    if feed is None:
        await message.answer(DELFEED_NOT_SET)
        return

    await message.answer(DELFEED_CONFIRM, reply_markup=_build_delfeed_confirm_kb())


@common_router.message(Feed.waiting_channel_forward)
async def setfeed_wait_channel_forward(message: Message, state: FSMContext):
    existing_feed = await user_session_service.get_user_feed(message.from_user.id)
    if existing_feed is not None:
        await state.clear()
        await message.answer(SETFEED_ALREADY_SET)
        return

    forwarded_channel = _extract_forwarded_channel(message)
    if not forwarded_channel:
        await message.answer(SETFEED_FORWARD_REQUIRED)
        return

    feed_chat_id, fallback_title = forwarded_channel

    try:
        chat = await message.bot.get_chat(feed_chat_id)
    except Exception:
        await message.answer(SETFEED_CHECK_FAILED)
        return

    if chat.type != ChatType.CHANNEL:
        await message.answer(SETFEED_NOT_CHANNEL)
        return

    try:
        bot_user = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(feed_chat_id, bot_user.id)
    except Exception:
        await message.answer(SETFEED_BOT_NOT_MEMBER)
        return

    if not _bot_can_post_to_channel(bot_member):
        await message.answer(SETFEED_BOT_NO_POST_RIGHTS)
        return

    await user_session_service.set_user_feed(
        message.from_user.id,
        feed_chat_id=feed_chat_id,
        feed_chat_title=str(getattr(chat, "title", None) or fallback_title),
    )
    await state.clear()
    await message.answer(SETFEED_SUCCESS)


@common_router.callback_query(F.data == "delfeed_cancel")
async def delfeed_cancel(callback: CallbackQuery):
    await callback.answer()
    if not callback.message:
        await callback.answer(DELFEED_MESSAGE_UNAVAILABLE, show_alert=False)
        return
    await callback.message.edit_text(DELFEED_CANCELLED)


@common_router.callback_query(F.data == "delfeed_confirm")
async def delfeed_confirm(callback: CallbackQuery):
    await callback.answer()
    if not callback.message:
        await callback.answer(DELFEED_MESSAGE_UNAVAILABLE, show_alert=False)
        return

    feed = await user_session_service.get_user_feed(callback.from_user.id)
    if feed is None:
        await callback.message.edit_text(DELFEED_NOT_SET)
        return

    try:
        await user_session_service.clear_user_feed(callback.from_user.id)
    except Exception:
        await callback.message.edit_text(DELFEED_FAILED)
        return

    await callback.message.edit_text(DELFEED_SUCCESS)


@common_router.message(Command("monitors"))
async def cmd_monitors(message: Message):
    await _render_monitors_page(message, message.from_user.id, page=0)


@common_router.message(Command("delmonitor"))
async def cmd_delmonitor(message: Message, state: FSMContext):
    monitors = await monitored_channel_service.get_user_monitors(message.from_user.id)
    if not monitors:
        await message.answer(MONITORS_EMPTY)
        return

    await state.set_state(DelMonitor.selecting)
    await state.update_data(delmonitor_selected_ids=[], delmonitor_page=0)
    await message.answer(DELMONITOR_PICK, reply_markup=_build_delmonitors_page_kb(monitors, 0, set()))


@common_router.message(Command("addmonitor"))
async def cmd_addmonitor(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await is_userbot_already_authorized(user_id):
        await message.answer(MONITOR_UNAVAILABLE)
        return

    await state.set_state(Monitor.waiting_channels)
    await state.update_data(monitor_channels_buffer=[])
    await message.answer(MONITOR_PROMPT)


@common_router.message(Monitor.waiting_channels, ~F.text.startswith("/"))
async def monitor_collect_channels(message: Message, state: FSMContext):
    parsed_links = _parse_channel_links(message.text or "")
    if not parsed_links:
        await message.answer(MONITOR_NO_VALID_LINKS)
        return

    data = await state.get_data()
    buffer_links = list(data.get("monitor_channels_buffer", []))
    dedup: set[str] = set(buffer_links)
    for link in parsed_links:
        if link not in dedup:
            dedup.add(link)
            buffer_links.append(link)

    await state.update_data(monitor_channels_buffer=buffer_links)
    await state.set_state(Monitor.waiting_confirm)
    await message.answer(MONITOR_SAVE_QUESTION, reply_markup=kb.monitor_save_kb)


@common_router.callback_query(Monitor.waiting_confirm, F.data == "monitor_add_more")
async def monitor_add_more(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(Monitor.waiting_channels)
    if callback.message:
        await callback.message.edit_text(MONITOR_PROMPT_MORE)


@common_router.callback_query(Monitor.waiting_confirm, F.data == "monitor_save")
async def monitor_save_channels(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    channel_links = list(data.get("monitor_channels_buffer", []))

    if not channel_links:
        await state.set_state(Monitor.waiting_channels)
        if callback.message:
            await callback.message.edit_text(MONITOR_NO_VALID_LINKS)
        return

    session_str = await user_session_service.get_user_session(callback.from_user.id)
    try:
        client = await start_active_client(callback.from_user.id, session_str)
    except InvalidStoredSessionError:
        await user_session_service.clear_user_session(callback.from_user.id)
        await state.clear()
        if callback.message:
            await callback.message.edit_text(MONITOR_UNAVAILABLE)
        return
    except Exception:
        if callback.message:
            await callback.message.edit_text(MONITOR_RESOLVE_FAILED)
        return

    resolved_channels: list[dict[str, object]] = []
    seen_channel_ids: set[int] = set()
    for link in channel_links:
        resolved = await _resolve_channel_meta(client, link)
        if not resolved:
            continue
        channel_id, access_hash, channel_title = resolved
        if channel_id in seen_channel_ids:
            continue
        seen_channel_ids.add(channel_id)
        resolved_channels.append(
            {
                "channel_link": link,
                "channel_id": channel_id,
                "access_hash": access_hash,
                "channel_title": channel_title,
            }
        )

    if not resolved_channels:
        if callback.message:
            await callback.message.edit_text(MONITOR_RESOLVE_FAILED)
        return

    await monitored_channel_service.add_channels(callback.from_user.id, resolved_channels)
    await state.clear()

    links_text = "\n".join(str(channel["channel_link"]) for channel in resolved_channels)
    if callback.message:
        await callback.message.edit_text(MONITOR_SAVED_TEMPLATE.format(links=links_text))


@common_router.callback_query(DelMonitor.selecting, F.data.startswith("delmonitors_page:"))
async def delmonitors_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page_part = callback.data.split(":", maxsplit=1)[1]
    if not page_part.isdigit():
        return
    await _edit_delmonitors_page(callback, state, int(page_part))


@common_router.callback_query(DelMonitor.selecting, F.data.startswith("monitor_del_toggle:"))
async def delmonitors_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, monitor_id_raw, page_raw = parts
    if not (monitor_id_raw.isdigit() and page_raw.isdigit()):
        return

    monitor_id = int(monitor_id_raw)
    data = await state.get_data()
    selected_ids = set(int(v) for v in data.get("delmonitor_selected_ids", []))
    if monitor_id in selected_ids:
        selected_ids.remove(monitor_id)
    else:
        selected_ids.add(monitor_id)

    await state.update_data(delmonitor_selected_ids=sorted(selected_ids))
    await _edit_delmonitors_page(callback, state, int(page_raw))


@common_router.callback_query(DelMonitor.selecting, F.data == "delmonitors_commit")
async def delmonitors_commit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    selected_ids = [int(v) for v in data.get("delmonitor_selected_ids", [])]
    if not selected_ids:
        await callback.answer(DELMONITOR_NOT_SELECTED, show_alert=True)
        return

    await monitored_channel_service.delete_user_monitors(callback.from_user.id, selected_ids)
    await state.clear()
    if callback.message:
        await callback.message.edit_text(DELMONITOR_DONE)


@common_router.callback_query(F.data == "monitor_add_more")
async def monitor_add_more_expired(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(MONITOR_FLOW_EXPIRED)


@common_router.callback_query(F.data == "monitor_save")
async def monitor_save_expired(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(MONITOR_FLOW_EXPIRED)


@common_router.callback_query(F.data.startswith("delmonitors_page:"))
async def delmonitor_page_expired(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(DELMONITOR_FLOW_EXPIRED)


@common_router.callback_query(F.data.startswith("monitor_del_toggle:"))
async def delmonitor_toggle_expired(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(DELMONITOR_FLOW_EXPIRED)


@common_router.callback_query(F.data == "delmonitors_commit")
async def delmonitor_commit_expired(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(DELMONITOR_FLOW_EXPIRED)


@common_router.callback_query(F.data == "monitors_nav_noop")
async def monitors_nav_noop(callback: CallbackQuery):
    await callback.answer(PAGINATION_END_HINT, show_alert=False)


@common_router.callback_query(F.data.startswith("monitors_page:"))
async def monitors_page(callback: CallbackQuery):
    await callback.answer()
    page_part = callback.data.split(":", maxsplit=1)[1]
    if not page_part.isdigit():
        return
    await _edit_monitors_page(callback, int(page_part))


@common_router.callback_query(F.data.startswith("monitor_open:"))
async def monitor_open(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, monitor_id_raw, page_raw = parts
    if not (monitor_id_raw.isdigit() and page_raw.isdigit()):
        return

    await _render_monitor_card(callback, int(monitor_id_raw), int(page_raw))


@common_router.callback_query(F.data.startswith("monitor_delete_prompt:"))
async def monitor_delete_prompt(callback: CallbackQuery):
    await callback.answer()
    if not callback.message:
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, monitor_id_raw, page_raw = parts
    if not (monitor_id_raw.isdigit() and page_raw.isdigit()):
        return

    await callback.message.edit_text(
        MONITOR_DELETE_CONFIRM,
        reply_markup=_build_monitor_delete_confirm_kb(int(monitor_id_raw), int(page_raw)),
    )


@common_router.callback_query(F.data.startswith("monitor_delete_cancel:"))
async def monitor_delete_cancel(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, monitor_id_raw, page_raw = parts
    if not (monitor_id_raw.isdigit() and page_raw.isdigit()):
        return

    await _render_monitor_card(callback, int(monitor_id_raw), int(page_raw))


@common_router.callback_query(F.data.startswith("monitor_delete_confirm:"))
async def monitor_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    if not callback.message:
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, monitor_id_raw, page_raw = parts
    if not (monitor_id_raw.isdigit() and page_raw.isdigit()):
        return

    deleted = await monitored_channel_service.delete_user_monitors(callback.from_user.id, [int(monitor_id_raw)])
    if deleted <= 0:
        await callback.message.edit_text(MONITOR_NOT_FOUND)
        return

    await callback.message.edit_text(
        MONITOR_DELETED,
        reply_markup=_build_monitor_deleted_back_kb(int(page_raw)),
    )


@common_router.callback_query(F.data.startswith("monitor_deleted_back:"))
async def monitor_deleted_back(callback: CallbackQuery):
    await callback.answer()
    page_part = callback.data.split(":", maxsplit=1)[1]
    if not page_part.isdigit():
        return
    await _edit_monitors_page(callback, int(page_part))


@common_router.callback_query(F.data.startswith("monitor_back:"))
async def monitor_back(callback: CallbackQuery):
    await callback.answer()
    page_part = callback.data.split(":", maxsplit=1)[1]
    if not page_part.isdigit():
        return
    await _edit_monitors_page(callback, int(page_part))


@common_router.message(Command("settings"))
async def cmd_settings(message: Message):
    user_id = message.from_user.id

    if not await is_userbot_already_authorized(user_id):
        await message.answer(SETTINGS_UNAVAILABLE)
        return

    session_str = await user_session_service.get_user_session(user_id)

    try:
        client = await start_active_client(user_id, session_str)
        me = await client.get_me()
    except InvalidStoredSessionError:
        await user_session_service.clear_user_session(user_id)
        await message.answer(SETTINGS_UNAVAILABLE)
        return
    except Exception:
        await message.answer(SETTINGS_LOAD_FAILED)
        return

    if not me:
        await message.answer(SETTINGS_LOAD_FAILED)
        return

    username = f"@{me.username}" if me.username else "не указано"
    premium = "да" if getattr(me, "premium", False) else "нет"

    text = SETTINGS_TEXT.format(
        id=safe_html(me.id),
        first_name=safe_html(me.first_name),
        last_name=safe_html(me.last_name),
        username=safe_html(username),
        phone=safe_html(me.phone),
        premium=safe_html(premium),
    )
    feed = await user_session_service.get_user_feed(user_id)
    if feed is not None:
        feed_chat_id, feed_title = feed
        text += SETTINGS_FEED_DETAILS_TEMPLATE.format(
            feed_title=safe_html(feed_title or "Без названия"),
            feed_chat_id=safe_html(feed_chat_id),
        )
    await message.answer(text)
