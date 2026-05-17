from __future__ import annotations

import pytest

from app.services.tracing.migrations import (
    LEGACY_V1_CHECKSUM,
    TRACE_MIGRATIONS,
    TraceMigration,
    ensure_migration_table,
    migrate_trace_schema,
    validate_migrations,
)


def test_trace_migration_checksum_is_stable_and_content_based() -> None:
    migration = TraceMigration(1, "base", ("SELECT 1", "SELECT 2"))
    same = TraceMigration(1, "base", ("SELECT 1", "SELECT 2"))
    changed = TraceMigration(1, "base", ("SELECT 1", "SELECT 3"))

    assert migration.checksum == same.checksum
    assert migration.checksum != changed.checksum


def test_validate_migrations_rejects_invalid_definitions() -> None:
    with pytest.raises(ValueError):
        validate_migrations((TraceMigration(1, "one", ("SELECT 1",)), TraceMigration(1, "two", ("SELECT 2",))))
    with pytest.raises(ValueError):
        validate_migrations((TraceMigration(0, "zero", ("SELECT 1",)),))
    with pytest.raises(ValueError):
        validate_migrations((TraceMigration(1, "", ("SELECT 1",)),))
    with pytest.raises(ValueError):
        validate_migrations((TraceMigration(1, "empty", ()),))


def test_ensure_migration_table_does_not_insert_managed_migration() -> None:
    conn = _FakeConnection()

    ensure_migration_table(conn)

    assert conn.records == {}
    assert any("CREATE TABLE IF NOT EXISTS trace_schema_migrations" in sql for sql in conn.executed)


def test_migrate_trace_schema_applies_missing_migrations_in_version_order() -> None:
    conn = _FakeConnection()
    migrations = (
        TraceMigration(2, "second", ("SELECT 2",)),
        TraceMigration(1, "first", ("SELECT 1",)),
    )

    migrate_trace_schema(conn, migrations)

    assert list(conn.records) == [1, 2]
    assert conn.records[1]["checksum"] == migrations[1].checksum
    assert conn.records[2]["checksum"] == migrations[0].checksum


def test_migrate_trace_schema_skips_matching_checksum() -> None:
    migration = TraceMigration(1, "first", ("SELECT 1",))
    conn = _FakeConnection(records={1: {"name": "first", "checksum": migration.checksum}})

    migrate_trace_schema(conn, (migration,))

    assert conn.executed.count("SELECT 1") == 0


def test_migrate_trace_schema_backfills_legacy_v1_null_checksum() -> None:
    migration = TraceMigration(1, "first", ("SELECT 1",))
    conn = _FakeConnection(records={1: {"name": "legacy", "checksum": None}})

    migrate_trace_schema(conn, (migration,))

    assert conn.records[1]["name"] == "first"
    assert conn.records[1]["checksum"] == migration.checksum
    assert "SELECT 1" in conn.executed


def test_migrate_trace_schema_backfills_allowlisted_legacy_v1_checksum() -> None:
    migration = TraceMigration(1, "first", ("SELECT 1",))
    conn = _FakeConnection(records={1: {"name": "legacy", "checksum": LEGACY_V1_CHECKSUM}})

    migrate_trace_schema(conn, (migration,))

    assert conn.records[1]["checksum"] == migration.checksum


def test_migrate_trace_schema_rejects_checksum_mismatch() -> None:
    migration = TraceMigration(1, "first", ("SELECT 1",))
    conn = _FakeConnection(records={1: {"name": "first", "checksum": "not-allowed"}})

    with pytest.raises(RuntimeError):
        migrate_trace_schema(conn, (migration,))


def test_migrate_trace_schema_rejects_version_two_checksum_mismatch() -> None:
    migration = TraceMigration(2, "second", ("SELECT 2",))
    conn = _FakeConnection(records={2: {"name": "second", "checksum": None}})

    with pytest.raises(RuntimeError):
        migrate_trace_schema(conn, (migration,))


def test_migrate_trace_schema_does_not_insert_record_after_statement_failure() -> None:
    migration = TraceMigration(1, "bad", ("SELECT 1", "BROKEN SQL"))
    conn = _FakeConnection(fail_on="BROKEN SQL")

    with pytest.raises(RuntimeError):
        migrate_trace_schema(conn, (migration,))

    assert conn.records == {}


def test_trace_migrations_define_v1_and_v2_only() -> None:
    assert [migration.version for migration in TRACE_MIGRATIONS] == [1, 2]
    assert [migration.name for migration in TRACE_MIGRATIONS] == [
        "trace_base_schema",
        "question_persistence_fields",
    ]
    assert all(migration.checksum for migration in TRACE_MIGRATIONS)


class _FakeConnection:
    def __init__(self, records=None, fail_on: str | None = None) -> None:
        self.records = dict(records or {})
        self.fail_on = fail_on
        self.executed: list[str] = []
        self._fetchall: list[tuple] = []

    def cursor(self):
        return _FakeCursor(self)


class _FakeCursor:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=None):
        normalized = " ".join(str(sql).split())
        self.conn.executed.append(str(sql).strip())
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("statement failed")
        if normalized.startswith("SELECT version, name, checksum FROM trace_schema_migrations"):
            self.conn._fetchall = [
                (version, record["name"], record["checksum"])
                for version, record in self.conn.records.items()
            ]
            return
        if normalized.startswith("INSERT INTO trace_schema_migrations"):
            version, name, checksum = params
            self.conn.records[int(version)] = {"name": name, "checksum": checksum}
            return
        if normalized.startswith("UPDATE trace_schema_migrations"):
            name, checksum, version = params
            self.conn.records[int(version)] = {"name": name, "checksum": checksum}

    def fetchall(self):
        return self.conn._fetchall
