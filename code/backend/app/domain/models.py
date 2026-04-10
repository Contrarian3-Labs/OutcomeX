from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import (
    ExecutionRunStatus,
    ExecutionState,
    OrderState,
    PaymentState,
    PreviewState,
    SettlementState,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


UNPAID_ORDER_TTL = timedelta(minutes=10)


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    onchain_machine_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(128))
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_chain_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    ownership_source: Mapped[str] = mapped_column(String(32), default="bootstrap", nullable=False)
    owner_projection_last_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_transfer_new_owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_transfer_keep_previous_setup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pending_transfer_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_active_tasks: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_unsettled_revenue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="machine")
    revenue_entries: Mapped[list["RevenueEntry"]] = relationship(back_populates="machine")
    listings: Mapped[list["MachineListing"]] = relationship(back_populates="machine")
    claims: Mapped[list["MachineRevenueClaim"]] = relationship(back_populates="machine")


class MachineListing(Base):
    __tablename__ = "machine_listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    onchain_listing_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    machine_id: Mapped[str | None] = mapped_column(ForeignKey("machines.id"), index=True, nullable=True)
    onchain_machine_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    seller_chain_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    buyer_chain_address: Mapped[str | None] = mapped_column(String(42), nullable=True, index=True)
    payment_token_address: Mapped[str] = mapped_column(String(42), nullable=False)
    payment_token_symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payment_token_decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    last_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    listed_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cancel_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filled_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    listed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    machine: Mapped["Machine | None"] = relationship(back_populates="listings")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    onchain_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    onchain_machine_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    create_order_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    create_order_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    create_order_block_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), index=True)
    chat_session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_prompt: Mapped[str] = mapped_column(Text)
    recommended_plan_summary: Mapped[str] = mapped_column(Text)
    quoted_amount_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[OrderState] = mapped_column(Enum(OrderState), default=OrderState.PLAN_RECOMMENDED)
    execution_state: Mapped[ExecutionState] = mapped_column(Enum(ExecutionState), default=ExecutionState.QUEUED)
    preview_state: Mapped[PreviewState] = mapped_column(Enum(PreviewState), default=PreviewState.READY)
    settlement_state: Mapped[SettlementState] = mapped_column(
        Enum(SettlementState),
        default=SettlementState.NOT_READY,
    )
    settlement_beneficiary_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settlement_is_self_use: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    settlement_is_dividend_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    execution_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="orders")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    settlement: Mapped["SettlementRecord | None"] = relationship(back_populates="order")
    revenue_entries: Mapped[list["RevenueEntry"]] = relationship(back_populates="order")
    execution_runs: Mapped[list["ExecutionRun"]] = relationship(back_populates="order")

    @property
    def latest_success_payment_currency(self) -> str | None:
        successful = [payment for payment in self.payments if payment.state == PaymentState.SUCCEEDED]
        if not successful:
            return None
        latest = max(successful, key=lambda payment: payment.created_at)
        return latest.currency

    @property
    def payment_state(self) -> PaymentState:
        if not self.payments:
            return PaymentState.CREATED
        if any(payment.state == PaymentState.SUCCEEDED for payment in self.payments):
            return PaymentState.SUCCEEDED
        latest = max(self.payments, key=lambda payment: payment.created_at)
        return latest.state

    @property
    def unpaid_expiry_at(self) -> datetime | None:
        metadata = dict(self.execution_metadata or {})
        if metadata.get("authoritative_paid_projection") is True:
            return None
        if self.payment_state == PaymentState.SUCCEEDED or self.is_cancelled:
            return None
        return self.created_at + UNPAID_ORDER_TTL

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None or self.state == OrderState.CANCELLED

    @property
    def is_expired(self) -> bool:
        expiry = self.unpaid_expiry_at
        if expiry is None:
            return False
        if expiry.tzinfo is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            now = datetime.now(expiry.tzinfo)
        return expiry <= now


class ExecutionRun(Base):
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True, nullable=True)
    machine_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    viewer_user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    run_kind: Mapped[str] = mapped_column(String(32), default="order", nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    status: Mapped[ExecutionRunStatus] = mapped_column(
        Enum(ExecutionRunStatus),
        default=ExecutionRunStatus.QUEUED,
        nullable=False,
    )
    submission_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_manifest: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    artifact_manifest: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    skills_manifest: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    model_usage_manifest: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    summary_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    order: Mapped["Order | None"] = relationship(back_populates="execution_runs")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="hsp", nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merchant_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    flow_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    state: Mapped[PaymentState] = mapped_column(Enum(PaymentState), default=PaymentState.CREATED)
    callback_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    callback_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    callback_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="payments")


class PrimaryIssuanceSku(Base):
    __tablename__ = "primary_issuance_skus"

    sku_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    profile_label: Mapped[str] = mapped_column(String(128), nullable=False)
    gpu_spec: Mapped[str] = mapped_column(String(256), nullable=False)
    model_family: Mapped[str] = mapped_column(String(128), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USDC")
    stock_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    purchases: Mapped[list["PrimaryIssuancePurchase"]] = relationship(back_populates="sku")


class PrimaryIssuancePurchase(Base):
    __tablename__ = "primary_issuance_purchases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    sku_id: Mapped[str] = mapped_column(ForeignKey("primary_issuance_skus.sku_id"), nullable=False, index=True)
    buyer_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="hsp", nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    merchant_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    flow_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USDC")
    state: Mapped[PaymentState] = mapped_column(Enum(PaymentState), default=PaymentState.CREATED, nullable=False)
    stock_reserved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stock_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    callback_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    callback_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    minted_machine_id: Mapped[str | None] = mapped_column(ForeignKey("machines.id"), nullable=True, unique=True)
    minted_onchain_machine_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    sku: Mapped["PrimaryIssuanceSku"] = relationship(back_populates="purchases")
    minted_machine: Mapped["Machine | None"] = relationship()


class SettlementRecord(Base):
    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("order_id", name="uq_settlements_order_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    gross_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_share_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[SettlementState] = mapped_column(Enum(SettlementState), default=SettlementState.NOT_READY)
    distributed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="settlement")
    revenue_entries: Mapped[list["RevenueEntry"]] = relationship(back_populates="settlement")


class RevenueEntry(Base):
    __tablename__ = "revenue_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    settlement_id: Mapped[str] = mapped_column(ForeignKey("settlements.id"), index=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), index=True)
    beneficiary_user_id: Mapped[str] = mapped_column(String(64), index=True)
    gross_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_share_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    is_self_use: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_dividend_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="revenue_entries")
    settlement: Mapped["SettlementRecord"] = relationship(back_populates="revenue_entries")
    machine: Mapped["Machine"] = relationship(back_populates="revenue_entries")


class MachineRevenueClaim(Base):
    __tablename__ = "machine_revenue_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), index=True, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="claims")


class SettlementClaimRecord(Base):
    __tablename__ = "settlement_claim_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    claim_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    claimant_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    account_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    token_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    machine_id: Mapped[str | None] = mapped_column(ForeignKey("machines.id"), index=True, nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ChatPlan(Base):
    __tablename__ = "chat_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    chat_session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_plan_summary: Mapped[str] = mapped_column(Text, nullable=False)
    preview_state: Mapped[PreviewState] = mapped_column(Enum(PreviewState), default=PreviewState.READY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)



class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_attachment_session_id", "attachment_session_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    attachment_session_id: Mapped[str] = mapped_column(ForeignKey("attachment_sessions.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    attachment_session: Mapped["AttachmentSession"] = relationship(back_populates="attachments")


class AttachmentSession(Base):
    __tablename__ = "attachment_sessions"
    __table_args__ = (Index("ix_attachment_sessions_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    attachments: Mapped[list["Attachment"]] = relationship(back_populates="attachment_session")


class OnchainIndexerCursor(Base):
    __tablename__ = "onchain_indexer_cursor"

    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_indexed_block: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class OnchainProcessedEvent(Base):
    __tablename__ = "onchain_processed_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
