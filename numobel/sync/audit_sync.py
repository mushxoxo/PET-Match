"""Audit-log merge logic for Google Sheets sync.

Pure and transport-agnostic: every cloud interaction goes through a
:class:`~numobel.sync.backend.Backend` (its ``read_audit_log`` /
``write_audit_log`` over the hidden ``_audit`` tab), so this module imports no
``google`` libraries and is fully testable against a ``FakeBackend``.

Unlike the catalog (which syncs by last-writer-wins full-replace), the audit log
is append-only per-device history and must be UNIONED across devices so NO
device ever loses history. The cross-device identity of an entry is its
``uuid`` (minted by :func:`numobel.audit.log_change`); the device-local ``id``
is intentionally NOT synced.

Convention, mirroring the photos side-channel:

* **push** (local -> cloud): write the UNION of local + cloud audit entries back
  to the cloud, so a push never clobbers another device's entries. push does
  NOT mutate the local audit log.
* **pull** (cloud -> local): INSERT any cloud entry whose ``uuid`` isn't already
  local; NEVER delete local entries (so "keep cloud" on a catalog conflict
  discards the local catalog but preserves local audit history).

No-commit discipline (like :func:`numobel.sync.photos.pull_photos`):
``_absorb`` / :func:`pull_audit` execute INSERTs on ``conn`` but do NOT commit —
the engine's pull owns the final commit. :func:`push_audit` only writes to the
cloud and reads local; it neither inserts nor commits locally.
"""

from __future__ import annotations

import sqlite3


def local_audit_rows(conn: sqlite3.Connection) -> list[dict]:
    """Return all local audit_log rows as sync-shaped dicts.

    Excludes the device-local ``id``; rows are keyed for merge by ``uuid``.
    ``entity_id`` stays ``int|None`` and ``details`` stays ``str|None``.
    """
    rows = conn.execute(
        "SELECT uuid, ts, action, entity, entity_id, details "
        "FROM audit_log ORDER BY id"
    ).fetchall()
    return [
        {
            "uuid": r["uuid"],
            "ts": r["ts"],
            "action": r["action"],
            "entity": r["entity"],
            "entity_id": r["entity_id"],
            "details": r["details"],
        }
        for r in rows
    ]


def _local_uuids(conn: sqlite3.Connection) -> set:
    """uuids already present locally."""
    return {
        row["uuid"]
        for row in conn.execute("SELECT uuid FROM audit_log").fetchall()
    }


def _absorb(conn: sqlite3.Connection, rows) -> int:
    """INSERT each row whose uuid isn't already local. No commit.

    The caller owns the transaction. Returns the number inserted. Rows with a
    blank/None uuid are skipped (defensive — uuid is the identity).
    """
    existing = _local_uuids(conn)
    count = 0
    for r in rows:
        uid = r.get("uuid")
        if not uid or uid in existing:
            continue
        conn.execute(
            "INSERT INTO audit_log(uuid, ts, action, entity, entity_id, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                uid,
                r.get("ts"),
                r.get("action"),
                r.get("entity"),
                r.get("entity_id"),
                r.get("details"),
            ),
        )
        existing.add(uid)
        count += 1
    return count


def push_audit(conn: sqlite3.Connection, backend) -> int:
    """Union local audit entries with the cloud's and write the union to the cloud.

    Returns the size of the merged cloud audit log. Does not mutate or commit the
    local DB. Rows with a blank/None uuid are ignored (uuid is the identity).
    """
    cloud = backend.read_audit_log()
    local = local_audit_rows(conn)

    by_uuid: dict[str, dict] = {}
    # Cloud first, then local (local wins ties — identical anyway).
    for row in cloud:
        uid = row.get("uuid")
        if not uid:
            continue
        by_uuid[uid] = row
    for row in local:
        uid = row.get("uuid")
        if not uid:
            continue
        by_uuid[uid] = row

    merged = list(by_uuid.values())
    backend.write_audit_log(merged)
    return len(merged)


def pull_audit(conn: sqlite3.Connection, backend) -> int:
    """Absorb cloud audit entries into the local log (insert uuids not present).

    Never deletes local rows. Returns the number absorbed. Does not commit — the
    caller owns the transaction.
    """
    return _absorb(conn, backend.read_audit_log())
