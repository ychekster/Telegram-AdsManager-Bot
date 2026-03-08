from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.base import SessionFactory
from app.db.models import MutedPostText


class MutedPostTextRepository:
    async def should_mute(self, tg_user_id: int, text_hash: str, muted_text: str, ttl_days: int = 7) -> bool:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=ttl_days)

        async with SessionFactory() as session:
            await session.execute(
                delete(MutedPostText).where(
                    and_(
                        MutedPostText.user_tg_id == tg_user_id,
                        MutedPostText.expires_at <= now,
                    )
                )
            )

            existing_stmt = select(MutedPostText.id).where(
                and_(
                    MutedPostText.user_tg_id == tg_user_id,
                    MutedPostText.text_hash == text_hash,
                    MutedPostText.expires_at > now,
                )
            )
            existing_id = await session.scalar(existing_stmt)
            if existing_id is not None:
                await session.commit()
                return True

            dialect_name = session.bind.dialect.name if session.bind else ""
            values = {
                "user_tg_id": tg_user_id,
                "text_hash": text_hash,
                "muted_text": muted_text,
                "expires_at": expires_at,
                "created_at": now,
            }

            if dialect_name == "postgresql":
                stmt = (
                    pg_insert(MutedPostText)
                    .values(**values)
                    .on_conflict_do_update(
                        constraint="uq_muted_post_text_user_hash",
                        set_={"expires_at": expires_at, "muted_text": muted_text},
                    )
                )
                await session.execute(stmt)
            elif dialect_name == "sqlite":
                stmt = (
                    sqlite_insert(MutedPostText)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=[MutedPostText.user_tg_id, MutedPostText.text_hash],
                        set_={"expires_at": expires_at, "muted_text": muted_text},
                    )
                )
                await session.execute(stmt)
            else:
                insert_stmt = select(MutedPostText.id).where(
                    and_(
                        MutedPostText.user_tg_id == tg_user_id,
                        MutedPostText.text_hash == text_hash,
                    )
                )
                existing_any_id = await session.scalar(insert_stmt)
                if existing_any_id is None:
                    session.add(MutedPostText(**values))
                else:
                    await session.execute(
                        update(MutedPostText)
                        .where(MutedPostText.id == existing_any_id)
                        .values(expires_at=expires_at, muted_text=muted_text)
                    )

            await session.commit()
            return False

    async def get_user_muted_texts(self, tg_user_id: int) -> list[str]:
        now = datetime.now(timezone.utc)
        async with SessionFactory() as session:
            await session.execute(
                delete(MutedPostText).where(
                    and_(
                        MutedPostText.user_tg_id == tg_user_id,
                        MutedPostText.expires_at <= now,
                    )
                )
            )
            stmt = (
                select(MutedPostText.muted_text)
                .where(
                    and_(
                        MutedPostText.user_tg_id == tg_user_id,
                        MutedPostText.expires_at > now,
                    )
                )
                .order_by(MutedPostText.created_at.asc(), MutedPostText.id.asc())
            )
            rows = (await session.execute(stmt)).all()
            await session.commit()
            return [str(row[0] or "") for row in rows if row]
