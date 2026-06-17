"""Tests for the audit-log merge logic (numobel.sync.audit_sync).

The merge semantics are data-correctness critical: the audit log is append-only
per-device history that must be UNIONED across devices by ``uuid`` so NO device
ever loses history. These tests use two separate sqlite DBs ("device A" and
"device B") sharing ONE FakeBackend instance as "the cloud".
"""

from __future__ import annotations

import sqlite3

from numobel import audit, db
from numobel.sync import audit_sync
from tests.sync_fakes import FakeBackend


def _device() -> sqlite3.Connection:
    """A fresh in-memory device DB with the schema applied."""
    conn = db.connect(":memory:")
    db.create_schema(conn)
    return conn


def _log(conn: sqlite3.Connection, action: str, entity: str, **kw) -> None:
    """Append one audit entry (minting a uuid) and commit it locally."""
    audit.log_change(conn, action, entity, **kw)
    conn.commit()


def _uuids(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT uuid FROM audit_log")}


def test_push_audit_unions_into_cloud():
    a = _device()
    _log(a, "create", "product", entity_id=1)
    _log(a, "update", "product", entity_id=1)

    merged = audit_sync.push_audit(a, FakeBackend())  # local-only cloud start

    # Re-run against a shared cloud to assert the count lands.
    cloud = FakeBackend()
    merged = audit_sync.push_audit(a, cloud)
    assert merged == 2
    assert len(cloud.audit_log) == 2


def test_push_audit_preserves_other_device_entries():
    cloud = FakeBackend()
    # Cloud already holds device B's single entry.
    b = _device()
    _log(b, "delete", "brand", entity_id=9)
    audit_sync.push_audit(b, cloud)
    assert len(cloud.audit_log) == 1
    b_uuids = _uuids(b)

    # Device A (2 local) pushes — B's entry must NOT be clobbered.
    a = _device()
    _log(a, "create", "product", entity_id=1)
    _log(a, "update", "product", entity_id=1)
    merged = audit_sync.push_audit(a, cloud)

    assert merged == 3
    cloud_uuids = {r["uuid"] for r in cloud.audit_log}
    assert b_uuids <= cloud_uuids  # B preserved
    assert _uuids(a) <= cloud_uuids  # A added


def test_pull_audit_absorbs_cloud_only():
    cloud = FakeBackend()
    a = _device()
    _log(a, "create", "product", entity_id=1)
    _log(a, "update", "product", entity_id=2)
    audit_sync.push_audit(a, cloud)

    b = _device()
    n = audit_sync.pull_audit(b, cloud)
    b.commit()

    assert n == 2
    assert _uuids(b) == _uuids(a)


def test_pull_audit_idempotent():
    cloud = FakeBackend()
    a = _device()
    _log(a, "create", "product", entity_id=1)
    _log(a, "update", "product", entity_id=2)
    audit_sync.push_audit(a, cloud)

    b = _device()
    first = audit_sync.pull_audit(b, cloud)
    b.commit()
    count_after_first = b.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    second = audit_sync.pull_audit(b, cloud)
    b.commit()
    count_after_second = b.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    assert first == 2
    assert second == 0  # no duplicates inserted
    assert count_after_first == count_after_second == 2


def test_pull_audit_never_deletes_local():
    cloud = FakeBackend()
    a = _device()
    _log(a, "create", "product", entity_id=1)
    audit_sync.push_audit(a, cloud)

    # Device B has its own entry X, which the cloud does NOT contain.
    b = _device()
    _log(b, "rename", "color_group", entity_id=42)
    x_uuids = _uuids(b)

    n = audit_sync.pull_audit(b, cloud)
    b.commit()

    assert n == 1  # only A's entry absorbed
    after = _uuids(b)
    assert x_uuids <= after  # X preserved
    assert _uuids(a) <= after  # A's entry added


def test_two_devices_converge():
    cloud = FakeBackend()

    a = _device()
    _log(a, "create", "product", entity_id=1)
    audit_sync.push_audit(a, cloud)

    b = _device()
    audit_sync.pull_audit(b, cloud)
    b.commit()
    _log(b, "update", "product", entity_id=1)
    audit_sync.push_audit(b, cloud)

    audit_sync.pull_audit(a, cloud)
    a.commit()

    full_union = {r["uuid"] for r in cloud.audit_log}
    assert len(full_union) == 2
    assert _uuids(a) == full_union
    assert _uuids(b) == full_union


def test_merge_preserves_entity_id_none_and_blank_details():
    cloud = FakeBackend()
    a = _device()
    # entity_id None and details "" — both edge values.
    audit.log_change(a, "noop", "system", entity_id=None, details="")
    a.commit()
    audit_sync.push_audit(a, cloud)

    b = _device()
    audit_sync.pull_audit(b, cloud)
    b.commit()

    row = b.execute(
        "SELECT entity_id, details FROM audit_log"
    ).fetchone()
    assert row["entity_id"] is None
    assert row["details"] == ""


def test_pull_audit_does_not_commit():
    cloud = FakeBackend()
    a = _device()
    _log(a, "create", "product", entity_id=1)
    audit_sync.push_audit(a, cloud)
    pushed_uuids = _uuids(a)

    b = _device()
    n = audit_sync.pull_audit(b, cloud)
    assert n == 1
    # Visible on this connection before commit.
    assert _uuids(b) == pushed_uuids

    # No commit happened — rolling back undoes the insert.
    b.rollback()
    assert b.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_absorb_skips_blank_uuid():
    """Defensive: rows with blank/None uuid are never inserted."""
    b = _device()
    rows = [
        {"uuid": "", "ts": "t", "action": "a", "entity": "e",
         "entity_id": None, "details": None},
        {"uuid": None, "ts": "t", "action": "a", "entity": "e",
         "entity_id": None, "details": None},
        {"uuid": "good", "ts": "t", "action": "a", "entity": "e",
         "entity_id": 1, "details": "x"},
    ]
    n = audit_sync._absorb(b, rows)
    b.commit()
    assert n == 1
    assert _uuids(b) == {"good"}
