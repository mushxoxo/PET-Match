"""Single source of truth for catalog table dump/restore.

Both the ``.xlsx`` snapshot exporter and importer (and the future Google Sheets
sync) serialize the same set of tables through this module so the on-disk and
on-the-wire shapes never drift apart.

The dump path takes sqlite values verbatim. The restore path coerces each cell
to its destination column's SQLite type affinity, because values may arrive as
proper Python types (openpyxl returns ints/floats/None) or as bare strings
(Google Sheets coerces everything to text and loses the NULL-vs-empty
distinction).
"""

from __future__ import annotations

import sqlite3

#: Catalog tables dumped/restored in foreign-key-safe order — the full-replace
#: set shared by Google sync and the ``.xlsx`` snapshot. ``settings`` is
#: machine-local and never synced. ``audit_log`` is excluded here too, but it IS
#: synced — via a SEPARATE append-only merge channel (see
#: :mod:`numobel.sync.audit_sync`), not this full-replace set — and is added back
#: to the snapshot via :data:`EXPORT_TABLES`.
SNAPSHOT_TABLES = ("brands", "color_groups", "products", "color_links", "prices")

#: Restore order alias — a separate public name so callers (snapshot.py) can
#: re-export it without depending on the dump-side spelling.
RESTORE_ORDER = SNAPSHOT_TABLES

#: Tables carried by the shareable ``.xlsx`` snapshot (export/import round-trip).
#: A superset of :data:`SNAPSHOT_TABLES`: the snapshot is a full database dump
#: for backup/sharing, so it also carries ``audit_log`` as plain rows. (Google
#: sync also syncs ``audit_log``, but via the separate append-only merge channel
#: in :mod:`numobel.sync.audit_sync`, not as part of this full-replace set.)
#: ``audit_log`` has no foreign keys, so it is safe to restore last.
EXPORT_TABLES = (*SNAPSHOT_TABLES, "audit_log")
EXPORT_RESTORE_ORDER = (*RESTORE_ORDER, "audit_log")


def dump_table(conn: sqlite3.Connection, table: str) -> dict:
    """Dump one table as ``{"columns": [...], "rows": [[cell, ...], ...]}``.

    Columns come from the cursor description (schema order); rows are plain
    lists of cell values taken verbatim from sqlite (no coercion on dump).
    """
    cur = conn.execute(f"SELECT * FROM {table}")
    columns = [d[0] for d in cur.description]
    rows = [list(row) for row in cur.fetchall()]
    return {"columns": columns, "rows": rows}


def dump_rows(conn: sqlite3.Connection) -> dict:
    """Dump every snapshot table as ``{table: {"columns", "rows"}}``."""
    return {table: dump_table(conn, table) for table in SNAPSHOT_TABLES}


def _affinity(declared_type: str) -> str:
    """Reduce a declared column type to ``INTEGER`` / ``REAL`` / ``TEXT``.

    Follows the SQLite affinity rules sufficient for our schema: a type
    containing ``INT`` is INTEGER; one containing ``REAL``/``FLOA``/``DOUB`` is
    REAL; everything else is treated as TEXT (value kept as-is).
    """
    t = (declared_type or "").upper()
    if "INT" in t:
        return "INTEGER"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "REAL"
    return "TEXT"


def _table_affinities(conn: sqlite3.Connection, table: str) -> dict:
    """Map each column of ``table`` to its affinity (derived once per restore)."""
    return {
        row[1]: _affinity(row[2])
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def _coerce(value, affinity: str):
    """Coerce one cell to its destination column affinity.

    ``None`` and blank strings become ``None``; INTEGER/REAL columns parse from
    Python numbers or numeric strings; TEXT (and unknown) columns keep the value
    untouched so JSON strings round-trip byte-for-byte.
    """
    if value is None:
        return None
    if value == "":  # a blank cell is SQL NULL for every affinity
        return None
    if affinity == "INTEGER":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            # Only narrow to int when genuinely integral; a non-integral float
            # (5.5) passes through so SQLite stores it as REAL rather than us
            # silently truncating it to 5.
            return int(value) if value.is_integer() else value
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                # Numeric but not a bare int string (e.g. "5.0" / "5.5"). Keep
                # integral values as int; preserve non-integral as float rather
                # than truncating. Unparseable values fall through unchanged.
                parsed = float(value)
            except (TypeError, ValueError):
                return value  # unparseable — leave as-is rather than crash
            return int(parsed) if parsed.is_integer() else parsed
    if affinity == "REAL":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value  # TEXT / unknown — keep as-is


def restore_table(
    conn: sqlite3.Connection, table: str, columns, rows
) -> int:
    """Insert ``rows`` into ``table``, coercing cells to column affinity.

    Returns the number of rows inserted. Does not commit and does not clear the
    table — the caller controls the transaction and any prior reset.
    """
    columns = list(columns)
    affinities = _table_affinities(conn, table)
    col_affinity = [affinities.get(c, "TEXT") for c in columns]
    placeholders = ",".join("?" * len(columns))
    # Identifier interpolation is safe here: ``table`` comes from the fixed
    # internal SNAPSHOT_TABLES allowlist and column names originate from trusted
    # dump/schema sources (cursor.description / PRAGMA table_info). A future
    # caller passing externally-derived column names would need to validate them.
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"

    count = 0
    for row in rows:
        coerced = [_coerce(cell, aff) for cell, aff in zip(row, col_affinity)]
        conn.execute(sql, coerced)
        count += 1
    return count


def restore_rows(conn: sqlite3.Connection, data: dict) -> dict:
    """Restore each table present in ``data`` (the :func:`dump_rows` shape).

    Tables are restored in :data:`RESTORE_ORDER`; returns ``{table: inserted}``.
    Does not commit or reset — the caller owns the transaction.
    """
    counts = {}
    for table in RESTORE_ORDER:
        if table not in data:
            continue
        block = data[table]
        counts[table] = restore_table(
            conn, table, block["columns"], block["rows"]
        )
    return counts
