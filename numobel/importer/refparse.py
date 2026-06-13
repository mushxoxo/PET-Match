"""Normalize messy cross-reference cell values into structured results."""

import re
from typing import NamedTuple


class ParsedRef(NamedTuple):
    brand_code: str | None
    number: str
    code: str
    name: str | None
    normalized: str
    raw: str


_REF_RE = re.compile(r"^\s*([A-Za-z]+)?\s*-?\s*(\d+)\s*-?\s*(.*)$")


def _coerce(raw) -> str | None:
    """Convert a cell value to a string for parsing, or None if blank."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        # Avoid treating booleans as numbers.
        return None
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        return str(int(raw)) if raw.is_integer() else str(raw)
    text = str(raw).strip()
    if not text:
        return None
    return text


def parse_ref(raw, default_brand=None) -> ParsedRef | None:
    text = _coerce(raw)
    if text is None:
        return None

    match = _REF_RE.match(text)
    if not match:
        return None

    prefix, number, trailing = match.groups()

    if prefix:
        brand_code = prefix.upper()
    elif default_brand:
        brand_code = default_brand.upper()
    else:
        brand_code = None

    code = f"{brand_code}{number}" if brand_code else number

    name = trailing.strip().strip("-").strip() if trailing else ""
    name = name or None

    raw_str = str(raw).strip()

    return ParsedRef(
        brand_code=brand_code,
        number=number,
        code=code,
        name=name,
        normalized=code,
        raw=raw_str,
    )
