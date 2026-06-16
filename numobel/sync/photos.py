"""Photo diff / upload / download logic for Google Sheets sync.

Pure and transport-agnostic: every cloud interaction goes through a
:class:`~numobel.sync.backend.Backend`, so this module imports no ``google``
libraries and is fully testable against a ``FakeBackend``.

The unit of exchange is the *photo map* — one row per product that currently has
a local photo: ``{"product_id", "drive_file_id", "filename", "checksum"}``. The
checksum (sha256 of the file bytes) is what lets both sides diff cheaply and
avoid re-uploading / re-downloading unchanged images.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from numobel import db


def _resolve_image(image_path: str) -> Path:
    """Resolve a stored ``products.image_path`` to an absolute path.

    Mirrors the exporter's rule: an absolute stored path is used as-is;
    otherwise it is taken relative to :func:`db.base_dir`.
    """
    p = Path(image_path)
    return p if p.is_absolute() else db.base_dir() / p


def checksum_file(path) -> str:
    """Return the sha256 hex digest of the file at ``path``."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def local_photos(conn: sqlite3.Connection) -> dict[int, dict]:
    """Map each product with an on-disk photo to its file metadata.

    Returns ``{product_id: {"path": abs_path_str, "filename": basename,
    "checksum": sha256}}`` for every product whose non-empty ``image_path``
    resolves to a file that actually EXISTS. Broken references (file missing)
    are silently skipped.
    """
    result: dict[int, dict] = {}
    rows = conn.execute(
        "SELECT id, image_path FROM products "
        "WHERE image_path IS NOT NULL AND image_path != ''"
    ).fetchall()
    for row in rows:
        image_path = row["image_path"]
        resolved = _resolve_image(image_path)
        if not resolved.is_file():
            continue  # broken reference — skip
        result[row["id"]] = {
            "path": str(resolved),
            "filename": Path(image_path).name,
            "checksum": checksum_file(resolved),
        }
    return result


def push_photos(conn: sqlite3.Connection, backend) -> list[dict]:
    """Upload changed local photos and write a fresh photo map.

    Diffs the current local photos against the backend's existing photo map by
    checksum: any local photo that is new, or whose checksum changed, is
    uploaded via ``backend.upload_photo`` to obtain a (possibly new) file id;
    unchanged photos reuse their existing id. The rebuilt map has exactly one
    row per current local photo. The new map is written back via
    ``backend.write_photo_map`` and returned.

    Photos in the OLD map whose product no longer has a local file are simply
    omitted from the new map.
    """
    # Index the existing map by product_id for quick checksum/id lookup.
    old_by_product = {
        int(row["product_id"]): row for row in backend.read_photo_map()
    }
    locals_ = local_photos(conn)

    new_map: list[dict] = []
    for product_id, info in locals_.items():
        old = old_by_product.get(product_id)
        if old is not None and old.get("checksum") == info["checksum"]:
            # Unchanged: keep the existing drive file id, no upload.
            drive_file_id = old["drive_file_id"]
        else:
            # New or changed: upload and take the (re)minted id.
            drive_file_id = backend.upload_photo(info["path"], info["filename"])
        new_map.append(
            {
                "product_id": product_id,
                "drive_file_id": drive_file_id,
                "filename": info["filename"],
                "checksum": info["checksum"],
            }
        )

    # TODO(M-later): photos dropped from the map (product lost its local file)
    # are only omitted here, not deleted from Drive. Add remote cleanup once we
    # can safely confirm no other device still references the file.
    backend.write_photo_map(new_map)
    return new_map


def pull_photos(conn: sqlite3.Connection, backend, photo_map=None) -> int:
    """Download missing/changed photos and repoint products at them.

    For each row in the photo map (read from the backend when ``photo_map`` is
    ``None``): the intended local path is ``db.images_dir()/filename``. If that
    file is missing OR its checksum differs from the row's, it is downloaded via
    ``backend.download_photo``. Either way, ``products.image_path`` for that
    product is set to ``"images/<filename>"``.

    Returns the number of files actually downloaded. The UPDATEs are executed on
    ``conn`` but NOT committed — the caller owns the transaction.
    """
    if photo_map is None:
        photo_map = backend.read_photo_map()

    images_dir = db.images_dir()
    images_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for row in photo_map:
        filename = row["filename"]
        dest = images_dir / filename
        needs_download = (
            not dest.is_file()
            or checksum_file(dest) != row.get("checksum")
        )
        if needs_download:
            backend.download_photo(row["drive_file_id"], str(dest))
            downloaded += 1
        conn.execute(
            "UPDATE products SET image_path = ? WHERE id = ?",
            (f"images/{filename}", int(row["product_id"])),
        )
    return downloaded
