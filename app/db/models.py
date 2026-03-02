from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    session_str: Mapped[str | None] = mapped_column(Text, nullable=True)
    feed_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    feed_chat_title: Mapped[str | None] = mapped_column(Text, nullable=True)


class MonitoredChannel(Base):
    __tablename__ = "monitored_channels"
    __table_args__ = (
        UniqueConstraint("user_tg_id", "channel_id", name="uq_user_channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_link: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    access_hash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
