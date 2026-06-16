"""Tests for the photo diff / upload / download logic (numobel.sync.photos)."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from numobel import db
from numobel.sync import photos
from tests.sync_fakes import FakeBackend


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """An in-memory catalog with base_dir/images_dir isolated to tmp_path."""
    base = tmp_path / "base"
    images = base / "images"
    images.mkdir(parents=True)
    monkeypatch.setattr(db, "base_dir", lambda: base)
    monkeypatch.setattr(db, "images_dir", lambda: images)

    conn = db.connect(":memory:")
    db.create_schema(conn)
    conn.execute("INSERT INTO brands(id, code, name) VALUES (1, 'AT', 'Acoustic')")
    conn.executescript(
        """
        INSERT INTO products(id, brand_id, sku, image_path) VALUES
            (100, 1, 'AT01', 'images/100_a.png'),
            (101, 1, 'AT02', 'images/101_b.png'),
            (102, 1, 'AT03', NULL);
        """
    )
    conn.commit()
    return conn, base, images


def test_local_photos_only_existing_files(catalog):
    conn, base, images = catalog
    bytes_a = b"AAAA-photo-a"
    (images / "100_a.png").write_bytes(bytes_a)
    # 101 references a file that does NOT exist on disk -> skipped.

    result = photos.local_photos(conn)

    assert set(result) == {100}
    assert result[100]["filename"] == "100_a.png"
    assert result[100]["checksum"] == _sha256(bytes_a)
    assert result[100]["path"] == str(images / "100_a.png")


def test_push_photos_uploads_all_then_is_stable(catalog):
    conn, base, images = catalog
    (images / "100_a.png").write_bytes(b"photo-a")
    (images / "101_b.png").write_bytes(b"photo-b")
    backend = FakeBackend()

    first = photos.push_photos(conn, backend)
    assert backend.upload_count == 2
    assert {r["product_id"] for r in first} == {100, 101}
    assert len(backend.photo_map) == 2

    # Second push, nothing changed: no new uploads, map stable.
    second = photos.push_photos(conn, backend)
    assert backend.upload_count == 2  # unchanged
    assert second == first


def test_push_photos_reuploads_only_changed(catalog):
    conn, base, images = catalog
    (images / "100_a.png").write_bytes(b"photo-a")
    (images / "101_b.png").write_bytes(b"photo-b")
    backend = FakeBackend()

    photos.push_photos(conn, backend)
    assert backend.upload_count == 2
    id_100_before = next(r["drive_file_id"] for r in backend.photo_map if r["product_id"] == 100)

    # Change only product 100's bytes.
    (images / "100_a.png").write_bytes(b"photo-a-CHANGED")
    new_map = photos.push_photos(conn, backend)

    assert backend.upload_count == 3  # exactly one more upload
    row_100 = next(r for r in new_map if r["product_id"] == 100)
    row_101 = next(r for r in new_map if r["product_id"] == 101)
    assert row_100["checksum"] == _sha256(b"photo-a-CHANGED")
    assert row_100["drive_file_id"] != id_100_before  # re-minted id
    # 101 untouched: id stable.
    assert row_101["drive_file_id"] == next(
        r["drive_file_id"]
        for r in backend.read_photo_map()
        if r["product_id"] == 101
    )


def test_push_photos_drops_missing_local(catalog):
    conn, base, images = catalog
    (images / "100_a.png").write_bytes(b"photo-a")
    (images / "101_b.png").write_bytes(b"photo-b")
    backend = FakeBackend()
    photos.push_photos(conn, backend)
    assert len(backend.photo_map) == 2

    # Remove 101's local file; next push drops it from the map.
    (images / "101_b.png").unlink()
    new_map = photos.push_photos(conn, backend)
    assert {r["product_id"] for r in new_map} == {100}


def test_pull_photos_downloads_and_repoints(catalog):
    conn, base, images = catalog
    # Start with an empty images dir on the "pulling" device.
    for f in images.iterdir():
        f.unlink()

    backend = FakeBackend()
    bytes_a = b"remote-photo-a"
    bytes_b = b"remote-photo-b"
    backend.photo_store = {"fid-a": bytes_a, "fid-b": bytes_b}
    backend.photo_map = [
        {"product_id": 100, "drive_file_id": "fid-a", "filename": "100_a.png", "checksum": _sha256(bytes_a)},
        {"product_id": 101, "drive_file_id": "fid-b", "filename": "101_b.png", "checksum": _sha256(bytes_b)},
    ]

    count = photos.pull_photos(conn, backend)

    assert count == 2
    assert (images / "100_a.png").read_bytes() == bytes_a
    assert (images / "101_b.png").read_bytes() == bytes_b
    paths = dict(conn.execute("SELECT id, image_path FROM products WHERE id IN (100,101)").fetchall())
    assert paths[100] == "images/100_a.png"
    assert paths[101] == "images/101_b.png"


def test_pull_photos_skips_identical(catalog):
    conn, base, images = catalog
    bytes_a = b"remote-photo-a"
    # Already present + identical.
    (images / "100_a.png").write_bytes(bytes_a)

    backend = FakeBackend()
    backend.photo_store = {"fid-a": bytes_a}
    backend.photo_map = [
        {"product_id": 100, "drive_file_id": "fid-a", "filename": "100_a.png", "checksum": _sha256(bytes_a)},
    ]

    count = photos.pull_photos(conn, backend)

    assert count == 0  # no download, checksum matched
    assert backend.download_count == 0
    # image_path still repointed.
    path = conn.execute("SELECT image_path FROM products WHERE id = 100").fetchone()[0]
    assert path == "images/100_a.png"


def test_pull_photos_does_not_commit(catalog):
    conn, base, images = catalog
    bytes_a = b"remote-photo-a"
    backend = FakeBackend()
    backend.photo_store = {"fid-a": bytes_a}
    # Use a NEW filename so the UPDATE is observable, then rollback to prove
    # pull_photos didn't commit.
    backend.photo_map = [
        {"product_id": 100, "drive_file_id": "fid-a", "filename": "renamed.png", "checksum": _sha256(bytes_a)},
    ]

    photos.pull_photos(conn, backend)
    # Before rollback the UPDATE is visible on this connection.
    assert (
        conn.execute("SELECT image_path FROM products WHERE id = 100").fetchone()[0]
        == "images/renamed.png"
    )

    conn.rollback()  # caller owns the transaction; rollback undoes the UPDATE.
    assert (
        conn.execute("SELECT image_path FROM products WHERE id = 100").fetchone()[0]
        == "images/100_a.png"  # reverted to the original seeded value
    )
