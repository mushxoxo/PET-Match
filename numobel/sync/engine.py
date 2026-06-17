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

This is read-then-write *optimistic concurrency*: it reliably detects
*sequential* divergence — another device pushed since our last sync, leaving the
cloud revision ahead of (or, after a restore-from-backup, behind) our watermark.
It does NOT prevent a true simultaneous-write race: with the current
:class:`~numobel.sync.backend.GoogleBackend` performing a plain (non-conditional)
``write_meta``, two devices that read the same revision at the same instant can
both compute ``cloud+1`` and write it, silently losing one update. The scheme is
therefore advisory — adequate for low-concurrency personal laptop<->phone sync,
not a hard mutual-exclusion guarantee. See
:meth:`~numobel.sync.backend.Backend.write_meta` for the compare-and-swap
precondition this would need to be a hard guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from numobel import db
from numobel.sync import audit_sync, photos, serialize, state
from numobel.sync.errors import ConflictError

#: Marker identifying a spreadsheet as a numobel sync target. Distinct from the
#: snapshot FORMAT used by the .xlsx exporter.
META_FORMAT = "numobel-sync"
META_VERSION = 1


@dataclass
class PushResult:
    """Outcome of a successful push: the new cloud revision + photo-map size.

    ``photos`` is the number of photos tracked in the rebuilt photo map
    (``len(photo_map)``), i.e. the count of products that currently have a local
    photo — NOT necessarily the number newly uploaded this push (unchanged
    photos reuse their existing Drive id without re-uploading).

    ``audit`` is the merged cloud audit-log size (local UNION cloud) after push.
    """

    revision: int
    photos: int
    audit: int = 0


@dataclass
class PullResult:
    """Outcome of a successful pull: cloud revision, restored table counts, photos.

    ``photos`` here IS the actual number of files downloaded this pull (unchanged
    files already present locally are not re-downloaded), unlike
    :attr:`PushResult.photos` which is the map size.

    ``audit`` is the number of audit entries absorbed this pull.
    """

    revision: int
    tables: dict = field(default_factory=dict)
    photos: int = 0
    audit: int = 0


def _now() -> str:
    """Current local time as an ISO-8601 string at second resolution."""
    return datetime.now().isoformat(timespec="seconds")


def _read_revision(backend) -> int:
    """Read the cloud revision from ``_meta``; a fresh/empty sheet is revision 0."""
    return int(backend.read_meta().get("revision", 0) or 0)


def _stage_setting(conn, key: str, value: str) -> None:
    """Stage a settings row on ``conn`` WITHOUT committing.

    Mirrors :func:`db.set_setting`'s upsert, but deliberately omits the commit so
    the caller can flush these rows together with other staged work in a single
    transaction. Used by :func:`pull` to write the revision watermark and pending
    flag atomically with the restored catalog (see Fix 1 in the M4 hardening).
    """
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _do_push(conn, backend, new_rev: int) -> PushResult:
    """Write the catalog, photos and meta at ``new_rev`` (no conflict check).

    Shared body for :func:`push` and the keep-local path of
    :func:`resolve_conflict`. Updates the local watermark and clears the pending
    flag. Transient/auth errors raised by the backend propagate unchanged.
    """
    data = serialize.dump_rows(conn)
    backend.write_all(data)
    photo_map = photos.push_photos(conn, backend)
    audit_n = audit_sync.push_audit(conn, backend)
    backend.write_meta(
        {
            "format": META_FORMAT,
            "version": str(META_VERSION),
            "revision": str(new_rev),
            "last_writer_device": state.get_device_id(conn),
            "photo_folder_id": backend.photo_folder_id or "",
            "updated_at": _now(),
        }
    )
    # The local watermark is written LAST on purpose: cloud writes (sheet/Drive)
    # can never share a transaction with local SQLite, so if any backend write
    # above raised we want last_synced left untouched, leaving us to re-push.
    # Only once the cloud is fully written do we record that we are in sync.
    state.set_last_synced_revision(conn, new_rev)
    state.set_pending(conn, False)
    return PushResult(revision=new_rev, photos=len(photo_map), audit=audit_n)


def push(conn, backend) -> PushResult:
    """Push the local catalog to the cloud, guarding against conflicts.

    Raises :class:`ConflictError` (writing nothing) when the cloud revision no
    longer matches our last-synced revision. Otherwise writes the catalog, the
    photo map and a bumped ``_meta`` revision, then records the new watermark.
    """
    cloud_rev = _read_revision(backend)
    last_synced = state.get_last_synced_revision(conn)
    # `!=` (not `<`) is deliberate: a cloud revision LOWER than our watermark
    # (the sheet was reset or restored from an older backup) is also a divergence
    # we must surface rather than silently overwrite with our newer-looking copy.
    if cloud_rev != last_synced:
        raise ConflictError(local_revision=last_synced, cloud_revision=cloud_rev)
    # Control only reaches here when cloud_rev == last_synced, so the max() is
    # belt-and-suspenders; both operands are equal and we bump by one.
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
    # migrate() above guarantees the uuid column exists before we absorb.
    audit_n = audit_sync.pull_audit(conn, backend)

    # Stage the watermark + pending flag on the SAME connection and let the
    # single commit below flush them together with the audit rows absorbed just
    # above (Fix 1; db.migrate() above already committed the restored catalog).
    # Going through the committing state.* helpers here would split this
    # into multiple transactions: a crash between the catalog commit and the
    # watermark write would leave the local catalog at cloud_rev but last_synced
    # stale, surfacing a spurious ConflictError on the next push. state.KEY_* are
    # the source of truth for the key names.
    _stage_setting(conn, state.KEY_LAST_SYNCED_REVISION, str(int(cloud_rev)))
    _stage_setting(conn, state.KEY_PENDING, "0")
    conn.commit()

    return PullResult(revision=cloud_rev, tables=tables, photos=photos_n, audit=audit_n)


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
