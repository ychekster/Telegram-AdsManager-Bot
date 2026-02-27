import aiosqlite
from datetime import datetime


DB_PATH = 'database.db'


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                registered_at TEXT NOT NULL,
                session_str TEXT
            )
        ''')
        await db.commit()


async def add_user_if_not_exists(tg_id: int): # добавляем пользователя в БД, если его там нет
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT tg_id FROM users WHERE tg_id = ?', (tg_id,)) # проверяем, есть ли уже пользователь с таким tg_id
        exists = await cursor.fetchone() # получаем результат запроса (будет None, если пользователь не найден)
        if exists:
            return
        
        registered_at = datetime.now().isoformat() # сохраняем время регистрации в ISO формате (московское время)
        await db.execute(
            'INSERT INTO users (tg_id, registered_at, session_str) VALUES (?, ?, ?)', 
            (tg_id, registered_at, None)
        ) 
        await db.commit()


async def set_user_session(tg_id: int, session_str: str): # сохраняем строку сессии в БД для пользователя с данным tg_id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE users SET session_str = ? WHERE tg_id = ?', 
            (session_str, tg_id)
        )
        await db.commit()


async def get_user_session(tg_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT session_str FROM users WHERE tg_id = ?', (tg_id,)
        )
        result = await cursor.fetchone()
        return result[0] if result else None
    

async def get_all_sessions() -> list[tuple[int, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT tg_id, session_str FROM users WHERE session_str IS NOT NULL"
        )
        return await cursor.fetchall()


async def clear_user_session(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE users SET session_str = NULL WHERE tg_id = ?',
            (tg_id,)
        )
        await db.commit()