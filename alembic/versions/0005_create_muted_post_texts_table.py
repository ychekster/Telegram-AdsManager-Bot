"""create muted post texts table

Revision ID: 0005_create_muted_post_texts_table
Revises: 0004_add_feed_channel_to_users
Create Date: 2026-03-05 10:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_create_muted_post_texts_table"
down_revision = "0004_add_feed_channel_to_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "muted_post_texts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("text_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_tg_id", "text_hash", name="uq_muted_post_text_user_hash"),
    )
    op.create_index(
        "ix_muted_post_texts_user_tg_id",
        "muted_post_texts",
        ["user_tg_id"],
        unique=False,
    )
    op.create_index(
        "ix_muted_post_texts_expires_at",
        "muted_post_texts",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_muted_post_texts_expires_at", table_name="muted_post_texts")
    op.drop_index("ix_muted_post_texts_user_tg_id", table_name="muted_post_texts")
    op.drop_table("muted_post_texts")
