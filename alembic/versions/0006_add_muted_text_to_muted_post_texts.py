"""add muted_text column to muted_post_texts

Revision ID: 0006_add_muted_text_to_muted_post_texts
Revises: 0005_create_muted_post_texts_table
Create Date: 2026-03-05 18:20:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0006_add_muted_text_to_muted_post_texts"
down_revision = "0005_create_muted_post_texts_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("muted_post_texts") as batch_op:
        batch_op.add_column(sa.Column("muted_text", sa.Text(), nullable=True))
    op.execute("UPDATE muted_post_texts SET muted_text = '' WHERE muted_text IS NULL")
    with op.batch_alter_table("muted_post_texts") as batch_op:
        batch_op.alter_column("muted_text", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("muted_post_texts") as batch_op:
        batch_op.drop_column("muted_text")
