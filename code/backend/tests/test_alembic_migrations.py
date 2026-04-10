import ast
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.db.base import Base

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


def test_attachments_migration_file_exists() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260410_02_add_attachments_table.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    ast.parse(source)

    assert 'revision = "20260410_02"' in source
    assert 'down_revision = "20260410_01"' in source
    assert 'op.create_table("attachments"' in source
    assert 'op.create_index("ix_attachments_user_id", "attachments"' in source


def test_attachment_session_scope_migration_contains_rekey_and_backfill_logic() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260410_03_rekey_attachments_to_session_scope.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    ast.parse(source)
    assert 'revision = "20260410_03"' in source
    assert 'down_revision = "20260410_02"' in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert "batch_op.add_column(sa.Column(\"session_kind\"" in source
    assert "batch_op.add_column(sa.Column(\"session_id\"" in source
    assert "UPDATE attachments SET session_kind = 'legacy_user', session_id = user_id" in source
    assert "batch_op.drop_column(\"user_id\")" in source
    assert "op.create_index(\"ix_attachments_session_context\", \"attachments\"" in source
    assert "batch_op.add_column(sa.Column(\"user_id\"" in source
    assert "UPDATE attachments SET user_id = COALESCE(user_id, session_id)" in source
    assert "batch_op.drop_column(\"session_kind\")" in source
    assert "batch_op.drop_column(\"session_id\")" in source
    assert "op.create_index(\"ix_attachments_user_id\", \"attachments\"" in source


def test_attachment_server_session_migration_contains_expected_operations() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260410_04_add_attachment_sessions_table.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    ast.parse(source)

    assert 'revision = "20260410_04"' in source
    assert 'down_revision = "20260410_03"' in source
    assert 'op.create_table("attachment_sessions"' in source
    assert 'batch_op.add_column(sa.Column("attachment_session_id"' in source
    assert "INSERT INTO attachment_sessions" in source
    assert 'batch_op.drop_column("session_kind")' in source
    assert 'batch_op.drop_column("session_id")' in source
    assert "batch_op.create_foreign_key(" in source
    assert '"fk_attachments_attachment_session_id"' in source


def test_runtime_metadata_contains_attachment_session_structures(tmp_path) -> None:
    db_path = tmp_path / "attachments-runtime.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    table_names = set(inspector.get_table_names())
    assert "attachment_sessions" in table_names
    assert "attachments" in table_names

    attachment_columns = {column["name"] for column in inspector.get_columns("attachments")}
    assert "attachment_session_id" in attachment_columns
    assert "session_kind" not in attachment_columns
    assert "session_id" not in attachment_columns

    indexes = {index["name"] for index in inspector.get_indexes("attachments")}
    assert "ix_attachments_attachment_session_id" in indexes
