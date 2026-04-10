"""Add attachment session expiry column and index.

Revision ID: 20260410_05
Revises: 20260410_04
Create Date: 2026-04-11 01:26:00
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = "20260410_05"
down_revision = "20260410_04"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _table_indexes(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    columns = _table_columns("attachment_sessions")
    if not columns:
        return

    if "expires_at" not in columns:
        with op.batch_alter_table("attachment_sessions") as batch_op:
            batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, created_at FROM attachment_sessions WHERE expires_at IS NULL")).fetchall()
    for session_id, created_at in rows:
        base_time = created_at
        if isinstance(base_time, str):
            base_time = datetime.fromisoformat(base_time)
        if base_time is None:
            base_time = datetime.now(timezone.utc)
        expires_at = base_time + timedelta(hours=24)
        bind.execute(
            sa.text("UPDATE attachment_sessions SET expires_at = :expires_at WHERE id = :session_id"),
            {"expires_at": expires_at, "session_id": session_id},
        )

    with op.batch_alter_table("attachment_sessions") as batch_op:
        batch_op.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)

    if "ix_attachment_sessions_expires_at" not in _table_indexes("attachment_sessions"):
        op.create_index("ix_attachment_sessions_expires_at", "attachment_sessions", ["expires_at"], unique=False)


def downgrade() -> None:
    columns = _table_columns("attachment_sessions")
    if not columns or "expires_at" not in columns:
        return

    if "ix_attachment_sessions_expires_at" in _table_indexes("attachment_sessions"):
        op.drop_index("ix_attachment_sessions_expires_at", table_name="attachment_sessions")

    with op.batch_alter_table("attachment_sessions") as batch_op:
        batch_op.drop_column("expires_at")
