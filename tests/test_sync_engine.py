"""Tests for the M4 sync orchestration layer (push / pull / conflict).

Exercised entirely offline against the in-memory ``FakeBackend``: no event loop,
no ``google`` imports. A tmp dir is monkeypatched in for ``db.base_dir`` /
``db.images_dir`` so the photo logic has somewhere to look (with no photos the
maps are simply empty).
"""

from __future__ import annotations

import sqlite3

import pytest

from numobel import db
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
