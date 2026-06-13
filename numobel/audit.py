"""Audit logging for every user-initiated write.

A single :func:`log_change` helper inserts one row into ``audit_log``. It does
*not* commit — callers (the functions in :mod:`numobel.mutations`) own the
transaction so the audit row and the change it records commit together.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional


def _now() -> str:
    """Return an ISO-8601 timestamp to second precision."""
    return datetime.now().isoformat(timespec="seconds")


def log_change(
    conn: sqlite3.Connection,
    action: str,
    entity: str,
    entity_id: Optional[int] = None,
    details: Any = None,
) -> None:
    """Record a change in ``audit_log`` (no commit).

    ``details`` may be a string or a JSON-serializable object; objects are
    stored as a compact JSON string.
    """
    if details is not None and not isinstance(details, str):
        details = json.dumps(details, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "INSERT INTO audit_log(ts, action, entity, entity_id, details) "
        "VALUES (?, ?, ?, ?, ?)",
        (_now(), action, entity, entity_id, details),
    )


def get_audit_log(
    conn: sqlite3.Connection, limit: int = 1000
) -> list[sqlite3.Row]:
    """Return audit rows, newest first."""
    return conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
