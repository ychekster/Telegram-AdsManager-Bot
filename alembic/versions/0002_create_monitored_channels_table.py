"""create monitored channels table

Revision ID: 0002_create_monitored_channels_table
Revises: 0001_create_users_table
Create Date: 2026-03-01 00:10:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_create_monitored_channels_table"
down_revision = "0001_create_users_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitored_channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_link", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_tg_id", "channel_link", name="uq_user_channel_link"),
    )
    op.create_index(
        "ix_monitored_channels_user_tg_id",
        "monitored_channels",
        ["user_tg_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_monitored_channels_user_tg_id", table_name="monitored_channels")
    op.drop_table("monitored_channels")
