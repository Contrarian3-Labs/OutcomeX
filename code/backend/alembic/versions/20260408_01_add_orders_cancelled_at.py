"""Add orders.cancelled_at for projected unpaid expiry/cancel truth.

Revision ID: 20260408_01
Revises:
Create Date: 2026-04-08 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260408_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "orders" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "cancelled_at" in columns:
        return

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "orders" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "cancelled_at" not in columns:
        return

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("cancelled_at")
