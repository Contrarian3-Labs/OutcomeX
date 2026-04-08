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
