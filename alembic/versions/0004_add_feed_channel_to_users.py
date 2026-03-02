"""add feed channel to users

Revision ID: 0004_add_feed_channel_to_users
Revises: 0003_add_channel_identifiers_to_monitored_channels
Create Date: 2026-03-02 00:30:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004_add_feed_channel_to_users"
down_revision = "0003_add_channel_identifiers_to_monitored_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("feed_chat_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("feed_chat_title", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("feed_chat_title")
        batch_op.drop_column("feed_chat_id")
