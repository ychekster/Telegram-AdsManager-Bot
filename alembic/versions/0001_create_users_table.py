"""create users table

Revision ID: 0001_create_users_table
Revises:
Create Date: 2026-03-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_create_users_table"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("tg_id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_str", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("users")
