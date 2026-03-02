from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.db.base import SessionFactory
from app.db.models import User


class UserRepository:
    async def add_user_if_not_exists(self, tg_id: int) -> None:
        async with SessionFactory() as session:
            dialect_name = session.bind.dialect.name if session.bind else ""

            if dialect_name == "postgresql":
                stmt = (
                    pg_insert(User)
                    .values(
                        tg_id=tg_id,
                        registered_at=datetime.now(timezone.utc),
                        session_str=None,
                    )
                    .on_conflict_do_nothing(index_elements=[User.tg_id])
                )
                await session.execute(stmt)
                await session.commit()
                return

            if dialect_name == "sqlite":
                stmt = (
                    sqlite_insert(User)
                    .values(
                        tg_id=tg_id,
                        registered_at=datetime.now(timezone.utc),
                        session_str=None,
                    )
                    .on_conflict_do_nothing(index_elements=[User.tg_id])
                )
                await session.execute(stmt)
                await session.commit()
                return

            # Fallback for other dialects
            exists_stmt = select(User.tg_id).where(User.tg_id == tg_id)
            exists = await session.scalar(exists_stmt)
            if exists is not None:
                return

            session.add(
                User(
                    tg_id=tg_id,
                    registered_at=datetime.now(timezone.utc),
                    session_str=None,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def set_user_session(self, tg_id: int, session_str: str) -> None:
        await self.add_user_if_not_exists(tg_id)
        async with SessionFactory() as session:
            stmt = update(User).where(User.tg_id == tg_id).values(session_str=session_str)
            await session.execute(stmt)
            await session.commit()

    async def get_user_session(self, tg_id: int) -> str | None:
        async with SessionFactory() as session:
            stmt = select(User.session_str).where(User.tg_id == tg_id)
            return await session.scalar(stmt)

    async def get_all_sessions(self) -> list[tuple[int, str]]:
        async with SessionFactory() as session:
            stmt = select(User.tg_id, User.session_str).where(User.session_str.is_not(None))
            rows = (await session.execute(stmt)).all()
            return [(int(tg_id), session_str) for tg_id, session_str in rows if session_str]

    async def clear_user_session(self, tg_id: int) -> None:
        async with SessionFactory() as session:
            stmt = update(User).where(User.tg_id == tg_id).values(session_str=None)
            await session.execute(stmt)
            await session.commit()

    async def set_user_feed(self, tg_id: int, feed_chat_id: int, feed_chat_title: str) -> None:
        await self.add_user_if_not_exists(tg_id)
        async with SessionFactory() as session:
            stmt = (
                update(User)
                .where(User.tg_id == tg_id)
                .values(feed_chat_id=feed_chat_id, feed_chat_title=feed_chat_title)
            )
            await session.execute(stmt)
            await session.commit()

    async def get_user_feed(self, tg_id: int) -> tuple[int, str] | None:
        async with SessionFactory() as session:
            stmt = select(User.feed_chat_id, User.feed_chat_title).where(User.tg_id == tg_id).limit(1)
            row = (await session.execute(stmt)).first()
            if not row:
                return None
            feed_chat_id, feed_chat_title = row
            if feed_chat_id is None:
                return None
            return int(feed_chat_id), str(feed_chat_title or "")

    async def clear_user_feed(self, tg_id: int) -> None:
        async with SessionFactory() as session:
            stmt = (
                update(User)
                .where(User.tg_id == tg_id)
                .values(feed_chat_id=None, feed_chat_title=None)
            )
            await session.execute(stmt)
            await session.commit()
