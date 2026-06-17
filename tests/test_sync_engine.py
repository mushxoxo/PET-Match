"""Tests for the M4 sync orchestration layer (push / pull / conflict).

Exercised entirely offline against the in-memory ``FakeBackend``: no event loop,
no ``google`` imports. A tmp dir is monkeypatched in for ``db.base_dir`` /
``db.images_dir`` so the photo logic has somewhere to look (with no photos the
maps are simply empty).
"""

from __future__ import annotations

import sqlite3

import pytest

from numobel import audit, db
from numobel.sync import engine, serialize, state
from numobel.sync.errors import ConflictError, is_offline_error
from tests.sync_fakes import FakeBackend


def _sample_db() -> sqlite3.Connection:
    """An in-memory catalog exercising every snapshot table and FK column."""
    conn = db.connect(":memory:")
    db.create_schema(conn)
    conn.executescript(
        """
        INSERT INTO brands(id, code, name, has_sheet) VALUES
            (1, 'AT', 'Acoustic Tech', 1),
            (2, 'NU', 'Numobel', 0);

        INSERT INTO color_groups(id, note, created_at) VALUES
            (10, 'reds', '2026-01-01T00:00:00');

        INSERT INTO products
            (id, brand_id, sku, shade_no, color_name, thickness, self_label,
             category, extra_json, image_path, source_sheet, source_row,
             color_group_id) VALUES
            (100, 1, 'AT05', '5', 'Crimson', 1.5, 'lbl', 'cat',
             '{"k": "v"}', NULL, 'AT', 3, 10),
            (101, 2, 'NU05', '5', 'Scarlet', NULL, NULL, NULL,
             NULL, NULL, '(synthesized)', NULL, 10);

        INSERT INTO color_links
            (id, from_product_id, to_product_id, to_brand_code, raw_ref,
             normalized, status, source, note, created_at) VALUES
            (200, 100, NULL, 'PCP', 'PCP12', 'PCP12', 'external', 'import',
             NULL, '2026-01-01T00:00:00'),
            (201, 100, NULL, 'AT', 'AT99', 'AT99', 'unresolved', 'user',
             'manual', '2026-01-02T00:00:00');

        INSERT INTO prices
            (id, seller, mrp, mrp_sft, dp, dp_sft, profit, discount,
             cust_price, cust_price_sft) VALUES
            (300, 'ACME', 100.0, 10.0, 80.0, 8.0, 20.0, 0.2, 90.0, 9.0);
        """
    )
    conn.commit()
    return conn


def _dump(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """All rows of ``table`` as plain tuples, ordered by primary key."""
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


@pytest.fixture(autouse=True)
def _isolate_images(tmp_path, monkeypatch):
    """Point base_dir/images_dir at a tmp dir so photo logic stays self-contained."""
    images = tmp_path / "images"
    images.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(db, "images_dir", lambda: images)
    yield


def test_first_push_to_fresh_backend():
    src = _sample_db()
    backend = FakeBackend()

    result = engine.push(src, backend)

    assert result.revision == 1
    assert state.get_last_synced_revision(src) == 1
    assert backend.meta["revision"] == "1"
    assert backend.meta["last_writer_device"]  # set to this device's id
    assert backend.meta["format"] == engine.META_FORMAT
    assert state.is_pending(src) is False
    # The lossless blob equals the local dump verbatim.
    assert backend.read_all() == serialize.dump_rows(src)


def test_push_then_pull_roundtrip():
    src = _sample_db()
    backend = FakeBackend()
    engine.push(src, backend)

    dest = db.connect(":memory:")
    db.create_schema(dest)
    result = engine.pull(dest, backend)

    assert result.revision == 1
    for table in serialize.SNAPSHOT_TABLES:
        assert _dump(src, table) == _dump(dest, table), f"mismatch in {table}"
    assert state.get_last_synced_revision(dest) == 1


def test_conflict_aborts_push_without_writing():
    src = _sample_db()
    backend = FakeBackend()
    engine.push(src, backend)  # last_synced -> 1

    # Someone else advanced the sheet behind our back.
    backend.meta["revision"] = "5"
    data_before = backend.read_all()

    with pytest.raises(ConflictError) as excinfo:
        engine.push(src, backend)

    assert excinfo.value.local_revision == 1
    assert excinfo.value.cloud_revision == 5
    # Nothing written: data unchanged, meta only carries the external revision.
    assert backend.read_all() == data_before
    assert backend.meta["revision"] == "5"
    assert state.get_last_synced_revision(src) == 1


def test_resolve_conflict_local_overwrites_cloud():
    src = _sample_db()
    backend = FakeBackend()
    engine.push(src, backend)
    backend.meta["revision"] = "5"  # conflict state

    result = engine.resolve_conflict(src, backend, "local")

    assert isinstance(result, engine.PushResult)
    assert result.revision == 6  # cloud 5 + 1
    assert backend.meta["revision"] == "6"
    assert backend.read_all() == serialize.dump_rows(src)
    assert state.get_last_synced_revision(src) == 6


def test_resolve_conflict_cloud_discards_local():
    local = _sample_db()
    backend = FakeBackend()

    # Seed the cloud with a DIFFERENT catalog at revision 5.
    cloud = db.connect(":memory:")
    db.create_schema(cloud)
    cloud.execute(
        "INSERT INTO brands(id, code, name, has_sheet) VALUES (9, 'ZZ', 'Zed', 1)"
    )
    cloud.commit()
    backend.write_all(serialize.dump_rows(cloud))
    backend.write_meta({"revision": "5"})

    result = engine.resolve_conflict(local, backend, "cloud")

    assert isinstance(result, engine.PullResult)
    assert result.revision == 5
    for table in serialize.SNAPSHOT_TABLES:
        assert _dump(local, table) == _dump(cloud, table), f"mismatch in {table}"
    assert state.get_last_synced_revision(local) == 5


def test_resolve_conflict_rejects_unknown_choice():
    src = _sample_db()
    backend = FakeBackend()
    with pytest.raises(ValueError):
        engine.resolve_conflict(src, backend, "merge")


def test_offline_error_propagates_and_keeps_pending():
    class OfflineBackend(FakeBackend):
        def write_all(self, data):
            raise ConnectionError("no network")

    src = _sample_db()
    backend = OfflineBackend()
    state.set_pending(src, True)

    with pytest.raises(ConnectionError) as excinfo:
        engine.push(src, backend)

    assert is_offline_error(excinfo.value) is True
    # Push must NOT clear the pending flag on failure (that is M5's job).
    assert state.is_pending(src) is True


def test_pull_applies_migrate_folding_resolved_links():
    backend = FakeBackend()

    # Seed cloud data with two products joined by a RESOLVED color_link.
    cloud = db.connect(":memory:")
    db.create_schema(cloud)
    cloud.executescript(
        """
        INSERT INTO brands(id, code, name, has_sheet) VALUES (1, 'AT', 'A', 1);
        INSERT INTO products(id, brand_id, sku) VALUES
            (100, 1, 'AT05'),
            (101, 1, 'AT06');
        INSERT INTO color_links
            (id, from_product_id, to_product_id, status, source, created_at)
            VALUES (200, 100, 101, 'resolved', 'user', '2026-01-01T00:00:00');
        """
    )
    cloud.commit()
    backend.write_all(serialize.dump_rows(cloud))
    backend.write_meta({"revision": "3"})

    dest = db.connect(":memory:")
    db.create_schema(dest)
    engine.pull(dest, backend)

    # migrate ran: the resolved link folded into a shared color_group_id and the
    # link itself was consumed (mirrors what _import_catalog produces).
    groups = dict(
        dest.execute("SELECT id, color_group_id FROM products ORDER BY id")
    )
    assert groups[100] is not None
    assert groups[100] == groups[101]
    remaining = dest.execute(
        "SELECT COUNT(*) FROM color_links WHERE status = 'resolved'"
    ).fetchone()[0]
    assert remaining == 0


def test_pull_from_empty_backend_is_first_link():
    """Pull against a fresh/empty backend: revision 0, empty catalog, watermark 0."""
    backend = FakeBackend()  # data={}, no meta written

    dest = db.connect(":memory:")
    db.create_schema(dest)
    result = engine.pull(dest, backend)

    assert result.revision == 0
    for table in serialize.SNAPSHOT_TABLES:
        count = dest.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, f"{table} should be empty after first-link pull"
    assert state.get_last_synced_revision(dest) == 0
    assert state.is_pending(dest) is False


def test_push_pull_push_subsequent_push_proceeds():
    """A pull's watermark lets a later push through without a spurious conflict."""
    conn = _sample_db()
    backend = FakeBackend()

    first = engine.push(conn, backend)
    assert first.revision == 1
    assert state.get_last_synced_revision(conn) == 1

    # Pull on the SAME connection re-records the cloud revision as our watermark.
    pulled = engine.pull(conn, backend)
    assert pulled.revision == 1
    assert state.get_last_synced_revision(conn) == 1

    # The subsequent push must NOT raise ConflictError and must bump to 2.
    second = engine.push(conn, backend)
    assert second.revision == 2
    assert backend.meta["revision"] == "2"
    assert state.get_last_synced_revision(conn) == 2


def test_push_writes_photo_folder_id_into_meta():
    """A push records the backend's Drive photo-folder id in _meta."""
    src = _sample_db()
    fake = FakeBackend()
    fake.photo_folder_id = "folderX"

    engine.push(src, fake)

    assert fake.meta["photo_folder_id"] == "folderX"


def test_pull_ignores_extra_meta_keys():
    """Pull tolerates extra meta keys (e.g. photo_folder_id) without crashing."""
    backend = FakeBackend()
    backend.write_all(serialize.dump_rows(_sample_db()))
    backend.write_meta({"revision": "3", "photo_folder_id": "folderX"})

    dest = db.connect(":memory:")
    db.create_schema(dest)
    result = engine.pull(dest, backend)

    assert result.revision == 3
    assert state.get_last_synced_revision(dest) == 3


def test_resolve_conflict_local_uploads_real_photo(tmp_path, monkeypatch):
    """Keep-local with a real on-disk photo uploads it and lands at cloud+1."""
    images = tmp_path / "images"
    images.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(db, "images_dir", lambda: images)

    photo_bytes = b"\x89PNG\r\n\x1a\n-keep-local-photo-bytes"
    (images / "100_pic.png").write_bytes(photo_bytes)

    src = _sample_db()
    src.execute(
        "UPDATE products SET image_path = 'images/100_pic.png' WHERE id = 100"
    )
    src.commit()

    backend = FakeBackend()
    engine.push(src, backend)  # establishes cloud rev 1 + uploads the photo
    uploads_after_push = backend.upload_count
    assert uploads_after_push >= 1  # the photo was uploaded on the first push

    # Force a diverged cloud revision so a plain push would conflict.
    backend.meta["revision"] = "5"

    result = engine.resolve_conflict(src, backend, "local")

    assert isinstance(result, engine.PushResult)
    assert result.revision == 6  # cloud 5 + 1
    assert backend.meta["revision"] == "6"
    assert state.get_last_synced_revision(src) == 6
    # The photo is tracked in the rebuilt map and present in cloud blob storage.
    assert any(row["product_id"] == 100 for row in backend.read_photo_map())
    assert len(backend.photo_store) >= 1


def _audit_uuids(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT uuid FROM audit_log")}


def test_push_then_pull_carries_audit_log():
    """A push unions the audit log to the cloud; a fresh device's pull absorbs it."""
    src = _sample_db()
    audit.log_change(src, "create", "product", entity_id=100)
    audit.log_change(src, "update", "product", entity_id=100)
    src.commit()
    src_uuids = _audit_uuids(src)
    assert len(src_uuids) == 2

    backend = FakeBackend()
    push_result = engine.push(src, backend)
    assert push_result.audit == 2  # merged cloud audit-log size
    assert len(backend.audit_log) == 2
    # push must NOT mutate the local audit log.
    assert _audit_uuids(src) == src_uuids

    dest = db.connect(":memory:")
    db.create_schema(dest)
    pull_result = engine.pull(dest, backend)

    assert pull_result.audit == 2  # entries absorbed
    assert _audit_uuids(dest) == src_uuids


def test_resolve_conflict_cloud_preserves_local_audit():
    """Keep-cloud replaces the local catalog but preserves the device's audit log."""
    local = _sample_db()
    # The local device has its own audit history.
    audit.log_change(local, "delete", "product", entity_id=100)
    local.commit()
    local_audit = _audit_uuids(local)
    assert len(local_audit) == 1

    backend = FakeBackend()
    # Seed cloud with a DIFFERENT catalog AND a different audit entry at rev 5.
    cloud = db.connect(":memory:")
    db.create_schema(cloud)
    cloud.execute(
        "INSERT INTO brands(id, code, name, has_sheet) VALUES (9, 'ZZ', 'Zed', 1)"
    )
    audit.log_change(cloud, "create", "brand", entity_id=9)
    cloud.commit()
    backend.write_all(serialize.dump_rows(cloud))
    backend.audit_log = audit_sync_rows(cloud)
    backend.write_meta({"revision": "5"})
    cloud_audit = _audit_uuids(cloud)

    result = engine.resolve_conflict(local, backend, "cloud")

    assert isinstance(result, engine.PullResult)
    # Catalog was replaced with the cloud's.
    for table in serialize.SNAPSHOT_TABLES:
        assert _dump(local, table) == _dump(cloud, table), f"mismatch in {table}"
    # KEY GUARANTEE: the device's own audit history survives, plus cloud's absorbed.
    after = _audit_uuids(local)
    assert local_audit <= after  # local history NOT lost
    assert cloud_audit <= after  # cloud history absorbed
    assert after == local_audit | cloud_audit


def test_resolve_conflict_local_unions_audit():
    """Keep-local writes the audit union to the cloud (both sides retained)."""
    src = _sample_db()
    audit.log_change(src, "create", "product", entity_id=100)
    src.commit()
    src_audit = _audit_uuids(src)

    backend = FakeBackend()
    # Cloud already holds another device's audit entry.
    other = db.connect(":memory:")
    db.create_schema(other)
    audit.log_change(other, "update", "brand", entity_id=1)
    other.commit()
    backend.audit_log = audit_sync_rows(other)
    other_audit = _audit_uuids(other)
    backend.write_meta({"revision": "5"})  # conflict state

    result = engine.resolve_conflict(src, backend, "local")

    assert isinstance(result, engine.PushResult)
    cloud_uuids = {r["uuid"] for r in backend.audit_log}
    assert src_audit <= cloud_uuids  # this device's entry pushed
    assert other_audit <= cloud_uuids  # other device's entry retained
    assert result.audit == len(cloud_uuids)


def audit_sync_rows(conn):
    """Cloud-shaped audit rows for a connection (test helper)."""
    from numobel.sync import audit_sync

    return audit_sync.local_audit_rows(conn)
