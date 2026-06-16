"""Transport-agnostic storage backend contract for Google Sheets sync.

The :class:`Backend` ABC is the *only* coupling point between the sync logic
(photo diffing in :mod:`numobel.sync.photos`, and the M4 orchestration layer)
and the actual cloud transport. By depending on this interface rather than on
``googleapiclient`` directly, all of that logic is unit-testable against an
in-memory ``FakeBackend`` (see ``tests/sync_fakes.py``) with no network and no
``google`` imports.

Two concrete implementations satisfy this contract:

* ``GoogleBackend`` (M3b) — talks to Sheets + Drive over HTTP.
* ``FakeBackend`` (tests) — keeps everything in Python dicts.

Data model the methods agree on:

* The catalog is stored twice in the spreadsheet: human-readable per-table tabs
  (for people to eyeball) AND a hidden lossless ``_data`` blob that is the
  *source of truth*. :meth:`read_all` / :meth:`write_all` speak the
  :func:`numobel.sync.serialize.dump_rows` shape
  (``{table: {"columns": [...], "rows": [[...]]}}``).
* A ``_meta`` tab holds small ``{key: value}`` bookkeeping (revision, device id,
  schema version, …) as strings.
* Photos live in a Drive folder; a ``_photo_map`` tab records, per product, the
  Drive file id + filename + checksum so devices can diff without re-downloading.
"""

from __future__ import annotations

import abc


class Backend(abc.ABC):
    """Abstract cloud storage backend for the catalog + its photos."""

    @abc.abstractmethod
    def ensure_spreadsheet(self) -> dict:
        """Idempotently ensure the spreadsheet + photo folder exist.

        Creates them on first call, reuses them afterwards. Returns
        ``{"spreadsheet_id": str, "photo_folder_id": str}``. Safe to call every
        run.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def read_meta(self) -> dict:
        """Return the ``_meta`` tab as a plain ``{key: value}`` dict.

        Values are returned as strings. Returns an empty dict when the tab is
        unset/empty.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def write_meta(self, meta: dict) -> None:
        """Overwrite the ``_meta`` tab from a ``{key: value}`` dict."""
        raise NotImplementedError

    @abc.abstractmethod
    def read_all(self) -> dict:
        """Return the catalog in :func:`serialize.dump_rows` shape.

        Parsed from the lossless hidden ``_data`` blob (the source of truth),
        NOT from the human-readable tabs. Shape:
        ``{table: {"columns": [...], "rows": [[...]]}}``.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def write_all(self, data: dict) -> None:
        """Persist ``data`` (the :func:`serialize.dump_rows` shape).

        Writes BOTH the human-readable per-table tabs and the hidden lossless
        ``_data`` blob so they stay in lock-step.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def read_photo_map(self) -> list[dict]:
        """Return the photo map rows.

        Each row is
        ``{"product_id": int, "drive_file_id": str, "filename": str,
        "checksum": str}``. Returns an empty list when no photos are tracked.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def write_photo_map(self, rows: list[dict]) -> None:
        """Overwrite the photo map with ``rows`` (same shape as the reader)."""
        raise NotImplementedError

    @abc.abstractmethod
    def upload_photo(self, local_path: str, filename: str) -> str:
        """Upload/replace a photo in the photo folder; return its storage id.

        ``filename`` is the on-disk basename (already unique per product). An
        existing file of the same name may be replaced or a fresh id minted —
        callers must use the returned id, not assume stability.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def download_photo(self, file_id: str, dest_path: str) -> None:
        """Download the photo identified by ``file_id`` to ``dest_path``."""
        raise NotImplementedError
