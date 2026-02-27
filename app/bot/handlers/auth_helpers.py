from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from app.db.database import get_user_session


async def set_2fa_prompt_msg_id(state: FSMContext, message_id: int | None):
    await state.update_data(twofa_prompt_message_id=message_id)

async def delete_2fa_prompt_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt_id = data.get("twofa_prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
    await state.update_data(twofa_prompt_message_id=None)

async def is_userbot_already_authorized(tg_user_id: int) -> bool:
    session_str = await get_user_session(tg_user_id)
    return bool(session_str and session_str.strip())