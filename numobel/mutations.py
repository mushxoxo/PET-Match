"""User-initiated writes for the NUMOBEL catalog.

Every function here is the *only* sanctioned way to mutate catalog data from
the UI. Each one logs to ``audit_log`` via :mod:`numobel.audit` and commits the
change and its audit row together, so the log can never drift from the data.

All functions take an open ``sqlite3.Connection`` (with ``sqlite3.Row`` row
factory, as produced by :func:`numobel.db.connect`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from numobel import audit, search


class MutationError(ValueError):
    """Raised when a requested write is invalid (bad id, self-link, etc.)."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _require_product(conn: sqlite3.Connection, product_id: int) -> sqlite3.Row:
    row = search.get_product(conn, product_id)
    if row is None:
        raise MutationError(f"No product with id {product_id}")
    return row


# --------------------------------------------------------------------------- #
# Photos
# --------------------------------------------------------------------------- #
def set_product_image(
    conn: sqlite3.Connection, product_id: int, image_path: Optional[str]
) -> None:
    """Set (or clear, with ``None``) a product's image path."""
    _require_product(conn, product_id)
    conn.execute(
        "UPDATE products SET image_path = ? WHERE id = ?",
        (image_path, product_id),
    )
    audit.log_change(
        conn,
        action="clear_image" if image_path is None else "set_image",
        entity="product",
        entity_id=product_id,
        details={"image_path": image_path},
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Color links (similar-color mappings)
# --------------------------------------------------------------------------- #
def add_link(
    conn: sqlite3.Connection,
    from_product_id: int,
    to_product_id: int,
    note: Optional[str] = None,
) -> int:
    """Create a resolved, user-authored link between two products.

    Returns the new link id. Rejects self-links and duplicates of an existing
    link in either direction.
    """
    if from_product_id == to_product_id:
        raise MutationError("A product cannot be linked to itself.")
    _require_product(conn, from_product_id)
    to_row = _require_product(conn, to_product_id)

    existing = conn.execute(
        "SELECT id FROM color_links "
        "WHERE (from_product_id = ? AND to_product_id = ?) "
        "   OR (from_product_id = ? AND to_product_id = ?) "
        "LIMIT 1",
        (from_product_id, to_product_id, to_product_id, from_product_id),
    ).fetchone()
    if existing is not None:
        raise MutationError("These products are already linked.")

    cur = conn.execute(
        "INSERT INTO color_links("
        "from_product_id, to_product_id, to_brand_code, raw_ref, normalized, "
        "status, source, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'resolved', 'user', ?, ?)",
        (
            from_product_id,
            to_product_id,
            to_row["brand_code"],
            None,
            None,
            note,
            _now(),
        ),
    )
    link_id = int(cur.lastrowid)
    audit.log_change(
        conn,
        action="add_link",
        entity="color_link",
        entity_id=link_id,
        details={
            "from_product_id": from_product_id,
            "to_product_id": to_product_id,
            "note": note,
        },
    )
    conn.commit()
    return link_id


def remove_link(conn: sqlite3.Connection, link_id: int) -> None:
    """Delete a color link by id."""
    row = conn.execute(
        "SELECT * FROM color_links WHERE id = ?", (link_id,)
    ).fetchone()
    if row is None:
        raise MutationError(f"No color link with id {link_id}")
    conn.execute("DELETE FROM color_links WHERE id = ?", (link_id,))
    audit.log_change(
        conn,
        action="remove_link",
        entity="color_link",
        entity_id=link_id,
        details={
            "from_product_id": row["from_product_id"],
            "to_product_id": row["to_product_id"],
            "raw_ref": row["raw_ref"],
            "status": row["status"],
        },
    )
    conn.commit()


def resolve_link(
    conn: sqlite3.Connection, link_id: int, to_product_id: int
) -> None:
    """Point an unresolved/external link at a real product.

    Marks the link ``resolved`` and ``source='user'`` while preserving its
    original ``raw_ref`` for provenance.
    """
    link = conn.execute(
        "SELECT * FROM color_links WHERE id = ?", (link_id,)
    ).fetchone()
    if link is None:
        raise MutationError(f"No color link with id {link_id}")
    to_row = _require_product(conn, to_product_id)
    if link["from_product_id"] == to_product_id:
        raise MutationError("A product cannot be linked to itself.")

    conn.execute(
        "UPDATE color_links "
        "SET to_product_id = ?, to_brand_code = ?, status = 'resolved', "
        "    source = 'user' "
        "WHERE id = ?",
        (to_product_id, to_row["brand_code"], link_id),
    )
    audit.log_change(
        conn,
        action="resolve_link",
        entity="color_link",
        entity_id=link_id,
        details={
            "to_product_id": to_product_id,
            "raw_ref": link["raw_ref"],
            "previous_status": link["status"],
        },
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #
# Columns a user may edit on a price row (seller plus the numeric fields).
PRICE_FIELDS = (
    "seller",
    "mrp",
    "mrp_sft",
    "dp",
    "dp_sft",
    "profit",
    "discount",
    "cust_price",
    "cust_price_sft",
)


def update_price_field(
    conn: sqlite3.Connection, price_id: int, field: str, value
) -> None:
    """Update a single editable field on a price row.

    ``field`` must be one of :data:`PRICE_FIELDS`. ``value`` is stored as-is
    (the caller is responsible for coercing numeric fields to float/None).
    """
    if field not in PRICE_FIELDS:
        raise MutationError(f"{field!r} is not an editable price field.")
    old = conn.execute(
        "SELECT * FROM prices WHERE id = ?", (price_id,)
    ).fetchone()
    if old is None:
        raise MutationError(f"No price row with id {price_id}")

    conn.execute(
        f"UPDATE prices SET {field} = ? WHERE id = ?", (value, price_id)
    )
    audit.log_change(
        conn,
        action="update_price",
        entity="price",
        entity_id=price_id,
        details={"field": field, "old": old[field], "new": value},
    )
    conn.commit()
