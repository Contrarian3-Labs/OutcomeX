"""Add PWR wei accounting columns for exact machine revenue and refund claims.

Revision ID: 20260412_01
Revises: 20260410_06
Create Date: 2026-04-12 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260412_01"
down_revision = "20260410_06"
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "revenue_entries", "machine_share_pwr_wei"):
        with op.batch_alter_table("revenue_entries") as batch_op:
            batch_op.add_column(sa.Column("machine_share_pwr_wei", sa.String(length=80), nullable=True))

    if not _has_column(inspector, "machine_revenue_claims", "amount_wei"):
        with op.batch_alter_table("machine_revenue_claims") as batch_op:
            batch_op.add_column(sa.Column("amount_wei", sa.String(length=80), nullable=True))

    if not _has_column(inspector, "settlement_claim_records", "amount_wei"):
        with op.batch_alter_table("settlement_claim_records") as batch_op:
            batch_op.add_column(sa.Column("amount_wei", sa.String(length=80), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "settlement_claim_records", "amount_wei"):
        with op.batch_alter_table("settlement_claim_records") as batch_op:
            batch_op.drop_column("amount_wei")

    if _has_column(inspector, "machine_revenue_claims", "amount_wei"):
        with op.batch_alter_table("machine_revenue_claims") as batch_op:
            batch_op.drop_column("amount_wei")

    if _has_column(inspector, "revenue_entries", "machine_share_pwr_wei"):
        with op.batch_alter_table("revenue_entries") as batch_op:
            batch_op.drop_column("machine_share_pwr_wei")
