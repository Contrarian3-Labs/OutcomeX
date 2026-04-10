"""Add primary issuance SKU and purchase tables.

Revision ID: 20260410_01
Revises: 20260409_02
Create Date: 2026-04-10 22:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260410_01"
down_revision = "20260409_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "primary_issuance_skus" not in table_names:
        op.create_table("primary_issuance_skus",
            sa.Column("sku_id", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=256), nullable=False),
            sa.Column("profile_label", sa.String(length=128), nullable=False),
            sa.Column("gpu_spec", sa.String(length=256), nullable=False),
            sa.Column("model_family", sa.String(length=128), nullable=False),
            sa.Column("price_cents", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False),
            sa.Column("stock_available", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("sku_id"),
        )

    if "primary_issuance_purchases" not in table_names:
        op.create_table("primary_issuance_purchases",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("sku_id", sa.String(length=64), nullable=False),
            sa.Column("buyer_user_id", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("provider_reference", sa.String(length=128), nullable=True),
            sa.Column("merchant_order_id", sa.String(length=128), nullable=True),
            sa.Column("flow_id", sa.String(length=128), nullable=True),
            sa.Column("checkout_url", sa.Text(), nullable=True),
            sa.Column("provider_payload", sa.JSON(), nullable=True),
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False),
            sa.Column("state", sa.Enum("CREATED", "PENDING", "SUCCEEDED", "FAILED", "REFUNDED", name="paymentstate"), nullable=False),
            sa.Column("stock_reserved", sa.Boolean(), nullable=False),
            sa.Column("stock_released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("callback_event_id", sa.String(length=128), nullable=True),
            sa.Column("callback_state", sa.String(length=64), nullable=True),
            sa.Column("callback_received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("callback_tx_hash", sa.String(length=128), nullable=True),
            sa.Column("minted_machine_id", sa.String(length=36), nullable=True),
            sa.Column("minted_onchain_machine_id", sa.String(length=64), nullable=True),
            sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["minted_machine_id"], ["machines.id"]),
            sa.ForeignKeyConstraint(["sku_id"], ["primary_issuance_skus.sku_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_reference"),
            sa.UniqueConstraint("merchant_order_id"),
            sa.UniqueConstraint("flow_id"),
            sa.UniqueConstraint("minted_machine_id"),
        )
        op.create_index(op.f("ix_primary_issuance_purchases_sku_id"), "primary_issuance_purchases", ["sku_id"], unique=False)
        op.create_index(
            op.f("ix_primary_issuance_purchases_buyer_user_id"),
            "primary_issuance_purchases",
            ["buyer_user_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_primary_issuance_purchases_provider_reference"),
            "primary_issuance_purchases",
            ["provider_reference"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "primary_issuance_purchases" in table_names:
        op.drop_index(op.f("ix_primary_issuance_purchases_provider_reference"), table_name="primary_issuance_purchases")
        op.drop_index(op.f("ix_primary_issuance_purchases_buyer_user_id"), table_name="primary_issuance_purchases")
        op.drop_index(op.f("ix_primary_issuance_purchases_sku_id"), table_name="primary_issuance_purchases")
        op.drop_table("primary_issuance_purchases")

    if "primary_issuance_skus" in table_names:
        op.drop_table("primary_issuance_skus")
