"""Add server-issued attachment sessions and rekey attachments.

Revision ID: 20260410_04
Revises: 20260410_03
Create Date: 2026-04-11 00:32:00
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260410_04"
down_revision = "20260410_03"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


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
    bind = op.get_bind()
    table_names = _table_names()
    if "attachments" not in table_names:
        return

    if "attachment_sessions" not in table_names:
        op.create_table("attachment_sessions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    attachment_columns = _table_columns("attachments")
    if "attachment_session_id" not in attachment_columns:
        with op.batch_alter_table("attachments") as batch_op:
            batch_op.add_column(sa.Column("attachment_session_id", sa.String(length=36), nullable=True))

    attachment_columns = _table_columns("attachments")
    if {"session_kind", "session_id"}.issubset(attachment_columns):
        rows = bind.execute(sa.text("SELECT DISTINCT session_kind, session_id FROM attachments")).fetchall()
        now_utc = datetime.now(timezone.utc)
        for session_kind, session_id in rows:
            attachment_session_id = str(uuid4())
            legacy_seed = f"legacy:{session_kind}:{session_id}"
            token_hash = hashlib.sha256(legacy_seed.encode("utf-8")).hexdigest()
            bind.execute(
                sa.text(
                    """
                    INSERT INTO attachment_sessions (id, token_hash, created_at)
                    VALUES (:id, :token_hash, :created_at)
                    """
                ),
                {
                    "id": attachment_session_id,
                    "token_hash": token_hash,
                    "created_at": now_utc,
                },
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE attachments
                    SET attachment_session_id = :attachment_session_id
                    WHERE session_kind = :session_kind
                      AND session_id = :session_id
                      AND attachment_session_id IS NULL
                    """
                ),
                {
                    "attachment_session_id": attachment_session_id,
                    "session_kind": session_kind,
                    "session_id": session_id,
                },
            )

    indexes = _table_indexes("attachments")
    if "ix_attachments_session_context" in indexes:
        op.drop_index("ix_attachments_session_context", table_name="attachments")

    with op.batch_alter_table("attachments") as batch_op:
        batch_op.alter_column("attachment_session_id", existing_type=sa.String(length=36), nullable=False)
        if "session_kind" in attachment_columns:
            batch_op.drop_column("session_kind")
        if "session_id" in attachment_columns:
            batch_op.drop_column("session_id")
        batch_op.create_foreign_key(
            "fk_attachments_attachment_session_id",
            "attachment_sessions",
            ["attachment_session_id"],
            ["id"],
        )

    indexes = _table_indexes("attachments")
    if "ix_attachments_attachment_session_id" not in indexes:
        op.create_index("ix_attachments_attachment_session_id", "attachments", ["attachment_session_id"], unique=False)


def downgrade() -> None:
    table_names = _table_names()
    if "attachments" not in table_names:
        return

    attachment_columns = _table_columns("attachments")
    if "session_kind" not in attachment_columns or "session_id" not in attachment_columns:
        with op.batch_alter_table("attachments") as batch_op:
            if "session_kind" not in attachment_columns:
                batch_op.add_column(sa.Column("session_kind", sa.String(length=32), nullable=True))
            if "session_id" not in attachment_columns:
                batch_op.add_column(sa.Column("session_id", sa.String(length=128), nullable=True))

    op.execute("UPDATE attachments SET session_kind = 'legacy_session', session_id = attachment_session_id")

    indexes = _table_indexes("attachments")
    if "ix_attachments_attachment_session_id" in indexes:
        op.drop_index("ix_attachments_attachment_session_id", table_name="attachments")

    with op.batch_alter_table("attachments") as batch_op:
        batch_op.alter_column("session_kind", existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column("session_id", existing_type=sa.String(length=128), nullable=False)
        batch_op.drop_constraint("fk_attachments_attachment_session_id", type_="foreignkey")
        if "attachment_session_id" in _table_columns("attachments"):
            batch_op.drop_column("attachment_session_id")

    indexes = _table_indexes("attachments")
    if "ix_attachments_session_context" not in indexes:
        op.create_index("ix_attachments_session_context", "attachments", ["session_kind", "session_id"], unique=False)

    table_names = _table_names()
    if "attachment_sessions" in table_names:
        op.drop_table("attachment_sessions")
