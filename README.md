# NUMOBEL Colored PET Sheet Catalog

A desktop app (PySide6) that consolidates NUMOBEL's per-brand colored acoustic-PET
catalogs from one Excel workbook into a searchable SQLite database, with cross-brand
"similar color" mappings, an editable price-comparison view, photo attachments, a
mapping editor, an audit log, and light/dark themes.

## Status

**Phase A (MVP) — complete:** Excel import, search by color/brand, results table,
product detail with mapped similar colors, and a price-comparison tab.

**Phase B — complete:** photo attach/remove on a product; a mapping editor (add /
remove / resolve cross-brand similar-color links, all stored as `source='user'`);
an editable price grid; an audit log of every change (own tab); a light/dark theme
toggle (View ▸ Toggle Theme, `Ctrl+T`, persisted); and PyInstaller packaging.

## Setup

```bash
pip install -r requirements.txt
```

## Import the catalog (run once, or after the Excel changes)

Builds `numobel.db` from `data/numobel-catalog.xlsx` and prints a summary
(product counts + resolved/unresolved/external link tallies). The format is
auto-detected, so the default file can be either a full snapshot exported from
the app or the original `NUMOBEL_ACOUSTICS_COLOR_MAPS.xlsx` master workbook:

```bash
python -m numobel.importer.run_import
```

## Run the app

```bash
python run.py
```

- **Search box** — type a color name (partial OK) or a brand. The scope selector
  (All / Color / Brand) and the brand-filter dropdown narrow results.
- **Results table** — sortable; click a row to load it.
- **Detail panel** — shows all fields (including extra info preserved from Excel) and a
  **Similar Colors** list. Resolved matches are double-clickable to jump to that product;
  unresolved and external (e.g. PCP / E3) references are shown as labelled text.
  - **Add Photo… / Remove Photo** — attach a swatch image; it is copied into `images/`
    and the path is stored (relative to the app, so the DB stays portable).
  - **Add Similar Color… / Remove / Resolve…** — edit cross-brand mappings: link this
    product to another (search-and-pick), drop a link, or point an unresolved/external
    reference at a real product. User edits are tracked separately from imported links.
- **Prices tab** — the price-comparison table across sellers; **double-click a cell to
  edit** it (numeric fields validate; the discount column round-trips its percentage).
- **Audit tab** — a chronological log of every change (photos, mappings, prices).
- **View ▸ Toggle Theme** (`Ctrl+T`) — switch light/dark; the choice is remembered.

## Tests

```bash
python -m pytest -q
```

## Build a standalone executable (optional)

```bash
pip install pyinstaller
pyinstaller numobel.spec
```

The binary lands in `dist/` (`dist/numobel.exe` on Windows, `dist/numobel` on
Linux/macOS). PyInstaller does **not** cross-compile — build the Windows `.exe` on
Windows. `numobel.db` and `images/` are not bundled: the app reads/writes them next to
the executable, so ship a `numobel.db` (from the importer) alongside the binary, and
`images/` is created on the first photo attach.

## Layout

```
numobel/
  db.py                 # SQLite schema, connection + path helpers (frozen-aware)
  search.py             # read queries: search, brand list, similar colors, prices
  audit.py              # log_change() + get_audit_log()
  mutations.py          # all user writes (photos, links, prices); each logs + commits
  importer/
    refparse.py         # normalizes messy cross-reference cell formats
    sheets.py           # per-sheet adapters + brand alias map (BRAND_REGISTRY)
    run_import.py        # two-pass build of numobel.db
  ui/
    main_window.py      # search bar, results table, tabs, theme toggle
    detail_panel.py     # product detail, photos, mapping editor
    price_tab.py        # editable price comparison
    audit_tab.py        # chronological change log
    theme.py            # light/dark QSS
  app.py                # QApplication entrypoint
run.py                  # launcher
numobel.spec            # PyInstaller build spec
```

## Data notes

The source workbook is hand-maintained and irregular: every brand sheet has a different
schema, and cross-brand matches are encoded as inconsistent reference columns inside the
sheets (`AT22`, `PCP 27`, `ACP19-Violet`, `NW01133-Snow White`, `BA14-`, …). The importer
normalizes these and resolves them against imported products where possible; references to
brands without their own sheet (PCP, E3) are preserved as `external`. `NUMOBEL` is the hub
brand — its products are synthesized from the codes other brands map to. `ACP` codes are
PNV's internal coding and resolve to PNV products. One known ambiguity: the `Tranquil`
sheet's left columns disagree with its mapping columns; the mapping columns are treated as
authoritative and the rest preserved in each product's extra info.
