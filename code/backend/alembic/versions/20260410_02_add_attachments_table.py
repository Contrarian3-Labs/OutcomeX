"""Add attachments table for persisted user uploads.

Revision ID: 20260410_02
Revises: 20260410_01
Create Date: 2026-04-10 23:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260410_02"
down_revision = "20260410_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "attachments" in inspector.get_table_names():
        return

    op.create_table("attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachments_user_id", "attachments", ["user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "attachments" not in inspector.get_table_names():
        return

    op.drop_index("ix_attachments_user_id", table_name="attachments")
    op.drop_table("attachments")
