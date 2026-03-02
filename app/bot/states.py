from aiogram.fsm.state import State, StatesGroup


class Auth(StatesGroup):
    choosing_method = State()
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()
    scanning_qr = State()


class Monitor(StatesGroup):
    waiting_channels = State()
    waiting_confirm = State()


class DelMonitor(StatesGroup):
    selecting = State()


class Feed(StatesGroup):
    waiting_channel_forward = State()
