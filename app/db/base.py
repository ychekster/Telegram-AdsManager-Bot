from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.db.config import (
    get_database_url,
    get_db_echo,
    get_db_max_overflow,
    get_db_pool_size,
)


class Base(DeclarativeBase):
    pass


def create_engine() -> AsyncEngine:
    database_url = get_database_url()
    engine_kwargs = {"echo": get_db_echo()}

    # SQLite does not support pool sizing in the same way as server DBs.
    if not database_url.startswith("sqlite"):
        engine_kwargs["pool_size"] = get_db_pool_size()
        engine_kwargs["max_overflow"] = get_db_max_overflow()

    return create_async_engine(database_url, **engine_kwargs)


engine = create_engine()
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
