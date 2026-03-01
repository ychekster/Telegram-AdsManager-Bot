from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.base import SessionFactory
from app.db.models import MonitoredChannel


class MonitoredChannelRepository:
    async def add_channels(self, tg_user_id: int, channels: list[dict[str, object]]) -> None:
        if not channels:
            return

        dedup_by_id: dict[int, dict[str, object]] = {}
        for channel in channels:
            dedup_by_id[int(channel["channel_id"])] = channel
        channels = list(dedup_by_id.values())

        values = [
            {
                "user_tg_id": tg_user_id,
                "channel_link": str(channel["channel_link"]),
                "channel_id": int(channel["channel_id"]),
                "access_hash": int(channel["access_hash"]),
                "channel_title": str(channel["channel_title"]),
                "created_at": datetime.now(timezone.utc),
            }
            for channel in channels
        ]

        async with SessionFactory() as session:
            dialect_name = session.bind.dialect.name if session.bind else ""
            channel_ids = [int(channel["channel_id"]) for channel in channels]
            existing_stmt = select(MonitoredChannel.channel_id).where(
                MonitoredChannel.user_tg_id == tg_user_id,
                MonitoredChannel.channel_id.in_(channel_ids),
            )
            existing = set((await session.execute(existing_stmt)).scalars().all())
            values = [value for value in values if value["channel_id"] not in existing]
            if not values:
                return

            if dialect_name == "postgresql":
                stmt = (
                    pg_insert(MonitoredChannel)
                    .values(values)
                    .on_conflict_do_nothing()
                )
                await session.execute(stmt)
                await session.commit()
                return

            if dialect_name == "sqlite":
                stmt = (
                    sqlite_insert(MonitoredChannel)
                    .values(values)
                    .on_conflict_do_nothing()
                )
                await session.execute(stmt)
                await session.commit()
                return

            for value in values:
                session.add(MonitoredChannel(**value))
            await session.commit()

    async def get_user_channels(self, tg_user_id: int) -> list[str]:
        async with SessionFactory() as session:
            stmt = (
                select(MonitoredChannel.channel_link)
                .where(MonitoredChannel.user_tg_id == tg_user_id)
                .order_by(MonitoredChannel.created_at.asc(), MonitoredChannel.id.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return list(rows)

    async def get_user_monitors(
        self,
        tg_user_id: int,
    ) -> list[tuple[int, str, int, str, datetime]]:
        async with SessionFactory() as session:
            stmt = (
                select(
                    MonitoredChannel.id,
                    MonitoredChannel.channel_title,
                    MonitoredChannel.channel_id,
                    MonitoredChannel.channel_link,
                    MonitoredChannel.created_at,
                )
                .where(MonitoredChannel.user_tg_id == tg_user_id)
                .order_by(MonitoredChannel.created_at.asc(), MonitoredChannel.id.asc())
            )
            rows = (await session.execute(stmt)).all()
            return [
                (
                    int(m_id),
                    str(channel_title or "Без названия"),
                    int(channel_id) if channel_id is not None else 0,
                    str(channel_link),
                    created_at,
                )
                for m_id, channel_title, channel_id, channel_link, created_at in rows
            ]

    async def get_user_monitor_by_id(
        self,
        tg_user_id: int,
        monitor_id: int,
    ) -> tuple[int, str, int, int, str, datetime] | None:
        async with SessionFactory() as session:
            stmt = (
                select(
                    MonitoredChannel.id,
                    MonitoredChannel.channel_title,
                    MonitoredChannel.channel_id,
                    MonitoredChannel.access_hash,
                    MonitoredChannel.channel_link,
                    MonitoredChannel.created_at,
                )
                .where(
                    MonitoredChannel.user_tg_id == tg_user_id,
                    MonitoredChannel.id == monitor_id,
                )
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
            if not row:
                return None

            m_id, channel_title, channel_id, access_hash, channel_link, created_at = row

            return (
                int(m_id),
                str(channel_title or "Без названия"),
                int(channel_id) if channel_id is not None else 0,
                int(access_hash) if access_hash is not None else 0,
                str(channel_link),
                created_at,
            )

    async def delete_user_monitors(self, tg_user_id: int, monitor_ids: list[int]) -> int:
        if not monitor_ids:
            return 0

        async with SessionFactory() as session:
            stmt = (
                delete(MonitoredChannel)
                .where(
                    MonitoredChannel.user_tg_id == tg_user_id,
                    MonitoredChannel.id.in_(monitor_ids),
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)
