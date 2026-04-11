from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from datetime import datetime, timezone

from app.domain.enums import OrderState, PaymentState, PreviewState, SettlementState
from app.domain.models import (
    Base,
    Machine,
    MachineListing,
    MachineRevenueClaim,
    Order,
    Payment,
    RevenueEntry,
    SettlementClaimRecord,
    SettlementRecord,
)
from app.indexer.events import (
    MachineAssetEvent,
    MarketplaceListingEvent,
    NormalizedEvent,
    OrderLifecycleEvent,
    RevenueClaimedEvent,
    SettlementSplitEvent,
)
from app.indexer.sql_projection import SqlProjectionStore

PWR_ANCHOR_PRICE_CENTS = 25


def _pwr_wei_for_cents(amount_cents: int) -> int:
    return (amount_cents * 10**18) // PWR_ANCHOR_PRICE_CENTS


def _event(
    *,
    payload,
    event_name: str,
    transaction_hash: str = "0xabc",
    block_number: int = 10,
    contract_name: str = "OrderBook",
    contract_address: str = "0x3000000000000000000000000000000000000003",
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=f"133:{block_number}:{transaction_hash}:1",
        chain_id=133,
        contract_name=contract_name,
        contract_address=contract_address,
        event_name=event_name,
        block_number=block_number,
        block_hash="0xblock",
        transaction_hash=transaction_hash,
        log_index=1,
        payload=payload,
    )


def test_sql_projection_updates_machine_owner_from_chain_projection() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    with session_factory() as db:
        db.add(Machine(id="88", display_name="node", owner_user_id="owner-1"))
        db.commit()

    store = SqlProjectionStore(
        session_factory=session_factory,
        owner_resolver=lambda chain_owner: {"0x2222222222222222222222222222222222222222": "owner-2"}.get(chain_owner),
    )
    store.apply(
        _event(
            event_name="Transfer",
            payload=MachineAssetEvent(
                machine_id="88",
                owner="0x2222222222222222222222222222222222222222",
                metadata_uri=None,
                pwr_quota=None,
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "88")
        assert machine.owner_user_id == "owner-2"
        assert machine.owner_chain_address == "0x2222222222222222222222222222222222222222"
        assert machine.ownership_source == "chain"


def test_sql_projection_tracks_marketplace_listing_lifecycle() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        db.add(Machine(id="m-1", onchain_machine_id="7", display_name="node", owner_user_id="owner-1"))
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="ListingCreated",
            contract_name="MachineMarketplace",
            contract_address="0x3000000000000000000000000000000000000099",
            transaction_hash="0xlisting-created",
            payload=MarketplaceListingEvent(
                listing_id="11",
                machine_id="7",
                seller="0xseller0000000000000000000000000000000000",
                buyer=None,
                payment_token="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                price_wei=1_250_000,
                expiry_timestamp=int(datetime.now(timezone.utc).timestamp()) + 3600,
                status="ACTIVE",
            ),
        )
    )

    with session_factory() as db:
        listing = db.query(MachineListing).filter(MachineListing.onchain_listing_id == "11").one()
        assert listing.machine_id == "m-1"
        assert listing.onchain_machine_id == "7"
        assert listing.seller_chain_address == "0xseller0000000000000000000000000000000000"
        assert listing.payment_token_address == "0x79aec4eea31d50792f61d1ca0733c18c89524c9e"
        assert listing.price_units == 1_250_000
        assert listing.state == "active"
        assert listing.buyer_chain_address is None

    store.apply(
        _event(
            event_name="ListingPurchased",
            contract_name="MachineMarketplace",
            contract_address="0x3000000000000000000000000000000000000099",
            transaction_hash="0xlisting-purchased",
            block_number=11,
            payload=MarketplaceListingEvent(
                listing_id="11",
                machine_id="7",
                seller="0xseller0000000000000000000000000000000000",
                buyer="0xbuyer000000000000000000000000000000000000",
                payment_token="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                price_wei=1_250_000,
                expiry_timestamp=int(datetime.now(timezone.utc).timestamp()) + 3600,
                status="FILLED",
            ),
        )
    )

    with session_factory() as db:
        listing = db.query(MachineListing).filter(MachineListing.onchain_listing_id == "11").one()
        assert listing.state == "filled"
        assert listing.buyer_chain_address == "0xbuyer000000000000000000000000000000000000"
        assert listing.filled_at is not None


def test_sql_projection_releases_active_task_when_order_confirmed_onchain() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1", has_active_tasks=True)
        order = Order(
            id="o-1",
            onchain_order_id="42",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
            settlement_state=SettlementState.LOCKED,
            settlement_beneficiary_user_id="owner-1",
            settlement_is_self_use=False,
            settlement_is_dividend_eligible=True,
        )
        payment = Payment(
            order_id="o-1",
            provider="onchain_router",
            amount_cents=100,
            currency="USDC",
            state=PaymentState.SUCCEEDED,
        )
        db.add(machine)
        db.add(order)
        db.add(payment)
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="OrderSettled",
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="m-1",
                buyer="0xbuyer",
                status="CONFIRMED",
                amount_wei=100,
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "m-1")
        order = db.get(Order, "o-1")
        assert machine.has_active_tasks is False
        assert order.settlement_state == SettlementState.DISTRIBUTED
        assert order.state == OrderState.RESULT_CONFIRMED


def test_sql_projection_marks_unsettled_revenue_from_settlement_split() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1", has_unsettled_revenue=False)
        order = Order(
            id="o-1",
            onchain_order_id="42",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
        )
        db.add(machine)
        db.add(order)
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="RevenueAccrued",
            payload=SettlementSplitEvent(
                order_id="42",
                machine_id="m-1",
                recipient="0xowner",
                role="MACHINE_OWNER_DIVIDEND",
                amount_wei=1,
                bps=None,
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "m-1")
        assert machine.has_unsettled_revenue is True


def test_sql_projection_advances_direct_payment_from_created_and_paid_events() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1")
        order = Order(
            id="o-1",
            create_order_tx_hash="0xpaytx",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
        )
        payment = Payment(
            id="p-1",
            order_id="o-1",
            provider="onchain_router",
            amount_cents=100,
            currency="USDC",
            state=PaymentState.PENDING,
        )
        db.add(machine)
        db.add(order)
        db.add(payment)
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="OrderCreated",
            transaction_hash="0xpaytx",
            block_number=22,
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="7",
                buyer="0xbuyer",
                status="CREATED",
                amount_wei=100,
            ),
        )
    )
    store.apply(
        _event(
            event_name="PaymentFinalized",
            transaction_hash="0xpaytx",
            block_number=22,
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="7",
                buyer="0xbuyer",
                status="PAID",
                amount_wei=100,
                payer="0xpayer",
                payment_token="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                payment_source="0x1234",
                settlement_beneficiary="0xowner",
                dividend_eligible=True,
                refund_authorized=True,
            ),
        )
    )

    with session_factory() as db:
        order = db.get(Order, "o-1")
        payment = db.get(Payment, "p-1")
        machine = db.get(Machine, "m-1")
        metadata = dict(order.execution_metadata or {})
        assert order.onchain_order_id == "42"
        assert order.onchain_machine_id == "7"
        assert order.create_order_event_id == "133:22:0xpaytx:1"
        assert order.create_order_block_number == 22
        assert payment.state == PaymentState.SUCCEEDED
        assert payment.callback_tx_hash == "0xpaytx"
        assert payment.callback_state == PaymentState.SUCCEEDED.value
        assert order.settlement_beneficiary_user_id == "owner-1"
        assert order.settlement_is_self_use is False
        assert order.settlement_is_dividend_eligible is True
        assert metadata["authoritative_order_status"] == "PAID"
        assert metadata["authoritative_paid_projection"] is True
        assert machine.has_active_tasks is True


def test_sql_projection_does_not_downgrade_paid_truth_when_order_classified_arrives_after_payment() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1")
        order = Order(
            id="o-1",
            onchain_order_id="42",
            onchain_machine_id="7",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
        )
        payment = Payment(
            id="p-1",
            order_id="o-1",
            provider="onchain_router",
            amount_cents=100,
            currency="USDC",
            state=PaymentState.PENDING,
        )
        db.add(machine)
        db.add(order)
        db.add(payment)
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory, owner_resolver=lambda _: "owner-1")
    store.apply(
        _event(
            event_name="PaymentFinalized",
            transaction_hash="0xpaytx",
            block_number=22,
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="7",
                buyer="0xbuyer",
                status="PAID",
                amount_wei=100,
                payer="0xpayer",
                payment_token="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                payment_source="0x1234",
                settlement_beneficiary="0xowner",
                dividend_eligible=True,
                refund_authorized=True,
            ),
        )
    )
    store.apply(
        _event(
            event_name="OrderClassified",
            transaction_hash="0xpaytx",
            block_number=22,
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="7",
                buyer="0xbuyer",
                status="CLASSIFIED",
                amount_wei=None,
                settlement_beneficiary="0xowner",
                dividend_eligible=True,
                refund_authorized=True,
            ),
        )
    )

    with session_factory() as db:
        order = db.get(Order, "o-1")
        machine = db.get(Machine, "m-1")
        metadata = dict(order.execution_metadata or {})
        assert metadata["authoritative_order_status"] == "PAID"
        assert metadata["authoritative_paid_projection"] is True
        assert machine.has_active_tasks is True


def test_sql_projection_marks_order_cancelled_and_expired_from_onchain_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1", has_active_tasks=True)
        order = Order(
            id="o-1",
            onchain_order_id="42",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=100,
        )
        db.add(machine)
        db.add(order)
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="OrderCancelled",
            block_number=24,
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="m-1",
                buyer=None,
                status="CANCELLED",
                amount_wei=None,
                cancelled_at=1_712_553_600,
                cancelled_as_expired=True,
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "m-1")
        order = db.get(Order, "o-1")
        metadata = dict(order.execution_metadata or {})
        assert machine.has_active_tasks is False
        assert order.state == OrderState.CANCELLED
        assert order.preview_state == PreviewState.EXPIRED
        assert order.cancelled_at == datetime.fromtimestamp(1_712_553_600, tz=timezone.utc).replace(tzinfo=None)
        assert metadata["authoritative_order_status"] == "CANCELLED"
        assert metadata["authoritative_paid_projection"] is False
        assert metadata["cancelled_as_expired"] is True


def test_sql_projection_records_machine_claim_from_revenue_claimed_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(
            id="m-1",
            onchain_machine_id="7",
            display_name="node",
            owner_user_id="owner-1",
            has_unsettled_revenue=True,
        )
        order = Order(
            id="o-1",
            onchain_order_id="42",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=1000,
            state=OrderState.RESULT_CONFIRMED,
            settlement_state=SettlementState.DISTRIBUTED,
        )
        settlement = SettlementRecord(
            order_id="o-1",
            gross_amount_cents=1000,
            platform_fee_cents=100,
            machine_share_cents=900,
            state=SettlementState.DISTRIBUTED,
        )
        db.add_all([machine, order, settlement])
        db.flush()
        db.add(
            RevenueEntry(
                order_id="o-1",
                settlement_id=settlement.id,
                machine_id="m-1",
                beneficiary_user_id="owner-1",
                gross_amount_cents=1000,
                platform_fee_cents=100,
                machine_share_cents=900,
                is_self_use=False,
                is_dividend_eligible=True,
            )
        )
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="MachineRevenueClaimedDetailed",
            transaction_hash="0xclaimtx",
            block_number=23,
            payload=RevenueClaimedEvent(
                machine_id="7",
                account="0xowner",
                amount_wei=_pwr_wei_for_cents(900),
                claim_nonce=None,
                claim_kind="machine_revenue",
                remaining_claimable_wei=0,
                remaining_unsettled_wei=0,
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "m-1")
        claims = db.query(MachineRevenueClaim).filter(MachineRevenueClaim.machine_id == "m-1").all()
        assert machine.has_unsettled_revenue is False
        assert len(claims) == 1
        assert claims[0].amount_cents == 900
        assert claims[0].tx_hash == "0xclaimtx"


def test_sql_projection_keeps_machine_locked_when_claim_event_reports_remaining_unsettled() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(
            id="m-1",
            onchain_machine_id="7",
            display_name="node",
            owner_user_id="owner-1",
            has_unsettled_revenue=True,
        )
        db.add(machine)
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="MachineRevenueClaimedDetailed",
            transaction_hash="0xclaimtx-2",
            block_number=24,
            payload=RevenueClaimedEvent(
                machine_id="7",
                account="0xowner",
                amount_wei=_pwr_wei_for_cents(400),
                claim_nonce=None,
                claim_kind="machine_revenue",
                remaining_claimable_wei=0,
                remaining_unsettled_wei=_pwr_wei_for_cents(500),
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "m-1")
        claims = db.query(MachineRevenueClaim).filter(MachineRevenueClaim.machine_id == "m-1").all()
        assert machine.has_unsettled_revenue is True
        assert len(claims) == 1
        assert claims[0].amount_cents == 400


def test_sql_projection_creates_rejected_valid_preview_settlement_projection() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1", has_active_tasks=True)
        order = Order(
            id="o-1",
            onchain_order_id="42",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=1000,
            settlement_state=SettlementState.LOCKED,
            settlement_beneficiary_user_id="owner-1",
            settlement_is_self_use=False,
            settlement_is_dividend_eligible=True,
        )
        payment = Payment(
            order_id="o-1",
            provider="hsp",
            amount_cents=1000,
            currency="USDC",
            state=PaymentState.SUCCEEDED,
        )
        db.add_all([machine, order, payment])
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="OrderSettled",
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="m-1",
                buyer="0xbuyer",
                status="REJECTED",
                amount_wei=1000,
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "m-1")
        order = db.get(Order, "o-1")
        settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == "o-1").first()
        entry = db.query(RevenueEntry).filter(RevenueEntry.order_id == "o-1").first()
        assert machine.has_active_tasks is False
        assert machine.has_unsettled_revenue is True
        assert order.state == OrderState.CANCELLED
        assert order.settlement_state == SettlementState.DISTRIBUTED
        assert settlement is not None
        assert settlement.gross_amount_cents == 1000
        assert settlement.platform_fee_cents == 30
        assert settlement.machine_share_cents == 270
        assert entry is not None
        assert entry.platform_fee_cents == 30
        assert entry.machine_share_cents == 270
        assert entry.beneficiary_user_id == "owner-1"


def test_sql_projection_creates_refunded_settlement_projection_and_marks_payment_refunded() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with session_factory() as db:
        machine = Machine(id="m-1", display_name="node", owner_user_id="owner-1", has_active_tasks=True)
        order = Order(
            id="o-1",
            onchain_order_id="42",
            user_id="u-1",
            machine_id="m-1",
            chat_session_id="chat-1",
            user_prompt="build",
            recommended_plan_summary="plan",
            quoted_amount_cents=1000,
            settlement_state=SettlementState.LOCKED,
            settlement_beneficiary_user_id="owner-1",
            settlement_is_self_use=False,
            settlement_is_dividend_eligible=True,
        )
        payment = Payment(
            order_id="o-1",
            provider="onchain_router",
            amount_cents=1000,
            currency="USDC",
            state=PaymentState.SUCCEEDED,
        )
        db.add_all([machine, order, payment])
        db.commit()

    store = SqlProjectionStore(session_factory=session_factory)
    store.apply(
        _event(
            event_name="OrderSettled",
            transaction_hash="0xrefundtx",
            payload=OrderLifecycleEvent(
                order_id="42",
                machine_id="m-1",
                buyer="0xbuyer",
                status="REFUNDED",
                amount_wei=1000,
            ),
        )
    )

    with session_factory() as db:
        machine = db.get(Machine, "m-1")
        order = db.get(Order, "o-1")
        payment = db.query(Payment).filter(Payment.order_id == "o-1").first()
        settlement = db.query(SettlementRecord).filter(SettlementRecord.order_id == "o-1").first()
        entry = db.query(RevenueEntry).filter(RevenueEntry.order_id == "o-1").first()
        assert machine.has_active_tasks is False
        assert machine.has_unsettled_revenue is False
        assert order.state == OrderState.CANCELLED
        assert order.settlement_state == SettlementState.DISTRIBUTED
        assert payment.state == PaymentState.REFUNDED
        assert settlement is not None
        assert settlement.gross_amount_cents == 1000
        assert settlement.platform_fee_cents == 0
        assert settlement.machine_share_cents == 0
        assert entry is not None
        assert entry.platform_fee_cents == 0
        assert entry.machine_share_cents == 0


def test_sql_projection_records_refund_and_platform_claims_in_unified_claim_ledger() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    store = SqlProjectionStore(
        session_factory=session_factory,
        owner_resolver=lambda wallet: {
            "0xbuyer000000000000000000000000000000000000": "buyer-1",
            "0xtreasury00000000000000000000000000000000": "platform",
        }.get(wallet),
    )
    store.apply(
        _event(
            event_name="RefundClaimedDetailed",
            transaction_hash="0xrefundclaim",
            payload=RevenueClaimedEvent(
                machine_id=None,
                account="0xbuyer000000000000000000000000000000000000",
                amount_wei=700,
                claim_nonce=None,
                claim_kind="refund",
                token_address="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                remaining_account_balance_wei=0,
            ),
        )
    )
    store.apply(
        _event(
            event_name="PlatformRevenueClaimedDetailed",
            transaction_hash="0xplatformclaim",
            payload=RevenueClaimedEvent(
                machine_id=None,
                account="0xtreasury00000000000000000000000000000000",
                amount_wei=30,
                claim_nonce=None,
                claim_kind="platform_revenue",
                token_address="0x79aec4eea31d50792f61d1ca0733c18c89524c9e",
                remaining_account_balance_wei=0,
            ),
        )
    )

    with session_factory() as db:
        claims = db.query(SettlementClaimRecord).order_by(SettlementClaimRecord.claimed_at.asc()).all()
        assert len(claims) == 2
        assert claims[0].claim_kind == "refund"
        assert claims[0].claimant_user_id == "buyer-1"
        assert claims[0].amount_cents == 700
        assert claims[1].claim_kind == "platform_revenue"
        assert claims[1].claimant_user_id == "platform"
        assert claims[1].amount_cents == 30
