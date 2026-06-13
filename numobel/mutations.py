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
# Brands & products
# --------------------------------------------------------------------------- #
def add_brand(
    conn: sqlite3.Connection,
    code: str,
    name: Optional[str] = None,
    has_sheet: bool = False,
) -> int:
    """Create a new brand and return its id.

    ``code`` is required and must be unique (case-insensitive). ``has_sheet``
    marks whether the brand has a source price sheet (affects the brand filter).
    """
    code = (code or "").strip()
    if not code:
        raise MutationError("A brand code is required.")
    clash = conn.execute(
        "SELECT id FROM brands WHERE lower(code) = lower(?)", (code,)
    ).fetchone()
    if clash is not None:
        raise MutationError(f"A brand with code {code!r} already exists.")

    cur = conn.execute(
        "INSERT INTO brands(code, name, has_sheet) VALUES (?, ?, ?)",
        (code, (name or "").strip() or None, 1 if has_sheet else 0),
    )
    brand_id = int(cur.lastrowid)
    audit.log_change(
        conn,
        action="add_brand",
        entity="brand",
        entity_id=brand_id,
        details={"code": code, "name": name, "has_sheet": bool(has_sheet)},
    )
    conn.commit()
    return brand_id


def add_product(
    conn: sqlite3.Connection,
    brand_id: int,
    sku: Optional[str] = None,
    color_name: Optional[str] = None,
    shade_no: Optional[str] = None,
    thickness: Optional[float] = None,
    self_label: Optional[str] = None,
    category: Optional[str] = None,
) -> int:
    """Create a new product under ``brand_id`` and return its id.

    Requires the brand to exist and at least one of SKU/color name. Enforces
    the per-brand SKU uniqueness the schema already guarantees, with a friendly
    error instead of an IntegrityError.
    """
    brand = conn.execute(
        "SELECT id, code FROM brands WHERE id = ?", (brand_id,)
    ).fetchone()
    if brand is None:
        raise MutationError(f"No brand with id {brand_id}")

    sku = (sku or "").strip() or None
    color_name = (color_name or "").strip() or None
    if not sku and not color_name:
        raise MutationError("A product needs at least a SKU or a color name.")
    if sku is not None:
        dup = conn.execute(
            "SELECT id FROM products WHERE brand_id = ? AND sku = ?",
            (brand_id, sku),
        ).fetchone()
        if dup is not None:
            raise MutationError(
                f"{brand['code']} already has a product with SKU {sku!r}."
            )

    cur = conn.execute(
        "INSERT INTO products("
        "brand_id, sku, shade_no, color_name, thickness, self_label, "
        "category, source_sheet) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '(manual)')",
        (
            brand_id,
            sku,
            (shade_no or "").strip() or None,
            color_name,
            thickness,
            (self_label or "").strip() or None,
            (category or "").strip() or None,
        ),
    )
    product_id = int(cur.lastrowid)
    audit.log_change(
        conn,
        action="add_product",
        entity="product",
        entity_id=product_id,
        details={"brand_id": brand_id, "sku": sku, "color_name": color_name},
    )
    conn.commit()
    return product_id


def update_product(
    conn: sqlite3.Connection,
    product_id: int,
    *,
    brand_id: Optional[int] = None,
    sku: Optional[str] = None,
    color_name: Optional[str] = None,
    shade_no: Optional[str] = None,
    thickness: Optional[float] = None,
    category: Optional[str] = None,
    self_label: Optional[str] = None,
) -> None:
    """Update a product's editable fields (and optionally reassign its brand).

    The edit form always submits the full field set, so every keyword is
    applied. Empty strings normalize to ``NULL``; ``thickness`` <= 0 becomes
    ``NULL``. Validates the target brand exists, the ``(brand, sku)`` pair stays
    unique (excluding this product), and at least one of SKU/color name is set.
    Logs the old->new diff and commits atomically.
    """
    old = _require_product(conn, product_id)

    target_brand_id = old["brand_id"] if brand_id is None else int(brand_id)
    brand = conn.execute(
        "SELECT id, code FROM brands WHERE id = ?", (target_brand_id,)
    ).fetchone()
    if brand is None:
        raise MutationError(f"No brand with id {target_brand_id}")

    sku = (sku or "").strip() or None
    color_name = (color_name or "").strip() or None
    shade_no = (shade_no or "").strip() or None
    category = (category or "").strip() or None
    self_label = (self_label or "").strip() or None
    if thickness is not None:
        thickness = float(thickness)
        if thickness <= 0:
            thickness = None

    if not sku and not color_name:
        raise MutationError("A product needs at least a SKU or a color name.")

    if sku is not None:
        dup = conn.execute(
            "SELECT id FROM products WHERE brand_id = ? AND sku = ? AND id <> ?",
            (target_brand_id, sku, product_id),
        ).fetchone()
        if dup is not None:
            raise MutationError(
                f"{brand['code']} already has a product with SKU {sku!r}."
            )

    new_values = {
        "brand_id": target_brand_id,
        "sku": sku,
        "color_name": color_name,
        "shade_no": shade_no,
        "thickness": thickness,
        "category": category,
        "self_label": self_label,
    }
    conn.execute(
        "UPDATE products SET brand_id=?, sku=?, color_name=?, shade_no=?, "
        "thickness=?, category=?, self_label=? WHERE id=?",
        (
            target_brand_id, sku, color_name, shade_no, thickness,
            category, self_label, product_id,
        ),
    )
    changes = {
        key: {"old": old[key], "new": value}
        for key, value in new_values.items()
        if old[key] != value
    }
    audit.log_change(
        conn,
        action="update_product",
        entity="product",
        entity_id=product_id,
        details={"changes": changes},
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Color families (similar-color equivalence classes)
# --------------------------------------------------------------------------- #
def _group_of(conn: sqlite3.Connection, product_id: int) -> Optional[int]:
    """Return a product's color_group_id (None when ungrouped)."""
    return _require_product(conn, product_id)["color_group_id"]


def _ensure_same_family(
    conn: sqlite3.Connection, anchor_id: int, member_id: int
) -> int:
    """Put both products in one color group; return that group's id.

    Creates a group, joins one to the other's group, or merges two groups as
    needed. Idempotent: if they already share a group it is a no-op. Does NOT
    commit or audit — callers own that.
    """
    ga = _group_of(conn, anchor_id)
    gb = _group_of(conn, member_id)

    if ga is not None and ga == gb:
        return ga
    if ga is None and gb is None:
        gid = int(
            conn.execute(
                "INSERT INTO color_groups(created_at) VALUES (?)", (_now(),)
            ).lastrowid
        )
        conn.execute(
            "UPDATE products SET color_group_id = ? WHERE id IN (?, ?)",
            (gid, anchor_id, member_id),
        )
        return gid
    if ga is not None and gb is None:
        conn.execute(
            "UPDATE products SET color_group_id = ? WHERE id = ?",
            (ga, member_id),
        )
        return ga
    if ga is None and gb is not None:
        conn.execute(
            "UPDATE products SET color_group_id = ? WHERE id = ?",
            (gb, anchor_id),
        )
        return gb
    # Both grouped, different groups: merge gb into ga.
    conn.execute(
        "UPDATE products SET color_group_id = ? WHERE color_group_id = ?",
        (ga, gb),
    )
    conn.execute("DELETE FROM color_groups WHERE id = ?", (gb,))
    return ga


def add_to_family(
    conn: sqlite3.Connection, anchor_id: int, member_id: int
) -> int:
    """Mark two products as similar colors (same family). Returns the group id.

    Rejects self-links and a product already in the anchor's family.
    """
    if anchor_id == member_id:
        raise MutationError("A product cannot be similar to itself.")
    _require_product(conn, anchor_id)
    _require_product(conn, member_id)

    ga = _group_of(conn, anchor_id)
    if ga is not None and ga == _group_of(conn, member_id):
        raise MutationError("These colors are already in the same family.")

    gid = _ensure_same_family(conn, anchor_id, member_id)
    audit.log_change(
        conn,
        action="add_to_family",
        entity="product",
        entity_id=member_id,
        details={"anchor_id": anchor_id, "member_id": member_id, "group_id": gid},
    )
    conn.commit()
    return gid


def remove_from_family(conn: sqlite3.Connection, product_id: int) -> None:
    """Detach a product from its color family entirely.

    The product loses its group membership. If that leaves the group with one
    or zero members, the group is dissolved so a "family" never has a lone
    member.
    """
    gid = _group_of(conn, product_id)
    if gid is None:
        raise MutationError("This color is not part of a family.")

    conn.execute(
        "UPDATE products SET color_group_id = NULL WHERE id = ?", (product_id,)
    )
    remaining = conn.execute(
        "SELECT id FROM products WHERE color_group_id = ?", (gid,)
    ).fetchall()
    if len(remaining) <= 1:
        conn.execute(
            "UPDATE products SET color_group_id = NULL WHERE color_group_id = ?",
            (gid,),
        )
        conn.execute("DELETE FROM color_groups WHERE id = ?", (gid,))

    audit.log_change(
        conn,
        action="remove_from_family",
        entity="product",
        entity_id=product_id,
        details={"group_id": gid, "dissolved": len(remaining) <= 1},
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Pending references (unresolved / external import links)
# --------------------------------------------------------------------------- #
def remove_reference(conn: sqlite3.Connection, link_id: int) -> None:
    """Delete a pending (unresolved/external) color reference by id."""
    row = conn.execute(
        "SELECT * FROM color_links WHERE id = ?", (link_id,)
    ).fetchone()
    if row is None:
        raise MutationError(f"No color reference with id {link_id}")
    conn.execute("DELETE FROM color_links WHERE id = ?", (link_id,))
    audit.log_change(
        conn,
        action="remove_reference",
        entity="color_link",
        entity_id=link_id,
        details={
            "from_product_id": row["from_product_id"],
            "raw_ref": row["raw_ref"],
            "status": row["status"],
        },
    )
    conn.commit()


def resolve_reference(
    conn: sqlite3.Connection, link_id: int, to_product_id: int
) -> int:
    """Resolve a pending reference to a real product, adding it to the family.

    The referencing product and ``to_product_id`` are placed in one color
    family and the now-redundant pending reference row is deleted. Returns the
    resulting group id. ``raw_ref`` provenance is preserved in the audit log.
    """
    link = conn.execute(
        "SELECT * FROM color_links WHERE id = ?", (link_id,)
    ).fetchone()
    if link is None:
        raise MutationError(f"No color reference with id {link_id}")
    from_pid = link["from_product_id"]
    _require_product(conn, to_product_id)
    if from_pid == to_product_id:
        raise MutationError("A product cannot be similar to itself.")

    gid = _ensure_same_family(conn, from_pid, to_product_id)
    conn.execute("DELETE FROM color_links WHERE id = ?", (link_id,))
    audit.log_change(
        conn,
        action="resolve_reference",
        entity="color_link",
        entity_id=link_id,
        details={
            "from_product_id": from_pid,
            "to_product_id": to_product_id,
            "raw_ref": link["raw_ref"],
            "previous_status": link["status"],
            "group_id": gid,
        },
    )
    conn.commit()
    return gid


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
