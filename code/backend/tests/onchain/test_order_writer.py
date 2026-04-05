from datetime import datetime, timezone

from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Order, Payment, SettlementRecord
from app.onchain.contracts_registry import ContractsRegistry
from app.onchain.order_writer import OrderWriter


def _build_order() -> Order:
    order = Order(
        id="order-1",
        onchain_order_id="chain-order-1",
        user_id="user-1",
        machine_id="machine-1",
        chat_session_id="chat-1",
        user_prompt="Create a launch workflow",
        recommended_plan_summary="Recommended plan",
        quoted_amount_cents=1000,
        state=OrderState.PLAN_RECOMMENDED,
        execution_state=ExecutionState.SUCCEEDED,
        preview_state=PreviewState.READY,
        settlement_state=SettlementState.READY,
        settlement_beneficiary_user_id="owner-1",
        settlement_is_self_use=False,
        settlement_is_dividend_eligible=True,
        result_confirmed_at=datetime(2026, 4, 4, tzinfo=timezone.utc),
    )
    return order


def test_mark_order_paid_returns_deterministic_tx_metadata() -> None:
    writer = OrderWriter(ContractsRegistry())
    order = _build_order()
    payment = Payment(
        id="payment-1",
        order_id=order.id,
        provider="hsp",
        provider_reference="flow_123",
        merchant_order_id="merchant_123",
        flow_id="flow_123",
        amount_cents=1000,
        currency="USDC",
        state=PaymentState.SUCCEEDED,
    )

    first = writer.mark_order_paid(order, payment)
    second = writer.mark_order_paid(order, payment)

    assert first.tx_hash == second.tx_hash
    assert first.method_name == "markOrderPaid"
    assert first.contract_name == "OrderBook"
    assert first.payload["order_id"] == "chain-order-1"
    assert first.payload["settlement_beneficiary_user_id"] == "owner-1"
    assert first.payload["settlement_is_self_use"] is False
    assert first.payload["settlement_is_dividend_eligible"] is True


def test_writer_exposes_create_confirm_and_settle_actions() -> None:
    writer = OrderWriter(ContractsRegistry())
    order = _build_order()
    settlement = SettlementRecord(
        id="settlement-1",
        order_id=order.id,
        gross_amount_cents=1000,
        platform_fee_cents=100,
        machine_share_cents=900,
        state=SettlementState.LOCKED,
    )

    create_result = writer.create_order(order)
    preview_result = writer.mark_preview_ready(order)
    confirm_result = writer.confirm_result(order)
    settle_result = writer.settle_order(order, settlement)

    assert create_result.method_name == "createOrder"
    assert preview_result.method_name == "markPreviewReady"
    assert confirm_result.method_name == "confirmResult"
    assert settle_result.method_name == "settleOrder"
    assert settle_result.payload["platform_fee_cents"] == 100
    assert settle_result.payload["machine_share_cents"] == 900


def test_writer_builds_direct_payment_call_spec() -> None:
    writer = OrderWriter(ContractsRegistry())
    order = _build_order()
    payment = Payment(
        id="payment-2",
        order_id=order.id,
        provider="onchain_router",
        provider_reference="payWithUSDCByAuthorization",
        amount_cents=1000,
        currency="USDC",
        state=PaymentState.PENDING,
    )

    intent = writer.build_direct_payment_intent(order, payment)

    assert intent.contract_name == "OrderPaymentRouter"
    assert intent.method_name == "payWithUSDCByAuthorization"
    assert intent.payload["order_id"] == "chain-order-1"
    assert intent.payload["signing_standard"] == "eip3009"
    assert intent.payload["currency"] == "USDC"


def test_writer_builds_pwr_direct_payment_call_spec() -> None:
    writer = OrderWriter(ContractsRegistry())
    order = _build_order()
    payment = Payment(
        id="payment-3",
        order_id=order.id,
        provider="onchain_router",
        provider_reference="payWithPWR",
        amount_cents=1000,
        currency="PWR",
        state=PaymentState.PENDING,
    )

    intent = writer.build_direct_payment_intent(
        order,
        payment,
        pwr_amount="36000000000000000000",
        pricing_version="phase1_v3",
        pwr_anchor_price_cents=25,
    )

    assert intent.contract_name == "OrderPaymentRouter"
    assert intent.method_name == "payWithPWR"
    assert intent.payload["currency"] == "PWR"
    assert intent.payload["pwr_amount"] == "36000000000000000000"
    assert intent.payload["pricing_version"] == "phase1_v3"
