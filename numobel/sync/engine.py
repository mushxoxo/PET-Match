"""Orchestration layer for Google Sheets sync (push / pull / conflict).

Plain, synchronous, Qt-free functions that drive a
:class:`~numobel.sync.backend.Backend` to push the local catalog up, pull the
cloud catalog down, or resolve a divergence between the two. They are callable
directly in tests with no event loop; the Qt threading/timer wrapper lives in a
later milestone.

The cloud spreadsheet carries a monotonically increasing ``revision`` in its
``_meta`` tab. Each device records the revision it last synced
(:func:`numobel.sync.state.get_last_synced_revision`). A push is only safe when
the cloud revision still equals our last-synced revision; otherwise someone else
advanced the sheet and we raise :class:`~numobel.sync.errors.ConflictError`
rather than blindly overwrite their work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from numobel import db
from numobel.sync import photos, serialize, state
from numobel.sync.errors import ConflictError

#: Marker identifying a spreadsheet as a numobel sync target. Distinct from the
#: snapshot FORMAT used by the .xlsx exporter.
META_FORMAT = "numobel-sync"
META_VERSION = 1


@dataclass
class PushResult:
    """Outcome of a successful push: the new cloud revision + photos written."""

    revision: int
    photos: int


@dataclass
class PullResult:
    """Outcome of a successful pull: cloud revision, restored table counts, photos."""

    revision: int
    tables: dict = field(default_factory=dict)
    photos: int = 0


def _now() -> str:
    """Current local time as an ISO-8601 string at second resolution."""
    return datetime.now().isoformat(timespec="seconds")


def _read_revision(backend) -> int:
    """Read the cloud revision from ``_meta``; a fresh/empty sheet is revision 0."""
    return int(backend.read_meta().get("revision", 0) or 0)


def _do_push(conn, backend, new_rev: int) -> PushResult:
    """Write the catalog, photos and meta at ``new_rev`` (no conflict check).

    Shared body for :func:`push` and the keep-local path of
    :func:`resolve_conflict`. Updates the local watermark and clears the pending
    flag. Transient/auth errors raised by the backend propagate unchanged.
    """
    data = serialize.dump_rows(conn)
    backend.write_all(data)
    photo_map = photos.push_photos(conn, backend)
    backend.write_meta(
        {
            "format": META_FORMAT,
            "version": str(META_VERSION),
            "revision": str(new_rev),
            "last_writer_device": state.get_device_id(conn),
            "updated_at": _now(),
        }
    )
    state.set_last_synced_revision(conn, new_rev)
    state.set_pending(conn, False)
    return PushResult(revision=new_rev, photos=len(photo_map))


def push(conn, backend) -> PushResult:
    """Push the local catalog to the cloud, guarding against conflicts.

    Raises :class:`ConflictError` (writing nothing) when the cloud revision no
    longer matches our last-synced revision. Otherwise writes the catalog, the
    photo map and a bumped ``_meta`` revision, then records the new watermark.
    """
    cloud_rev = _read_revision(backend)
    last_synced = state.get_last_synced_revision(conn)
    if cloud_rev != last_synced:
        raise ConflictError(local_revision=last_synced, cloud_revision=cloud_rev)
    new_rev = max(last_synced, cloud_rev) + 1
    return _do_push(conn, backend, new_rev)


def pull(conn, backend) -> PullResult:
    """Replace the local catalog with the cloud one (mirrors ``_import_catalog``).

    Restores the lossless ``_data`` blob into a freshly reset catalog, folds
    resolved color_links into color groups via :func:`db.migrate`, downloads
    changed photos, commits, and records the cloud revision as our watermark.
    """
    cloud_rev = _read_revision(backend)
    data = backend.read_all()

    db.create_schema(conn)
    db.reset_catalog(conn)
    tables = serialize.restore_rows(conn, data)
    db.migrate(conn)
    photos_n = photos.pull_photos(conn, backend)
    conn.commit()

    state.set_last_synced_revision(conn, cloud_rev)
    state.set_pending(conn, False)
    return PullResult(revision=cloud_rev, tables=tables, photos=photos_n)


def resolve_conflict(conn, backend, choice):
    """Resolve a detected conflict by keeping ``"local"`` or ``"cloud"`` edits.

    ``"local"`` overwrites the cloud with our edits, landing strictly above the
    current cloud revision (bypassing the conflict check). ``"cloud"`` discards
    our local edits by pulling. Any other ``choice`` raises ``ValueError``.
    """
    if choice == "local":
        new_rev = _read_revision(backend) + 1
        return _do_push(conn, backend, new_rev)
    if choice == "cloud":
        return pull(conn, backend)
    raise ValueError(f"unknown conflict choice: {choice!r}")
