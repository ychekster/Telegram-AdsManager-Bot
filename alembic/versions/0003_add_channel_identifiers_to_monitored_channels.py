"""add channel identifiers to monitored channels

Revision ID: 0003_add_channel_identifiers_to_monitored_channels
Revises: 0002_create_monitored_channels_table
Create Date: 2026-03-01 12:30:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003_add_channel_identifiers_to_monitored_channels"
down_revision = "0002_create_monitored_channels_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("monitored_channels") as batch_op:
        batch_op.add_column(sa.Column("channel_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("access_hash", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("channel_title", sa.Text(), nullable=True))
        batch_op.drop_constraint("uq_user_channel_link", type_="unique")
        batch_op.create_unique_constraint(
            "uq_user_channel_id",
            ["user_tg_id", "channel_id"],
        )
        batch_op.create_index(
            "ix_monitored_channels_channel_id",
            ["channel_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("monitored_channels") as batch_op:
        batch_op.drop_index("ix_monitored_channels_channel_id")
        batch_op.drop_constraint("uq_user_channel_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_user_channel_link",
            ["user_tg_id", "channel_link"],
        )
        batch_op.drop_column("channel_title")
        batch_op.drop_column("access_hash")
        batch_op.drop_column("channel_id")
