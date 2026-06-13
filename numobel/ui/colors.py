"""Derive a swatch color from a product's name (or its color family).

Dependency-free: a curated table maps common color words to hex. The public
entry point is :func:`swatch_color`, which resolves name -> family-average ->
neutral grey. Presentational (only ``QColor`` from Qt) and importable without a
running ``QApplication``.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Optional

from PySide6.QtGui import QColor

from numobel import search

# Curated color-word table. Keys are lowercase single words; values are #rrggbb.
_COLOR_WORDS: dict[str, str] = {
    # primaries / secondaries
    "red": "#c0392b", "green": "#27ae60", "blue": "#2e6fb0",
    "yellow": "#e6c34a", "orange": "#e0782c", "purple": "#7d5ba6",
    "violet": "#7d5ba6", "pink": "#e08aa8", "brown": "#7a5230",
    "black": "#222222", "white": "#f4f1ea", "grey": "#9a9a9a",
    "gray": "#9a9a9a", "silver": "#c9cdd2", "gold": "#caa14a",
    # neutrals
    "ivory": "#efe9da", "cream": "#efe7d3", "beige": "#dcc9a6",
    "tan": "#cda878", "taupe": "#b8a894", "charcoal": "#4a4a4a",
    "slate": "#5b6770", "ash": "#b2aaa0", "smoke": "#a7a39c",
    "stone": "#a99f92", "sand": "#d8c39a", "pearl": "#eae6dc",
    "snow": "#f5f4f0", "chalk": "#e8e4d8", "graphite": "#3c3c3c",
    "espresso": "#3a2a20", "mocha": "#6f5240", "coffee": "#5a4434",
    # earth / wood / metal
    "walnut": "#5a3c28", "oak": "#b08552", "teak": "#9a6b3f",
    "mahogany": "#5c3326", "maple": "#c8a062", "cedar": "#7a4a2b",
    "rust": "#a8542b", "terracotta": "#c8714e", "brick": "#9c4a36",
    "clay": "#b06a4c", "copper": "#b5713c", "bronze": "#8c6b3f",
    "amber": "#cf9b3f", "honey": "#cf9b3f",
    # cool tones
    "navy": "#2a3a5c", "teal": "#2f8f8a", "aqua": "#4fb0b8",
    "cyan": "#3fb0c0", "turquoise": "#40b5a8", "sky": "#7fb4d8",
    "ocean": "#2f6f93", "sea": "#3f8f93", "mint": "#9bd3b0",
    "sage": "#9aa982", "olive": "#7f7f44", "forest": "#2f6b3f",
    "emerald": "#2f9e6b", "lime": "#9ac23f", "moss": "#6f7a3f",
    # warm / accent
    "crimson": "#b02233", "scarlet": "#c0392b", "wine": "#722f37",
    "burgundy": "#6e2a33", "maroon": "#6b2a2a", "ruby": "#9b1c31",
    "coral": "#e0795f", "salmon": "#e29078", "peach": "#e8b48f",
    "apricot": "#e0a060", "rose": "#d98a98", "blush": "#e7b8bd",
    "magenta": "#b0407f", "fuchsia": "#bf3f8f", "plum": "#7a4a6b",
    "lavender": "#b6a7d6", "lilac": "#b9a3d0", "indigo": "#3f3f7a",
    "mustard": "#cba135", "lemon": "#e6d24a", "champagne": "#e6d6b0",
    "khaki": "#b3a571", "camel": "#c19a6b", "chocolate": "#4a2f23",
    "midnight": "#262b3a", "denim": "#3f5d80", "steel": "#7c8893",
    "pewter": "#8a8d90", "platinum": "#d6d4cc", "onyx": "#2a2a2a",
}

#: Final fallback when nothing resolves.
NEUTRAL_GREY = QColor(154, 154, 154)  # #9a9a9a

_WORD_RE = re.compile(r"[a-z]+")


def resolve_name_color(name: Optional[str]) -> Optional[QColor]:
    """Return the color implied by ``name``, or ``None`` when no word matches.

    Tokenizes ``name`` into lowercase alphabetic words and matches them against
    the curated table. When several words match, the *last* one wins, since
    English color phrasing puts the head color word last ("Blizzard Grey").
    """
    if not name:
        return None
    found_hex: Optional[str] = None
    for word in _WORD_RE.findall(name.lower()):
        if word in _COLOR_WORDS:
            found_hex = _COLOR_WORDS[word]
    return QColor(found_hex) if found_hex is not None else None


def family_average_color(
    conn: sqlite3.Connection, product_id: int
) -> Optional[QColor]:
    """Average the resolvable colors of a product's color family.

    Returns ``None`` when the product has no family or no family member
    resolves to a known color.

    The ``conn`` must have ``row_factory = sqlite3.Row`` set (as provided by
    ``numobel.db.connect``).
    """
    product = search.get_product(conn, product_id)
    if product is None:
        return None
    group_id = product["color_group_id"]
    if group_id is None:
        return None
    rows = conn.execute(
        "SELECT color_name FROM products WHERE color_group_id = ?", (group_id,)
    ).fetchall()
    resolved = [
        c for c in (resolve_name_color(r["color_name"]) for r in rows)
        if c is not None
    ]
    if not resolved:
        return None
    n = len(resolved)
    return QColor(
        sum(c.red() for c in resolved) // n,
        sum(c.green() for c in resolved) // n,
        sum(c.blue() for c in resolved) // n,
    )


def swatch_color(conn: sqlite3.Connection, product_row: sqlite3.Row) -> QColor:
    """Resolve a product's swatch color: name -> family average -> grey."""
    own = resolve_name_color(product_row["color_name"])
    if own is not None:
        return own
    fam = family_average_color(conn, product_row["id"])
    if fam is not None:
        return fam
    return QColor(NEUTRAL_GREY)
