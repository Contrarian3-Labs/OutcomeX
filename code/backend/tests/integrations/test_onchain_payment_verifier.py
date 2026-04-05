from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Order, Payment
from app.integrations.onchain_payment_verifier import OnchainPaymentVerifier


def _build_order() -> Order:
    return Order(
        id="order-1",
        onchain_order_id="oc_chain_1",
        user_id="user-1",
        machine_id="machine-1",
        chat_session_id="chat-1",
        user_prompt="Generate a launch page",
        recommended_plan_summary="plan",
        quoted_amount_cents=1000,
        state=OrderState.PLAN_RECOMMENDED,
        execution_state=ExecutionState.QUEUED,
        preview_state=PreviewState.READY,
        settlement_state=SettlementState.NOT_READY,
    )


def _build_payment(order_id: str) -> Payment:
    return Payment(
        id="payment-1",
        order_id=order_id,
        provider="onchain_router",
        amount_cents=1000,
        currency="USDC",
        state=PaymentState.PENDING,
    )


def test_verifier_stub_returns_order_aligned_evidence() -> None:
    verifier = OnchainPaymentVerifier()
    order = _build_order()
    payment = _build_payment(order.id)

    verification = verifier.verify_payment(
        tx_hash="0xabc123",
        wallet_address="0xbuyer",
        order=order,
        payment=payment,
    )

    assert verification.matched is True
    assert verification.state == PaymentState.SUCCEEDED
    assert verification.event_id == "onchain:0xabc123"
    assert verification.evidence_order_id == "oc_chain_1"
    assert verification.evidence_amount_cents == 1000
    assert verification.evidence_currency == "USDC"


def test_verifier_stub_rejects_invalid_tx_hash() -> None:
    verifier = OnchainPaymentVerifier()
    order = _build_order()
    payment = _build_payment(order.id)

    verification = verifier.verify_payment(
        tx_hash="not_a_tx",
        wallet_address=None,
        order=order,
        payment=payment,
    )

    assert verification.matched is False
    assert verification.reason == "invalid_tx_hash"
    assert verification.state == PaymentState.FAILED
