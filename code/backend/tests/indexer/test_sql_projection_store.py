from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.enums import OrderState, PaymentState, SettlementState
from app.domain.models import Base, Machine, Order, Payment
from app.indexer.events import MachineAssetEvent, NormalizedEvent, OrderLifecycleEvent, SettlementSplitEvent
from app.indexer.sql_projection import SqlProjectionStore


def _event(*, payload, event_name: str) -> NormalizedEvent:
    return NormalizedEvent(
        event_id="133:10:0xabc:1",
        chain_id=133,
        contract_name="OrderBook",
        contract_address="0x3000000000000000000000000000000000000003",
        event_name=event_name,
        block_number=10,
        block_hash="0xblock",
        transaction_hash="0xabc",
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
        assert machine.ownership_source == "chain"


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
