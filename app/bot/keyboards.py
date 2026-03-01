from app.bot.texts.keyboards_texts import *
from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardMarkup, KeyboardButton)


auth_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=AUTH_METHOD_NUMBER),
        KeyboardButton(text=AUTH_METHOD_QR)],  
    ],
    resize_keyboard=True
)


deauth_confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text=KB_CANCEL, callback_data='deauth_cancel'),
        InlineKeyboardButton(text=KB_CONFIRM, callback_data='deauth_confirm'),
    ]
])


monitor_save_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text='Сохранить', callback_data='monitor_save'),
        InlineKeyboardButton(text='Добавить ещё', callback_data='monitor_add_more'),
    ]
])
