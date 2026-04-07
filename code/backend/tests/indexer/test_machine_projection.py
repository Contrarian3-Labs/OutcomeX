from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.models import Base, Machine
from app.indexer.events import MachineAssetEvent, NormalizedEvent
from app.indexer.projections import InMemoryProjectionStore
from app.integrations.machine_ownership_projection import MachineOwnershipProjectionIntegrator


def test_projection_store_keeps_latest_machine_owner_projection() -> None:
    projection = InMemoryProjectionStore()
    event = NormalizedEvent(
        event_id="177:12:0xowner:1",
        chain_id=177,
        contract_name="MachineAssetNFT",
        contract_address="0x1000000000000000000000000000000000000001",
        event_name="Transfer",
        block_number=12,
        block_hash="0xblock-12",
        transaction_hash="0xowner",
        log_index=1,
        payload=MachineAssetEvent(
            machine_id="88",
            owner="0x2222222222222222222222222222222222222222",
            metadata_uri=None,
            pwr_quota=None,
        ),
    )

    projection.apply(event)

    ownership = projection.get_machine_ownership("88")
    assert ownership.machine_id == "88"
    assert ownership.chain_owner == "0x2222222222222222222222222222222222222222"
    assert ownership.last_event_id == "177:12:0xowner:1"


def test_machine_ownership_integrator_applies_projected_owner_truth() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        machine = Machine(
            display_name="GANA node",
            owner_user_id="owner-1",
            onchain_machine_id="88",
            pending_transfer_new_owner_user_id="owner-2",
            pending_transfer_keep_previous_setup=False,
        )
        db.add(machine)
        db.commit()
        db.refresh(machine)

        integrator = MachineOwnershipProjectionIntegrator(
            owner_resolver=lambda chain_owner: {
                "0x2222222222222222222222222222222222222222": "owner-2",
            }.get(chain_owner),
        )

        outcome = integrator.apply_machine_owner_projection(
            db=db,
            machine_id="88",
            chain_owner="0x2222222222222222222222222222222222222222",
            event_id="177:12:0xowner:1",
        )

        assert outcome.applied is True
        assert outcome.machine_id == "88"
        assert outcome.owner_user_id == "owner-2"
        assert outcome.chain_owner == "0x2222222222222222222222222222222222222222"

        db.refresh(machine)
        assert machine.owner_user_id == "owner-2"
        assert machine.owner_chain_address == "0x2222222222222222222222222222222222222222"
        assert machine.ownership_source == "chain"
        assert machine.owner_projection_last_event_id == "177:12:0xowner:1"
        assert machine.pending_transfer_new_owner_user_id is None


def test_machine_ownership_integrator_persists_chain_owner_even_when_user_mapping_is_missing() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        machine = Machine(
            display_name="GANA node",
            owner_user_id="owner-1",
            onchain_machine_id="88",
        )
        db.add(machine)
        db.commit()
        db.refresh(machine)

        integrator = MachineOwnershipProjectionIntegrator(owner_resolver=lambda _chain_owner: None)

        outcome = integrator.apply_machine_owner_projection(
            db=db,
            machine_id="88",
            chain_owner="0x3333333333333333333333333333333333333333",
            event_id="177:13:0xowner:2",
        )

        assert outcome.applied is False
        assert outcome.reason == "owner_unresolved"
        assert outcome.chain_owner == "0x3333333333333333333333333333333333333333"

        db.refresh(machine)
        assert machine.owner_user_id == "owner-1"
        assert machine.owner_chain_address == "0x3333333333333333333333333333333333333333"
        assert machine.owner_projection_last_event_id == "177:13:0xowner:2"
        assert machine.owner_projected_at is not None
