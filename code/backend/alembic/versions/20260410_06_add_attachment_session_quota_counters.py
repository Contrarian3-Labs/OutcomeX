"""Add attachment session quota counters.

Revision ID: 20260410_06
Revises: 20260410_05
Create Date: 2026-04-11 01:48:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260410_06"
down_revision = "20260410_05"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _table_columns("attachment_sessions")
    if not columns:
        return

    if "attachment_count" not in columns:
        with op.batch_alter_table("attachment_sessions") as batch_op:
            batch_op.add_column(sa.Column("attachment_count", sa.Integer(), nullable=True))
    if "total_size_bytes" not in columns:
        with op.batch_alter_table("attachment_sessions") as batch_op:
            batch_op.add_column(sa.Column("total_size_bytes", sa.Integer(), nullable=True))

    op.execute("UPDATE attachment_sessions SET attachment_count = COALESCE(attachment_count, 0)")
    op.execute("UPDATE attachment_sessions SET total_size_bytes = COALESCE(total_size_bytes, 0)")

    with op.batch_alter_table("attachment_sessions") as batch_op:
        batch_op.alter_column("attachment_count", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("total_size_bytes", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    columns = _table_columns("attachment_sessions")
    if not columns:
        return

    with op.batch_alter_table("attachment_sessions") as batch_op:
        if "attachment_count" in columns:
            batch_op.drop_column("attachment_count")
        if "total_size_bytes" in columns:
            batch_op.drop_column("total_size_bytes")
