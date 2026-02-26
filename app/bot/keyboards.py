from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardMarkup, KeyboardButton)


auth_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Номер'),
        KeyboardButton(text='QR-код')],  
    ],
    resize_keyboard=True
)