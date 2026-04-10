import ast
from pathlib import Path


def test_slice_a_migration_file_exists_and_targets_orders_cancelled_at() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260408_01_add_orders_cancelled_at.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    ast.parse(source)

    assert 'revision = "20260408_01"' in source
    assert 'with op.batch_alter_table("orders") as batch_op:' in source
    assert 'batch_op.add_column(sa.Column("cancelled_at"' in source


def test_self_use_execution_run_migration_file_exists() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260409_01_add_self_use_execution_run_fields.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    ast.parse(source)

    assert 'revision = "20260409_01"' in source
    assert 'down_revision = "20260408_01"' in source
    assert 'with op.batch_alter_table("execution_runs") as batch_op:' in source
    assert 'batch_op.add_column(sa.Column("machine_id"' in source
    assert 'batch_op.alter_column("order_id"' in source


def test_primary_issuance_migration_file_exists() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260410_01_add_primary_issuance_tables.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    ast.parse(source)

    assert 'revision = "20260410_01"' in source
    assert 'down_revision = "20260409_02"' in source
    assert 'op.create_table("primary_issuance_skus"' in source
    assert 'op.create_table("primary_issuance_purchases"' in source
