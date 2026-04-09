"""Add machine listings projection table.

Revision ID: 20260409_02
Revises: 20260409_01
Create Date: 2026-04-09 00:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260409_02"
down_revision = "20260409_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "machine_listings" in inspector.get_table_names():
        return

    op.create_table(
        "machine_listings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("onchain_listing_id", sa.String(length=64), nullable=False),
        sa.Column("machine_id", sa.String(length=36), nullable=True),
        sa.Column("onchain_machine_id", sa.String(length=64), nullable=True),
        sa.Column("seller_chain_address", sa.String(length=42), nullable=False),
        sa.Column("buyer_chain_address", sa.String(length=42), nullable=True),
        sa.Column("payment_token_address", sa.String(length=42), nullable=False),
        sa.Column("payment_token_symbol", sa.String(length=16), nullable=True),
        sa.Column("payment_token_decimals", sa.Integer(), nullable=True),
        sa.Column("price_units", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_event_id", sa.String(length=128), nullable=True),
        sa.Column("listed_tx_hash", sa.String(length=128), nullable=True),
        sa.Column("cancel_tx_hash", sa.String(length=128), nullable=True),
        sa.Column("filled_tx_hash", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_machine_listings_onchain_listing_id"), "machine_listings", ["onchain_listing_id"], unique=True)
    op.create_index(op.f("ix_machine_listings_machine_id"), "machine_listings", ["machine_id"], unique=False)
    op.create_index(op.f("ix_machine_listings_onchain_machine_id"), "machine_listings", ["onchain_machine_id"], unique=False)
    op.create_index(op.f("ix_machine_listings_seller_chain_address"), "machine_listings", ["seller_chain_address"], unique=False)
    op.create_index(op.f("ix_machine_listings_buyer_chain_address"), "machine_listings", ["buyer_chain_address"], unique=False)
    op.create_index(op.f("ix_machine_listings_state"), "machine_listings", ["state"], unique=False)
    op.create_index(op.f("ix_machine_listings_last_event_id"), "machine_listings", ["last_event_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "machine_listings" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_machine_listings_last_event_id"), table_name="machine_listings")
    op.drop_index(op.f("ix_machine_listings_state"), table_name="machine_listings")
    op.drop_index(op.f("ix_machine_listings_buyer_chain_address"), table_name="machine_listings")
    op.drop_index(op.f("ix_machine_listings_seller_chain_address"), table_name="machine_listings")
    op.drop_index(op.f("ix_machine_listings_onchain_machine_id"), table_name="machine_listings")
    op.drop_index(op.f("ix_machine_listings_machine_id"), table_name="machine_listings")
    op.drop_index(op.f("ix_machine_listings_onchain_listing_id"), table_name="machine_listings")
    op.drop_table("machine_listings")
