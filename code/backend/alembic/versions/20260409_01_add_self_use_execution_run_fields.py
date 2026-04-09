"""Add self-use execution run fields and relax order-backed requirement.

Revision ID: 20260409_01
Revises: 20260408_01
Create Date: 2026-04-09 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260409_01"
down_revision = "20260408_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "execution_runs" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("execution_runs")}

    with op.batch_alter_table("execution_runs") as batch_op:
        if "machine_id" not in columns:
            batch_op.add_column(sa.Column("machine_id", sa.String(length=36), nullable=True))
        if "viewer_user_id" not in columns:
            batch_op.add_column(sa.Column("viewer_user_id", sa.String(length=64), nullable=True))
        if "run_kind" not in columns:
            batch_op.add_column(sa.Column("run_kind", sa.String(length=32), nullable=False, server_default="order"))

        if "order_id" in columns and not columns["order_id"].get("nullable", True):
            batch_op.alter_column("order_id", existing_type=sa.String(length=64), nullable=True)
        if "external_order_id" in columns:
            batch_op.alter_column("external_order_id", existing_type=sa.String(length=64), type_=sa.String(length=128))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "execution_runs" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("execution_runs")}

    with op.batch_alter_table("execution_runs") as batch_op:
        if "run_kind" in columns:
            batch_op.drop_column("run_kind")
        if "viewer_user_id" in columns:
            batch_op.drop_column("viewer_user_id")
        if "machine_id" in columns:
            batch_op.drop_column("machine_id")
        if "external_order_id" in columns:
            batch_op.alter_column("external_order_id", existing_type=sa.String(length=128), type_=sa.String(length=64))
        if "order_id" in columns:
            batch_op.alter_column("order_id", existing_type=sa.String(length=64), nullable=False)
