"""Rekey attachments to session-scoped access.

Revision ID: 20260410_03
Revises: 20260410_02
Create Date: 2026-04-10 23:58:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260410_03"
down_revision = "20260410_02"
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
    columns = _table_columns("attachments")
    if not columns:
        return

    if "session_kind" not in columns or "session_id" not in columns:
        with op.batch_alter_table("attachments") as batch_op:
            if "session_kind" not in columns:
                batch_op.add_column(sa.Column("session_kind", sa.String(length=32), nullable=True))
            if "session_id" not in columns:
                batch_op.add_column(sa.Column("session_id", sa.String(length=128), nullable=True))

    columns = _table_columns("attachments")
    if "user_id" in columns:
        op.execute("UPDATE attachments SET session_kind = 'legacy_user', session_id = user_id")
    else:
        op.execute(
            "UPDATE attachments SET session_kind = COALESCE(session_kind, 'legacy_user'), "
            "session_id = COALESCE(session_id, id)"
        )

    with op.batch_alter_table("attachments") as batch_op:
        batch_op.alter_column("session_kind", existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column("session_id", existing_type=sa.String(length=128), nullable=False)
        if "user_id" in columns:
            batch_op.drop_column("user_id")

    indexes = _table_indexes("attachments")
    if "ix_attachments_user_id" in indexes:
        op.drop_index("ix_attachments_user_id", table_name="attachments")

    indexes = _table_indexes("attachments")
    if "ix_attachments_session_context" not in indexes:
        op.create_index("ix_attachments_session_context", "attachments", ["session_kind", "session_id"], unique=False)


def downgrade() -> None:
    columns = _table_columns("attachments")
    if not columns:
        return

    if "user_id" not in columns:
        with op.batch_alter_table("attachments") as batch_op:
            batch_op.add_column(sa.Column("user_id", sa.String(length=64), nullable=True))

    op.execute("UPDATE attachments SET user_id = COALESCE(user_id, session_id)")

    with op.batch_alter_table("attachments") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(length=64), nullable=False)
        if "session_kind" in columns:
            batch_op.drop_column("session_kind")
        if "session_id" in columns:
            batch_op.drop_column("session_id")

    indexes = _table_indexes("attachments")
    if "ix_attachments_session_context" in indexes:
        op.drop_index("ix_attachments_session_context", table_name="attachments")

    indexes = _table_indexes("attachments")
    if "ix_attachments_user_id" not in indexes:
        op.create_index("ix_attachments_user_id", "attachments", ["user_id"], unique=False)
