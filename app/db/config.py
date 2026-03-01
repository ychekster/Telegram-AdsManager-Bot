import os


def get_database_url() -> str:
    # Default local DB for development; production should override via .env
    return os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./database.db")


def get_db_echo() -> bool:
    return os.getenv("DB_ECHO", "false").lower() in {"1", "true", "yes", "on"}


def get_db_pool_size() -> int:
    return int(os.getenv("DB_POOL_SIZE", "5"))


def get_db_max_overflow() -> int:
    return int(os.getenv("DB_MAX_OVERFLOW", "10"))
