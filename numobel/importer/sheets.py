"""Per-sheet adapters for the messy NUMOBEL color-map workbook.

Each brand sheet has its own layout. ``BRAND_REGISTRY`` maps a sheet name to a
``SheetSpec`` describing the brand identity and an ``extract`` callable that
yields :class:`RawProduct` records (one per real data row, blank padding rows
skipped). Each ``RawProduct`` carries the product fields plus a list of
``MappingCell`` entries (one per cross-reference cell), each with the
column's default target-brand code.

The actual workbook layout was inspected before writing this module; notable
deviations from the original written spec are documented inline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# Brand alias map: a parsed-ref brand_code (or column default) -> canonical
# brand code. Keys are upper-cased prefixes/codes.
# ---------------------------------------------------------------------------

#: External brands (no sheet, not the hub). Links to them get status='external'.
EXTERNAL_BRANDS = {"PCP", "E3"}

#: The hub brand code.
HUB_BRAND = "NUMOBEL"

_ALIASES = {
    "BA": "BA",
    "BAJAJ": "BA",
    "BOL": "BOL",
    "B": "BOL",
    "AT": "AT",
    "EA": "EA",
    "SD": "SD",
    "SAP": "SAP",
    "UTAB": "UTAB",
    "UT": "UTAB",  # EA's UTAB column uses bare 'UT' prefixes (e.g. 'UT19')
    "PNV": "PNV",
    "TRIR": "TRIR",
    "MMT": "MMT",
    "SMI": "SMI",
    "ACP": "PNV",  # ACP is PNV's internal product coding
    "PCP": "PCP",
    "E3": "E3",
}

# N-prefixed codes (NU, NW, NS, NY, NR, NB, and anything starting with 'N'
# followed by uppercase letters) all belong to the NUMOBEL hub.
_NUMOBEL_RE = re.compile(r"^N[A-Z]*$")


def resolve_brand(code: Optional[str]) -> Optional[str]:
    """Resolve a prefix/code to a canonical brand code, or None if unknown."""
    if not code:
        return None
    code = code.upper()
    if code in _ALIASES:
        return _ALIASES[code]
    if _NUMOBEL_RE.match(code):
        return HUB_BRAND
    return None


# ---------------------------------------------------------------------------
# Canonical brand table inserted before products. (code, name, has_sheet)
# ---------------------------------------------------------------------------

BRANDS: list[tuple[str, str, int]] = [
    ("SAP", "SAP", 1),
    ("UTAB", "UTAB", 1),
    ("AT", "AT", 1),
    ("EA", "EA", 1),
    ("SD", "SD", 1),
    ("BOL", "Bollard", 1),
    ("BA", "Bajaj", 1),
    ("PNV", "PNV", 1),
    ("TRIR", "Tranquil", 1),
    ("MMT", "MMT", 1),
    ("SMI", "SMI", 1),
    ("NUMOBEL", "Numobel", 0),
    ("PCP", "PCP", 0),
    ("E3", "E3", 0),
]


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------

@dataclass
class MappingCell:
    """A cross-reference cell to be parsed in pass 2."""
    value: object
    default_brand: Optional[str]


@dataclass
class RawProduct:
    brand_code: str
    sku: Optional[str]
    shade_no: Optional[str] = None
    color_name: Optional[str] = None
    thickness: Optional[float] = None
    self_label: Optional[str] = None
    category: Optional[str] = None
    extra: dict = field(default_factory=dict)
    source_sheet: str = ""
    source_row: int = 0  # 1-based row in the sheet (header == 1)
    mappings: list[MappingCell] = field(default_factory=list)


@dataclass
class SheetSpec:
    sheet_name: str
    extract: Callable[[Iterable[tuple]], list[RawProduct]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s(v) -> Optional[str]:
    """Coerce a cell value to a trimmed string, or None if blank."""
    if v is None:
        return None
    if isinstance(v, float):
        s = str(int(v)) if v.is_integer() else str(v)
    elif isinstance(v, int):
        s = str(v)
    else:
        s = str(v).strip()
    return s or None


def _num(v) -> Optional[float]:
    """Coerce a cell value to a float, or None if not numeric."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _nonempty(v) -> bool:
    return _s(v) is not None


# ---------------------------------------------------------------------------
# Per-sheet extractors. ``rows`` is a list of tuples (one per sheet row).
# ``i`` is the 0-based row index; ``source_row`` stored 1-based.
# ---------------------------------------------------------------------------

def _extract_sap(rows):
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue  # header
        shade = _s(row[1]) if len(row) > 1 else None
        if shade is None:
            continue
        extra = {}
        # Cols 5+ are stray free text (machine names / URLs), not mappings.
        for j in range(5, len(row)):
            txt = _s(row[j])
            if txt is not None:
                extra[f"note_{j}"] = txt
        rp = RawProduct(
            brand_code="SAP",
            sku=shade,
            shade_no=shade,
            color_name=None,
            thickness=_num(row[2]) if len(row) > 2 else None,
            self_label=shade,
            extra=extra,
            source_sheet="SAP",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[3] if len(row) > 3 else None, "AT"))
        rp.mappings.append(MappingCell(row[4] if len(row) > 4 else None, "PNV"))
        out.append(rp)
    return out


def _extract_utab(rows):
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        shade = _s(row[2]) if len(row) > 2 else None
        if shade is None:
            continue
        color = _s(row[3]) if len(row) > 3 else None
        sku = "UTAB" + shade
        label = sku + (" " + color if color else "")
        rp = RawProduct(
            brand_code="UTAB",
            sku=sku,
            shade_no=shade,
            color_name=color,
            thickness=_num(row[4]) if len(row) > 4 else None,
            self_label=label,
            source_sheet="UTAB",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[5] if len(row) > 5 else None, "PCP"))
        out.append(rp)
    return out


def _extract_at(rows):
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        shade = _s(row[2]) if len(row) > 2 else None
        if shade is None:
            continue
        rp = RawProduct(
            brand_code="AT",
            sku="AT" + shade,
            shade_no=shade,
            color_name=_s(row[3]) if len(row) > 3 else None,
            self_label=_s(row[4]) if len(row) > 4 else None,
            thickness=_num(row[5]) if len(row) > 5 else None,
            source_sheet="AT",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[6] if len(row) > 6 else None, "BA"))
        rp.mappings.append(MappingCell(row[7] if len(row) > 7 else None, "PCP"))
        rp.mappings.append(MappingCell(row[8] if len(row) > 8 else None, "EA"))
        out.append(rp)
    return out


def _extract_ea(rows):
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        shade = _s(row[2]) if len(row) > 2 else None
        if shade is None:
            continue
        extra = {}
        status = _s(row[4]) if len(row) > 4 else None
        if status:
            extra["status"] = status
        # Cols 12-16 are an embedded duplicate of the SD sheet (aligned match).
        # Do NOT make products from them; stash raw values + cols 17+ in extra.
        for j in range(12, len(row)):
            txt = _s(row[j])
            if txt is not None:
                extra[f"col_{j}"] = txt
        rp = RawProduct(
            brand_code="EA",
            sku="EA" + shade,
            shade_no=shade,
            color_name=_s(row[3]) if len(row) > 3 else None,
            extra=extra,
            source_sheet="EA",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[5] if len(row) > 5 else None, "BA"))
        rp.mappings.append(MappingCell(row[6] if len(row) > 6 else None, "UTAB"))
        rp.mappings.append(MappingCell(row[7] if len(row) > 7 else None, "BOL"))
        rp.mappings.append(MappingCell(row[8] if len(row) > 8 else None, "SAP"))
        rp.mappings.append(MappingCell(row[9] if len(row) > 9 else None, "ACP"))
        rp.mappings.append(MappingCell(row[10] if len(row) > 10 else None, "AT"))
        # Embedded SD shade at col14 -> aligned EA->SD match link.
        if len(row) > 14 and _nonempty(row[14]):
            rp.mappings.append(MappingCell(row[14], "SD"))
        out.append(rp)
    return out


def _extract_sd(rows):
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        shade = _s(row[2]) if len(row) > 2 else None
        if shade is None:
            continue
        extra = {}
        status = _s(row[5]) if len(row) > 5 else None
        if status:
            extra["status"] = status
        out.append(RawProduct(
            brand_code="SD",
            sku="SD" + shade,
            shade_no=shade,
            color_name=_s(row[3]) if len(row) > 3 else None,
            thickness=_num(row[4]) if len(row) > 4 else None,
            extra=extra,
            source_sheet="SD",
            source_row=i + 1,
        ))
    return out


def _extract_bollard(rows):
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        shade = _s(row[2]) if len(row) > 2 else None
        if shade is None:
            continue
        rp = RawProduct(
            brand_code="BOL",
            sku="B" + shade,
            shade_no=shade,
            color_name=_s(row[3]) if len(row) > 3 else None,
            thickness=_num(row[4]) if len(row) > 4 else None,
            source_sheet="Bollard",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[5] if len(row) > 5 else None, "PCP"))
        rp.mappings.append(MappingCell(row[6] if len(row) > 6 else None, "E3"))
        rp.mappings.append(MappingCell(row[7] if len(row) > 7 else None, "PNV"))
        out.append(rp)
    return out


def _extract_bajaj(rows):
    out = []
    for i, row in enumerate(rows):  # no header; process ALL rows incl. row0
        code = _s(row[0]) if len(row) > 0 else None
        if code is None:
            continue
        color = _s(row[1]) if len(row) > 1 else None
        m = re.search(r"(\d+)", code)
        shade = m.group(1) if m else None
        extra = {}
        c3 = _num(row[3]) if len(row) > 3 else None
        if c3 is not None:
            extra["col3"] = c3
        label = code + (" " + color if color else "")
        out.append(RawProduct(
            brand_code="BA",
            sku=code,
            shade_no=shade,
            color_name=color,
            category=_s(row[2]) if len(row) > 2 else None,
            self_label=label,
            extra=extra,
            source_sheet="Bajaj",
            source_row=i + 1,
        ))
    return out


def _extract_pnv(rows):
    from .refparse import parse_ref
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        ref = parse_ref(row[1] if len(row) > 1 else None)
        if ref is None:
            continue
        rp = RawProduct(
            brand_code="PNV",
            sku=ref.code,
            color_name=ref.name,
            self_label=_s(row[1]),
            source_sheet="PNV",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[2] if len(row) > 2 else None, "NU"))
        rp.mappings.append(MappingCell(row[3] if len(row) > 3 else None, "BOL"))
        out.append(rp)
    return out


def _extract_tranquil(rows):
    from .refparse import parse_ref
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        # col5 is the authoritative Tranquil product; cols0-3 are a stale template.
        ref = parse_ref(row[5] if len(row) > 5 else None)
        if ref is None:
            continue
        extra = {
            "template_prefix": _s(row[1]) if len(row) > 1 else None,
            "template_shade": _s(row[2]) if len(row) > 2 else None,
            "template_color": _s(row[3]) if len(row) > 3 else None,
        }
        extra = {k: v for k, v in extra.items() if v is not None}
        rp = RawProduct(
            brand_code="TRIR",
            sku=ref.code,
            color_name=ref.name,
            self_label=_s(row[5]),
            extra=extra,
            source_sheet="Tranquil",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[6] if len(row) > 6 else None, "NU"))
        out.append(rp)
    return out


def _extract_mmt(rows):
    from .refparse import parse_ref
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue  # row0 is the header
        ref = parse_ref(row[0] if len(row) > 0 else None)
        if ref is None:
            continue
        rp = RawProduct(
            brand_code="MMT",
            sku=ref.code,
            color_name=ref.name,
            self_label=_s(row[0]),
            source_sheet="MMT",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[1] if len(row) > 1 else None, "NU"))
        out.append(rp)
    return out


def _extract_smi(rows):
    # DECISION: cols 6-17 inspected and found entirely empty across all rows,
    # so they are NOT treated as mapping columns. Any non-empty leftover values
    # (defensively) are dumped into extra_json. Only col5 ('NU') is a mapping.
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        shade = _s(row[2]) if len(row) > 2 else None
        if shade is None:
            continue
        extra = {}
        for j in range(6, len(row)):
            txt = _s(row[j])
            if txt is not None:
                extra[f"col_{j}"] = txt
        rp = RawProduct(
            brand_code="SMI",
            sku="SMI" + shade,
            shade_no=shade,
            color_name=_s(row[3]) if len(row) > 3 else None,
            self_label=_s(row[4]) if len(row) > 4 else None,
            extra=extra,
            source_sheet="SMI",
            source_row=i + 1,
        )
        rp.mappings.append(MappingCell(row[5] if len(row) > 5 else None, "NU"))
        out.append(rp)
    return out


# ---------------------------------------------------------------------------
# Registry + price sheet
# ---------------------------------------------------------------------------

BRAND_REGISTRY: dict[str, SheetSpec] = {
    "SAP": SheetSpec("SAP", _extract_sap),
    "UTAB": SheetSpec("UTAB", _extract_utab),
    "AT": SheetSpec("AT", _extract_at),
    "EA": SheetSpec("EA", _extract_ea),
    "SD": SheetSpec("SD", _extract_sd),
    "Bollard": SheetSpec("Bollard", _extract_bollard),
    "Bajaj": SheetSpec("Bajaj", _extract_bajaj),
    "PNV": SheetSpec("PNV", _extract_pnv),
    "Tranquil": SheetSpec("Tranquil", _extract_tranquil),
    "MMT": SheetSpec("MMT", _extract_mmt),
    "SMI": SheetSpec("SMI", _extract_smi),
}

PRICE_SHEET = "Price Comparison"


def extract_prices(rows) -> list[tuple]:
    """Return (seller, mrp, mrp_sft, dp, dp_sft, profit, discount,
    cust_price, cust_price_sft) tuples from the Price Comparison sheet."""
    out = []
    for i, row in enumerate(rows):
        if i == 0:
            continue  # header
        seller = _s(row[1]) if len(row) > 1 else None
        if seller is None:
            continue
        out.append((
            seller,
            _num(row[2]) if len(row) > 2 else None,
            _num(row[3]) if len(row) > 3 else None,
            _num(row[4]) if len(row) > 4 else None,
            _num(row[5]) if len(row) > 5 else None,
            _num(row[6]) if len(row) > 6 else None,
            _num(row[7]) if len(row) > 7 else None,
            _num(row[8]) if len(row) > 8 else None,
            _num(row[9]) if len(row) > 9 else None,
        ))
    return out
