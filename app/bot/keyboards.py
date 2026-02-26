from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardMarkup, KeyboardButton)


auth_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Номер'),
        KeyboardButton(text='QR-код')],  
    ],
    resize_keyboard=True
)













# auth_kb = InlineKeyboardMarkup(inline_keyboard=[
#     [InlineKeyboardButton(text='Номер', callback_data='auth_number'),
#     InlineKeyboardButton(text='QR-код', callback_data='auth_qr'),]
# ])


# back_to_auth_kb = InlineKeyboardMarkup(inline_keyboard=[
#     [InlineKeyboardButton(text='‹ Назад', callback_data='back_to_auth')],
# ])