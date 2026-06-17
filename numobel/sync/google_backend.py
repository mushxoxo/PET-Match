"""Concrete :class:`~numobel.sync.backend.Backend` over Google Sheets + Drive.

The catalog is stored in a single spreadsheet two ways:

* **Human-readable tabs** — one per :data:`serialize.SNAPSHOT_TABLES`, so people
  can eyeball the data in the browser.
* **A hidden ``_data`` tab** — the lossless source of truth, a JSON blob of the
  whole :func:`serialize.dump_rows` shape chunked across cells (Sheets caps a
  cell at 50k chars, so we chunk at :data:`_CHUNK`, mirroring the xlsx exporter).

Two more hidden tabs hold bookkeeping: ``_meta`` (``{key: value}``) and
``_photos`` (the per-product Drive photo map). Photos themselves live as blobs
in a dedicated Drive folder.

The module-level helpers (encode/decode/round-trip the various tab layouts) are
PURE — they import no ``google`` library and are fully unit-tested. Every actual
Sheets/Drive call lives inside :class:`GoogleBackend`, which imports
``googleapiclient`` LAZILY so merely importing this module (e.g. to collect its
tests) never requires the libraries to be present.
"""

from __future__ import annotations

import json
import re

from numobel.sync import errors
from numobel.sync.backend import Backend
from numobel.sync.serialize import SNAPSHOT_TABLES

# --------------------------------------------------------------------------- #
# Tab names
# --------------------------------------------------------------------------- #
DATA_TAB = "_data"
META_TAB = "_meta"
PHOTOS_TAB = "_photos"

#: JSON chunk size — under Sheets' 50k char/cell cap (mirrors the exporter).
_CHUNK = 32000

_PHOTO_MAP_COLUMNS = ["product_id", "drive_file_id", "filename", "checksum"]


# --------------------------------------------------------------------------- #
# Pure helpers (NO google import) — the testable core
# --------------------------------------------------------------------------- #
def encode_data_blob(data: dict) -> list[list]:
    """Encode the dump_rows ``data`` dict as chunked spreadsheet rows.

    ``json.dumps`` the whole dict, split it into :data:`_CHUNK`-sized pieces,
    and return ``[["seq", "chunk"], [0, piece0], [1, piece1], ...]``. Always
    emits the header row plus at least one (possibly empty) chunk row, so the
    layout is deterministic.
    """
    blob = json.dumps(data)
    pieces = [blob[i : i + _CHUNK] for i in range(0, len(blob), _CHUNK)] or [""]
    rows: list[list] = [["seq", "chunk"]]
    for seq, piece in enumerate(pieces):
        rows.append([seq, piece])
    return rows


def decode_data_blob(rows: list[list]) -> dict:
    """Inverse of :func:`encode_data_blob`.

    Skips the header, sorts the remaining rows by their ``seq`` cell,
    concatenates the chunks, and ``json.loads`` the result. Returns ``{}`` for
    empty / missing input.
    """
    if not rows or len(rows) < 2:
        return {}
    body = rows[1:]
    ordered = sorted(body, key=lambda r: int(r[0]))
    blob = "".join(str(r[1]) if len(r) > 1 and r[1] is not None else "" for r in ordered)
    if not blob:
        return {}
    return json.loads(blob)


def meta_to_rows(meta: dict) -> list[list]:
    """Encode a ``{key: value}`` meta dict as ``[["key","value"], [k, v], ...]``."""
    rows: list[list] = [["key", "value"]]
    for key, value in meta.items():
        rows.append([str(key), str(value)])
    return rows


def rows_to_meta(rows: list[list]) -> dict:
    """Inverse of :func:`meta_to_rows` (skips the header). Returns ``{}`` if empty."""
    if not rows or len(rows) < 2:
        return {}
    meta = {}
    for row in rows[1:]:
        if not row:
            continue
        key = str(row[0])
        value = str(row[1]) if len(row) > 1 and row[1] is not None else ""
        meta[key] = value
    return meta


def photo_map_to_rows(rows: list[dict]) -> list[list]:
    """Encode photo-map row dicts as a header + one row per entry."""
    out: list[list] = [list(_PHOTO_MAP_COLUMNS)]
    for entry in rows:
        out.append([entry.get(col, "") for col in _PHOTO_MAP_COLUMNS])
    return out


def rows_to_photo_map(rows: list[list]) -> list[dict]:
    """Inverse of :func:`photo_map_to_rows`, coercing ``product_id`` to ``int``."""
    if not rows or len(rows) < 2:
        return []
    out: list[dict] = []
    for row in rows[1:]:
        if not row:
            continue
        entry = {}
        for idx, col in enumerate(_PHOTO_MAP_COLUMNS):
            entry[col] = row[idx] if idx < len(row) else ""
        try:
            entry["product_id"] = int(entry["product_id"])
        except (TypeError, ValueError):
            continue
        for col in ("drive_file_id", "filename", "checksum"):
            entry[col] = str(entry[col]) if entry[col] is not None else ""
        out.append(entry)
    return out


def readable_table_rows(data: dict, table: str) -> list[list]:
    """``[columns] + rows`` for ``table`` from the dump_rows shape (header first).

    Used to render the human-readable per-table tabs. Returns ``[]`` when the
    table is absent.
    """
    block = data.get(table)
    if not block:
        return []
    return [list(block["columns"]), *[list(r) for r in block["rows"]]]


# --------------------------------------------------------------------------- #
# Spreadsheet discovery / adoption helpers
# --------------------------------------------------------------------------- #
def extract_spreadsheet_id(text: str) -> str:
    """From a Google Sheets URL or a bare id, return the spreadsheet id.

    Pulls the id out of a ``/d/<id>/`` URL; passes through a bare id-looking
    token; otherwise returns the stripped input (let the API surface a clear
    error).
    """
    text = text.strip()
    if "/d/" in text:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", text)
        if match:
            return match.group(1)
    # A bare id (or anything unrecognized) passes through unchanged; an invalid
    # id lets the API surface a clear error.
    return text


def classify_adopt(tab_titles: list[str]) -> str:
    """Classify an existing spreadsheet by its tab titles for adoption.

    Returns ``"numobel"`` (our format: both ``_data`` and ``_meta`` present),
    ``"empty"`` (blank/new sheet — no tabs, or only a default ``"Sheet1"``),
    or ``"foreign"`` (has unrelated tabs but isn't ours).
    """
    titles = set(tab_titles)
    if DATA_TAB in titles and META_TAB in titles:
        return "numobel"
    if not titles or titles <= {"Sheet1"}:
        return "empty"
    return "foreign"


# --------------------------------------------------------------------------- #
# A1 range helpers
# --------------------------------------------------------------------------- #
def _quote_tab(name: str) -> str:
    """Quote a tab name for an A1 range (leading-underscore names need quotes)."""
    return "'" + name.replace("'", "''") + "'"


def _range(name: str, span: str) -> str:
    """Build a quoted A1 range like ``'_meta'!A:B``."""
    return f"{_quote_tab(name)}!{span}"


# --------------------------------------------------------------------------- #
# GoogleBackend
# --------------------------------------------------------------------------- #
class GoogleBackend(Backend):
    """Sheets + Drive implementation of the :class:`Backend` contract.

    ``googleapiclient`` is imported lazily on first use so this class can be
    imported without the library present. ``HttpError`` from Google calls is
    mapped to the sync error taxonomy (404 → :class:`errors.SheetMissingError`;
    401/403 → :class:`errors.AuthError`); other statuses (including transient
    5xx/429) propagate so the orchestration layer can classify them via
    :func:`errors.is_offline_error`.
    """

    SPREADSHEET_TITLE = "NUMOBEL Catalog Sync"
    PHOTO_FOLDER_NAME = "NUMOBEL Catalog Photos"

    def __init__(self, credentials, spreadsheet_id=None, photo_folder_id=None):
        self.credentials = credentials
        self.spreadsheet_id = spreadsheet_id
        self.photo_folder_id = photo_folder_id
        self._sheets = None
        self._drive = None

    # -- service construction ---------------------------------------------- #
    def _services(self):
        """Lazily build and cache the Sheets + Drive service objects."""
        if self._sheets is None or self._drive is None:
            from googleapiclient.discovery import build

            self._sheets = build(
                "sheets", "v4", credentials=self.credentials, cache_discovery=False
            )
            self._drive = build(
                "drive", "v3", credentials=self.credentials, cache_discovery=False
            )
        return self._sheets, self._drive

    @staticmethod
    def _classify(exc):
        """Map a googleapiclient ``HttpError`` to the sync error taxonomy.

        404 → :class:`errors.SheetMissingError`; 401/403 → :class:`errors.AuthError`.
        Anything else is returned unchanged so the caller re-raises it (transient
        statuses are then sorted by :func:`errors.is_offline_error`).
        """
        status = errors.http_status_of(exc)
        if status == 404:
            return errors.SheetMissingError(str(exc))
        if status in (401, 403):
            return errors.AuthError(str(exc))
        return exc

    def _execute(self, request):
        """Execute a googleapiclient request, translating ``HttpError``."""
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except HttpError as exc:
            raise self._classify(exc) from exc

    # -- spreadsheet / folder provisioning --------------------------------- #
    def ensure_spreadsheet(self) -> dict:
        """Idempotently ensure the spreadsheet + photo folder exist.

        Returns ``{"spreadsheet_id", "photo_folder_id"}``. If both ids are
        already set this is a no-op that just returns them.

        .. warning:: Idempotent on the happy path and when both ids are already
           set, but **NOT crash-safe mid-flight**: if the spreadsheet is created
           and the subsequent Drive photo-folder creation then fails, the
           spreadsheet id is set on the instance (``self.spreadsheet_id``) but
           never returned to the caller, so a naive retry would create a *second*
           (orphan) spreadsheet. ``self.spreadsheet_id`` is assigned on the
           instance immediately after the create call (and ``self.photo_folder_id``
           likewise), so a caller inspecting the instance after a failure can
           recover the id. The orchestration layer (M4) should persist
           ``self.spreadsheet_id`` / ``self.photo_folder_id`` as soon as each is
           created so a retry reuses them rather than orphaning a spreadsheet.
        """
        if self.spreadsheet_id and self.photo_folder_id:
            return {
                "spreadsheet_id": self.spreadsheet_id,
                "photo_folder_id": self.photo_folder_id,
            }

        sheets, _ = self._services()

        if not self.spreadsheet_id:
            body = {
                "properties": {"title": self.SPREADSHEET_TITLE},
                "sheets": self._required_sheet_specs(),
            }
            result = self._execute(
                sheets.spreadsheets().create(body=body, fields="spreadsheetId")
            )
            self.spreadsheet_id = result["spreadsheetId"]

        self._ensure_photo_folder()

        return {
            "spreadsheet_id": self.spreadsheet_id,
            "photo_folder_id": self.photo_folder_id,
        }

    @staticmethod
    def _required_sheet_specs() -> list[dict]:
        """The ``sheets`` create-body specs for every tab we provision.

        Single source of truth shared by :meth:`ensure_spreadsheet` (create body)
        and :meth:`_add_missing_tabs` (batchUpdate addSheet). Visible
        :data:`SNAPSHOT_TABLES` first, then the hidden ``_data``/``_meta``/
        ``_photos`` bookkeeping tabs.
        """
        specs: list[dict] = []
        for table in SNAPSHOT_TABLES:
            specs.append({"properties": {"title": table}})
        for hidden in (DATA_TAB, META_TAB, PHOTOS_TAB):
            specs.append({"properties": {"title": hidden, "hidden": True}})
        return specs

    def _ensure_photo_folder(self) -> str:
        """Create the Drive photo folder if not already set; return its id."""
        if not self.photo_folder_id:
            _, drive = self._services()
            folder = self._execute(
                drive.files().create(
                    body={
                        "name": self.PHOTO_FOLDER_NAME,
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                    fields="id",
                )
            )
            self.photo_folder_id = folder["id"]
        return self.photo_folder_id

    def _add_missing_tabs(self, existing_titles) -> None:
        """Add any required tabs absent from ``self.spreadsheet_id``.

        Computes which of :meth:`_required_sheet_specs` are missing (by title)
        and adds them in one ``batchUpdate`` ``addSheet`` call, preserving the
        hidden flag for the bookkeeping tabs. No-op when nothing is missing.
        """
        present = set(existing_titles)
        requests = [
            {"addSheet": spec}
            for spec in self._required_sheet_specs()
            if spec["properties"]["title"] not in present
        ]
        if not requests:
            return
        sheets, _ = self._services()
        self._execute(
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            )
        )

    # -- discovery / adoption ---------------------------------------------- #
    def list_catalog_spreadsheets(self) -> list[dict]:
        """List spreadsheets this app can see (via ``drive.file`` scope).

        Under the per-file ``drive.file`` scope this is exactly the set of
        spreadsheets this app created. Returns ``[{"id","name","modifiedTime"}]``
        newest-first (single page; up to 100).
        """
        _, drive = self._services()
        result = self._execute(
            drive.files().list(
                q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                fields="files(id,name,modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=100,
            )
        )
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "modifiedTime": f.get("modifiedTime", ""),
            }
            for f in result.get("files", [])
        ]

    def adopt_spreadsheet(self, spreadsheet_id: str) -> dict:
        """Adopt an existing spreadsheet as the linked catalog.

        Inspects its tabs: a ``"foreign"`` sheet is rejected with
        :class:`errors.NotNumobelSheetError`; an ``"empty"`` sheet is
        provisioned with our tabs (revision 0); a ``"numobel"`` sheet has its
        meta/revision read back. Recovers (or recreates) the Drive photo folder.
        Returns ``{"spreadsheet_id","photo_folder_id","revision","is_numobel"}``.
        """
        self.spreadsheet_id = spreadsheet_id
        sheets, drive = self._services()

        info = self._execute(
            sheets.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties.title",
            )
        )
        titles = [s["properties"]["title"] for s in info.get("sheets", [])]
        kind = classify_adopt(titles)

        if kind == "foreign":
            raise errors.NotNumobelSheetError(
                "This spreadsheet isn't a NUMOBEL catalog. Pick another or "
                "create a new one."
            )

        if kind == "empty":
            self._add_missing_tabs(titles)
            revision = 0
            meta: dict = {}
        else:  # numobel
            meta = self.read_meta()
            revision = int(meta.get("revision", 0) or 0)

        # Photo folder recovery: verify the recorded folder still exists / is
        # usable, else create a fresh one.
        folder = meta.get("photo_folder_id")
        if folder:
            try:
                got = self._execute(
                    drive.files().get(fileId=folder, fields="id,trashed")
                )
                if got.get("trashed"):
                    folder = None
            except errors.SheetMissingError:
                folder = None
        if folder:
            self.photo_folder_id = folder
        else:
            self.photo_folder_id = None
            self._ensure_photo_folder()

        return {
            "spreadsheet_id": self.spreadsheet_id,
            "photo_folder_id": self.photo_folder_id,
            "revision": revision,
            "is_numobel": True,
        }

    # -- low-level Sheets value IO ----------------------------------------- #
    def _read_values(self, tab: str, span: str) -> list[list]:
        sheets, _ = self._services()
        result = self._execute(
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=_range(tab, span))
        )
        return result.get("values", [])

    def _write_values(self, tab: str, span: str, values: list[list]) -> None:
        """Clear ``tab`` over ``span`` then write ``values`` (RAW) from A1."""
        sheets, _ = self._services()
        self._execute(
            sheets.spreadsheets()
            .values()
            .clear(spreadsheetId=self.spreadsheet_id, range=_range(tab, span), body={})
        )
        if not values:
            return
        self._execute(
            sheets.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=_range(tab, "A1"),
                valueInputOption="RAW",
                body={"values": values},
            )
        )

    # -- meta -------------------------------------------------------------- #
    def read_meta(self) -> dict:
        return rows_to_meta(self._read_values(META_TAB, "A:B"))

    def write_meta(self, meta: dict) -> None:
        self._write_values(META_TAB, "A:B", meta_to_rows(meta))

    # -- catalog ----------------------------------------------------------- #
    def read_all(self) -> dict:
        return decode_data_blob(self._read_values(DATA_TAB, "A:B"))

    def write_all(self, data: dict) -> None:
        # Human-readable tabs, one per snapshot table.
        for table in SNAPSHOT_TABLES:
            self._write_values(table, "A:ZZ", readable_table_rows(data, table))
        # The lossless source of truth.
        self._write_values(DATA_TAB, "A:B", encode_data_blob(data))

    # -- photo map --------------------------------------------------------- #
    def read_photo_map(self) -> list[dict]:
        return rows_to_photo_map(self._read_values(PHOTOS_TAB, "A:D"))

    def write_photo_map(self, rows: list[dict]) -> None:
        self._write_values(PHOTOS_TAB, "A:D", photo_map_to_rows(rows))

    # -- photo blobs ------------------------------------------------------- #
    def upload_photo(self, local_path: str, filename: str) -> str:
        from googleapiclient.http import MediaFileUpload

        _, drive = self._services()
        media = MediaFileUpload(local_path, resumable=True)
        result = self._execute(
            drive.files().create(
                body={"name": filename, "parents": [self.photo_folder_id]},
                media_body=media,
                fields="id",
            )
        )
        return result["id"]

    def download_photo(self, file_id: str, dest_path: str) -> None:
        """Download a Drive blob to ``dest_path`` atomically and error-translated.

        The blob is streamed into a sibling ``*.part`` temp file and only
        ``os.replace``-d onto ``dest_path`` once the full download succeeds, so a
        failure mid-stream never leaves a truncated/empty file at the final path.
        Both the ``get_media`` request build and the ``next_chunk()`` loop are
        routed through :meth:`_classify` (via the same ``HttpError`` handling the
        other methods use), so 404 → :class:`errors.SheetMissingError`, 401/403 →
        :class:`errors.AuthError`, and any other status propagates intact. The
        destination's parent directory is created if missing.
        """
        import os
        import tempfile

        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaIoBaseDownload

        _, drive = self._services()
        dest_dir = os.path.dirname(os.path.abspath(dest_path))
        os.makedirs(dest_dir, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                try:
                    request = drive.files().get_media(fileId=file_id)
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                except HttpError as exc:
                    raise self._classify(exc) from exc
            os.replace(tmp_path, dest_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
            raise
