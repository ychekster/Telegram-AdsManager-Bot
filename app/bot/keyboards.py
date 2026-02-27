from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardMarkup, KeyboardButton)


auth_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Номер'),
        KeyboardButton(text='QR-код')],  
    ],
    resize_keyboard=True
)


deauth_confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text='Отменить', callback_data='deauth_cancel'),
        InlineKeyboardButton(text='Подтвердить', callback_data='deauth_confirm'),
    ]
])
