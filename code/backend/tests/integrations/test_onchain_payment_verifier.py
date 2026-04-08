from app.domain.enums import ExecutionState, OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import Order, Payment
from app.integrations.onchain_payment_verifier import OnchainPaymentVerifier
from app.onchain.event_decoder import ORDER_CREATED_TOPIC0, PAYMENT_FINALIZED_TOPIC0
from app.onchain.receipts import ChainReceipt

BUYER_ADDRESS = "0x00000000000000000000000000000000000000b1"
OTHER_BUYER_ADDRESS = "0x00000000000000000000000000000000000000b2"
USDC_ADDRESS = "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"
USDT_ADDRESS = "0x372325443233febaC1F6998aC750276468c83CC6"


def _build_order() -> Order:
    return Order(
        id="order-1",
        onchain_order_id=None,
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


def test_verifier_rejects_missing_receipt_when_live_receipt_unavailable() -> None:
    verifier = OnchainPaymentVerifier()
    order = _build_order()
    payment = _build_payment(order.id)

    verification = verifier.verify_payment(
        tx_hash="0xabc123",
        wallet_address=BUYER_ADDRESS,
        order=order,
        payment=payment,
    )

    assert verification.matched is False
    assert verification.state == PaymentState.FAILED
    assert verification.reason == "receipt_not_found"
    assert verification.event_id == "onchain:0xabc123"
    assert verification.evidence_order_id is None
    assert verification.evidence_amount_cents is None
    assert verification.evidence_currency is None
    assert verification.evidence_create_order_tx_hash is None
    assert verification.evidence_create_order_event_id is None
    assert verification.evidence_create_order_block_number is None


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
    assert verification.evidence_create_order_tx_hash is None
    assert verification.evidence_create_order_event_id is None
    assert verification.evidence_create_order_block_number is None


class StubReceiptReader:
    def __init__(self, receipt: ChainReceipt | None) -> None:
        self._receipt = receipt

    def get_receipt(self, tx_hash: str) -> ChainReceipt | None:
        return self._receipt


def _order_created_log(order_id: int) -> dict[str, object]:
    return {
        "address": "0x0000000000000000000000000000000000000133",
        "topics": [ORDER_CREATED_TOPIC0, hex(order_id)],
        "transactionHash": "0xabc123",
        "logIndex": "0x2",
    }


def _topic_address(address: str) -> str:
    normalized = address.lower().replace("0x", "")
    return "0x" + ("0" * 24) + normalized


def _topic_uint256(value: int) -> str:
    return hex(value)


def _encode_word(value: int | str) -> str:
    if isinstance(value, int):
        encoded = hex(value)[2:]
    else:
        encoded = value.lower().replace("0x", "")
    return encoded.rjust(64, "0")


def _order_created_log_full(*, order_id: int, machine_id: int, buyer: str, gross_amount: int) -> dict[str, object]:
    return {
        "address": "0x0000000000000000000000000000000000000133",
        "topics": [
            ORDER_CREATED_TOPIC0,
            _topic_uint256(order_id),
            _topic_uint256(machine_id),
            _topic_address(buyer),
        ],
        "data": "0x" + _encode_word(gross_amount) + _encode_word("0x0000000000000000000000000000000000000abc"),
        "transactionHash": "0xabc123",
        "logIndex": "0x2",
    }


def _order_payment_received_log(*, order_id: int, payer: str, token: str, amount: int) -> dict[str, object]:
    return {
        "address": "0x0000000000000000000000000000000000000134",
        "topics": [
            PAYMENT_FINALIZED_TOPIC0,
            _topic_uint256(order_id),
            _topic_uint256(7),
            _topic_address(BUYER_ADDRESS),
        ],
        "data": (
            "0x"
            + _encode_word(payer)
            + _encode_word(token)
            + _encode_word(amount)
            + _encode_word("0x" + "11" * 32)
            + _encode_word("0x0000000000000000000000000000000000000abc")
            + _encode_word(1)
            + _encode_word(1)
        ),
        "transactionHash": "0xabc123",
        "logIndex": "0x3",
    }


def test_verifier_prefers_live_receipt_when_available() -> None:
    order = _build_order()
    payment = _build_payment(order.id)
    verifier = OnchainPaymentVerifier(
        receipt_reader=StubReceiptReader(
            ChainReceipt(
                tx_hash="0xabc123",
                status=1,
                from_address=BUYER_ADDRESS,
                to_address="0x0000000000000000000000000000000000000134",
                block_number=888,
                event_id="receipt:0xabc123:888",
                metadata={
                    "logs": [
                        _order_created_log_full(order_id=42, machine_id=7, buyer=BUYER_ADDRESS, gross_amount=1000),
                        _order_payment_received_log(
                            order_id=42,
                            payer=BUYER_ADDRESS,
                            token=USDC_ADDRESS,
                            amount=1000,
                        ),
                    ]
                },
            )
        )
    )

    verification = verifier.verify_payment(
        tx_hash="0xabc123",
        wallet_address=BUYER_ADDRESS,
        order=order,
        payment=payment,
    )

    assert verification.matched is True
    assert verification.event_id == "OrderCreated:42:0xabc123"
    assert verification.evidence_order_id == "42"
    assert verification.evidence_create_order_tx_hash == "0xabc123"
    assert verification.evidence_create_order_event_id == "OrderCreated:42:0xabc123"
    assert verification.evidence_create_order_block_number == 888


def test_verifier_rejects_receipt_without_order_created_event() -> None:
    order = _build_order()
    payment = _build_payment(order.id)
    verifier = OnchainPaymentVerifier(
        receipt_reader=StubReceiptReader(
            ChainReceipt(
                tx_hash="0xabc123",
                status=1,
                from_address=BUYER_ADDRESS,
                to_address="0x0000000000000000000000000000000000000134",
                block_number=888,
                event_id="receipt:0xabc123:888",
                metadata={"logs": []},
            )
        )
    )

    verification = verifier.verify_payment(
        tx_hash="0xabc123",
        wallet_address=BUYER_ADDRESS,
        order=order,
        payment=payment,
    )

    assert verification.matched is False
    assert verification.reason == "payment_received_event_not_found"
    assert verification.state == PaymentState.FAILED
    assert verification.evidence_order_id is None


def test_verifier_rejects_wallet_mismatch_from_live_receipt() -> None:
    order = _build_order()
    payment = _build_payment(order.id)
    verifier = OnchainPaymentVerifier(
        receipt_reader=StubReceiptReader(
            ChainReceipt(
                tx_hash="0xabc123",
                status=1,
                from_address=OTHER_BUYER_ADDRESS,
                to_address="0x0000000000000000000000000000000000000134",
                block_number=888,
                event_id="receipt:0xabc123:888",
                metadata={"logs": [_order_created_log(42)]},
            )
        )
    )

    verification = verifier.verify_payment(
        tx_hash="0xabc123",
        wallet_address=BUYER_ADDRESS,
        order=order,
        payment=payment,
    )

    assert verification.matched is False
    assert verification.reason == "wallet_mismatch"


def test_verifier_rejects_machine_id_mismatch_from_order_created_event() -> None:
    order = _build_order()
    order.onchain_machine_id = "7"
    payment = _build_payment(order.id)
    verifier = OnchainPaymentVerifier(
        receipt_reader=StubReceiptReader(
            ChainReceipt(
                tx_hash="0xabc123",
                status=1,
                from_address=BUYER_ADDRESS,
                to_address="0x0000000000000000000000000000000000000134",
                block_number=888,
                event_id="receipt:0xabc123:888",
                metadata={
                    "logs": [
                        _order_created_log_full(order_id=42, machine_id=8, buyer=BUYER_ADDRESS, gross_amount=1000),
                        _order_payment_received_log(
                            order_id=42,
                            payer=BUYER_ADDRESS,
                            token=USDC_ADDRESS,
                            amount=1000,
                        ),
                    ]
                },
            )
        )
    )

    verification = verifier.verify_payment(
        tx_hash="0xabc123",
        wallet_address=BUYER_ADDRESS,
        order=order,
        payment=payment,
    )

    assert verification.matched is False
    assert verification.reason == "machine_id_mismatch"


def test_verifier_rejects_payment_token_mismatch_from_router_event() -> None:
    order = _build_order()
    order.onchain_machine_id = "7"
    payment = _build_payment(order.id)
    verifier = OnchainPaymentVerifier(
        receipt_reader=StubReceiptReader(
            ChainReceipt(
                tx_hash="0xabc123",
                status=1,
                from_address=BUYER_ADDRESS,
                to_address="0x0000000000000000000000000000000000000134",
                block_number=888,
                event_id="receipt:0xabc123:888",
                metadata={
                    "logs": [
                        _order_created_log_full(order_id=42, machine_id=7, buyer=BUYER_ADDRESS, gross_amount=1000),
                        _order_payment_received_log(
                            order_id=42,
                            payer=BUYER_ADDRESS,
                            token=USDT_ADDRESS,
                            amount=1000,
                        ),
                    ]
                },
            )
        )
    )

    verification = verifier.verify_payment(
        tx_hash="0xabc123",
        wallet_address=BUYER_ADDRESS,
        order=order,
        payment=payment,
    )

    assert verification.matched is False
    assert verification.reason == "payment_token_mismatch"
