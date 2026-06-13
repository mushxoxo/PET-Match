"""Read-query layer for the NUMOBEL catalog.

All functions take an open ``sqlite3.Connection`` (with ``sqlite3.Row`` row
factory, as produced by :func:`numobel.db.connect`) as their first argument.
Queries are parameterized; user input is never interpolated into SQL.
"""

from __future__ import annotations

import sqlite3
from difflib import SequenceMatcher
from typing import Optional

# Shared SELECT prefix that joins products to their brand and exposes the
# brand code/name under stable aliases.
_PRODUCT_SELECT = (
    "SELECT products.*, "
    "brands.code AS brand_code, "
    "brands.name AS brand_name "
    "FROM products "
    "JOIN brands ON brands.id = products.brand_id"
)


def list_brands(
    conn: sqlite3.Connection, only_with_sheet: bool = False
) -> list[sqlite3.Row]:
    """Return brands ordered by name then code.

    When ``only_with_sheet`` is true, restrict to brands with ``has_sheet=1``.
    """
    sql = "SELECT * FROM brands"
    params: list = []
    if only_with_sheet:
        sql += " WHERE has_sheet = 1"
    sql += " ORDER BY name, code"
    return conn.execute(sql, params).fetchall()


def _looks_like_brand(
    conn: sqlite3.Connection, query: str
) -> Optional[str]:
    """Return a brand code if ``query`` exactly matches a brand code/name.

    Comparison is case-insensitive. Returns ``None`` when no brand matches.
    """
    q = query.strip()
    if not q:
        return None
    row = conn.execute(
        "SELECT code FROM brands "
        "WHERE lower(code) = lower(?) OR lower(name) = lower(?) "
        "LIMIT 1",
        (q, q),
    ).fetchone()
    return row["code"] if row is not None else None


def _color_match_sql(query: str) -> tuple[str, list]:
    """Build a WHERE fragment + params for a color-style substring match.

    Matches ``query`` as a case-insensitive substring against color_name,
    self_label, and sku.
    """
    like = f"%{query.strip()}%"
    fragment = (
        "(color_name LIKE ? OR self_label LIKE ? OR sku LIKE ?)"
    )
    return fragment, [like, like, like]


def _rank_by_color_similarity(
    rows: list[sqlite3.Row], query: str
) -> list[sqlite3.Row]:
    """Sort rows descending by difflib ratio of query vs color_name (stable)."""
    q = query.strip().lower()
    if not q:
        return rows

    def ratio(row: sqlite3.Row) -> float:
        color = (row["color_name"] or "").lower()
        return SequenceMatcher(None, q, color).ratio()

    # Python's sort is stable, so ties preserve the SQL ordering.
    return sorted(rows, key=ratio, reverse=True)


def search_products(
    conn: sqlite3.Connection,
    query: str,
    scope: str = "all",
    brand_code: Optional[str] = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    """Search products, returning rows joined with brand_code/brand_name.

    ``scope`` is one of ``'all'``, ``'color'``, ``'brand'``.

    * ``brand_code`` (when given) always restricts results to that brand and
      combines with the text query.
    * Empty/whitespace ``query`` returns all products (respecting brand_code)
      ordered by brand_name then color_name.
    * ``scope='color'`` does a substring match on color_name/self_label/sku.
    * ``scope='brand'`` matches products whose brand code or name contains the
      query.
    * ``scope='all'`` first detects whether the query is a brand (exact code/
      name match); if so returns that brand's products, otherwise falls back to
      the color-style substring match.

    Text/color matches are ranked by difflib similarity to color_name.
    """
    q = (query or "").strip()
    where: list[str] = []
    params: list = []
    rank = False

    if brand_code:
        where.append("brands.code = ?")
        params.append(brand_code)

    if not q:
        # Empty query: all products (within brand filter), ordered naturally.
        pass
    elif scope == "color":
        fragment, fparams = _color_match_sql(q)
        where.append(fragment)
        params.extend(fparams)
        rank = True
    elif scope == "brand":
        like = f"%{q}%"
        where.append("(brands.code LIKE ? OR brands.name LIKE ?)")
        params.extend([like, like])
    else:  # scope == 'all' (default)
        detected = _looks_like_brand(conn, q)
        if detected is not None:
            where.append("brands.code = ?")
            params.append(detected)
        else:
            fragment, fparams = _color_match_sql(q)
            where.append(fragment)
            params.extend(fparams)
            rank = True

    sql = _PRODUCT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY brands.name, products.color_name"
    sql += " LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    if rank:
        rows = _rank_by_color_similarity(rows, q)
        # Re-apply limit defensively (SQL already limited, but be safe).
        rows = rows[:limit]

    return rows


def get_product(
    conn: sqlite3.Connection, product_id: int
) -> Optional[sqlite3.Row]:
    """Return a single product joined with brand_code/brand_name, or None."""
    return conn.execute(
        _PRODUCT_SELECT + " WHERE products.id = ?",
        (product_id,),
    ).fetchone()


def _resolved_label(row: sqlite3.Row) -> str:
    """Build 'BRANDCODE sku color_name' label for a resolved product row."""
    parts = [
        row["brand_code"] or "",
        row["sku"] or "",
        row["color_name"] or "",
    ]
    return " ".join(p for p in parts if p).strip()


def get_similar_colors(
    conn: sqlite3.Connection, product_id: int
) -> list[dict]:
    """Return all color_links touching ``product_id`` (bidirectional).

    Each returned dict has keys: ``link_id``, ``status``, ``direction``
    ('out' when this product is the from side, 'in' when the to side),
    ``other_product_id``, ``other_label``, ``to_brand_code``, ``raw_ref``,
    ``normalized``, ``source``.
    """
    rows = conn.execute(
        "SELECT * FROM color_links "
        "WHERE from_product_id = ? OR to_product_id = ? "
        "ORDER BY id",
        (product_id, product_id),
    ).fetchall()

    results: list[dict] = []
    for link in rows:
        is_out = link["from_product_id"] == product_id
        direction = "out" if is_out else "in"

        other_product_id: Optional[int] = None
        other_label: Optional[str] = None

        if is_out:
            # We are the from side; the other end is to_product_id (if resolved)
            # or the raw_ref text otherwise.
            if link["status"] == "resolved" and link["to_product_id"]:
                other_product_id = link["to_product_id"]
                other = get_product(conn, other_product_id)
                if other is not None:
                    other_label = _resolved_label(other)
            else:
                raw = link["raw_ref"] or ""
                other_label = f"{raw} [{link['to_brand_code'] or ''}]"
        else:
            # We are the to side; the other end is the from_product_id.
            other_product_id = link["from_product_id"]
            other = get_product(conn, other_product_id)
            if other is not None:
                other_label = _resolved_label(other)
            elif link["raw_ref"]:
                other_label = f"{link['raw_ref']} [{link['to_brand_code'] or ''}]"

        results.append(
            {
                "link_id": link["id"],
                "status": link["status"],
                "direction": direction,
                "other_product_id": other_product_id,
                "other_label": other_label,
                "to_brand_code": link["to_brand_code"],
                "raw_ref": link["raw_ref"],
                "normalized": link["normalized"],
                "source": link["source"],
            }
        )

    return results


def get_prices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all price rows ordered by seller."""
    return conn.execute("SELECT * FROM prices ORDER BY seller").fetchall()
