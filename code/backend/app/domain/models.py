from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
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


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    display_name: Mapped[str] = mapped_column(String(128))
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
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


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    onchain_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="orders")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    settlement: Mapped["SettlementRecord | None"] = relationship(back_populates="order")
    revenue_entries: Mapped[list["RevenueEntry"]] = relationship(back_populates="order")
    execution_runs: Mapped[list["ExecutionRun"]] = relationship(back_populates="order")


class ExecutionRun(Base):
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True, nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
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

    order: Mapped["Order"] = relationship(back_populates="execution_runs")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="hsp", nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merchant_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    flow_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    state: Mapped[PaymentState] = mapped_column(Enum(PaymentState), default=PaymentState.CREATED)
    callback_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    callback_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    callback_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="payments")


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


class ChatPlan(Base):
    __tablename__ = "chat_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    chat_session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_plan_summary: Mapped[str] = mapped_column(Text, nullable=False)
    preview_state: Mapped[PreviewState] = mapped_column(Enum(PreviewState), default=PreviewState.READY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
