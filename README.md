# NUMOBEL Colored PET Sheet Catalog

A desktop app (PySide6) that consolidates NUMOBEL's per-brand colored acoustic-PET
catalogs from one Excel workbook into a searchable SQLite database, with cross-brand
"similar color" mappings, a price-comparison view, and (in later phases) photo
attachments, mapping editing, audit log, and themes.

## Status

**Phase A (MVP) — complete:** Excel import, search by color/brand, results table,
product detail with mapped similar colors (read-only), and a price-comparison tab.
Phase B (photo attach, mapping editor, audit log, light/dark theme, `.exe` packaging)
is planned next — see `~/.claude/plans/numobel-colored-pet-binary-tower.md`.

## Setup

```bash
pip install -r requirements.txt
```

## Import the catalog (run once, or after the Excel changes)

Builds `numobel.db` from `my_excel/NUMOBEL_ACOUSTICS_COLOR_MAPS.xlsx` and prints a
summary (per-sheet product counts + resolved/unresolved/external link tallies):

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
- **Prices tab** — the price-comparison table across sellers.

## Tests

```bash
python -m pytest -q
```

## Layout

```
numobel/
  db.py                 # SQLite schema + connection helpers
  search.py             # read queries: search, brand list, similar colors, prices
  importer/
    refparse.py         # normalizes messy cross-reference cell formats
    sheets.py           # per-sheet adapters + brand alias map (BRAND_REGISTRY)
    run_import.py        # two-pass build of numobel.db
  ui/
    main_window.py      # search bar, results table, tabs
    detail_panel.py     # product detail + similar colors
    price_tab.py        # price comparison
  app.py                # QApplication entrypoint
run.py                  # launcher
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
