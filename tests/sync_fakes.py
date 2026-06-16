"""In-memory test doubles for the sync backend.

``FakeBackend`` implements the full :class:`numobel.sync.backend.Backend`
contract using plain Python state, so the offline sync logic (photos, and the
M4 orchestration) can be exercised with no network and no ``google`` imports.
It is deliberately importable by later milestones' tests too.
"""

from __future__ import annotations

import copy
from pathlib import Path

from numobel.sync.backend import Backend


class FakeBackend(Backend):
    """A Backend that stores everything in memory.

    Attributes:
        meta: the ``_meta`` ``{key: value}`` dict.
        data: the catalog in :func:`serialize.dump_rows` shape (or ``{}``).
        photo_map: list of photo-map row dicts.
        photo_store: ``{file_id: bytes}`` standing in for Drive's blob storage.
        upload_count / download_count: call counters for assertions.
    """

    def __init__(self, download_dir=None):
        self.spreadsheet_id = "fake-spreadsheet"
        self.photo_folder_id = "fake-folder"
        self.meta: dict = {}
        self.data: dict = {}
        self.photo_map: list[dict] = []
        self.photo_store: dict[str, bytes] = {}
        self.download_dir = Path(download_dir) if download_dir else None
        self.upload_count = 0
        self.download_count = 0
        self._id_counter = 0

    # -- spreadsheet / meta ------------------------------------------------- #
    def ensure_spreadsheet(self) -> dict:
        return {
            "spreadsheet_id": self.spreadsheet_id,
            "photo_folder_id": self.photo_folder_id,
        }

    def read_meta(self) -> dict:
        return dict(self.meta)

    def write_meta(self, meta: dict) -> None:
        self.meta = {str(k): str(v) for k, v in meta.items()}

    # -- catalog ------------------------------------------------------------ #
    def read_all(self) -> dict:
        return copy.deepcopy(self.data)

    def write_all(self, data: dict) -> None:
        self.data = copy.deepcopy(data)

    # -- photo map ---------------------------------------------------------- #
    def read_photo_map(self) -> list[dict]:
        return copy.deepcopy(self.photo_map)

    def write_photo_map(self, rows: list[dict]) -> None:
        self.photo_map = copy.deepcopy(rows)

    # -- photo blobs -------------------------------------------------------- #
    def upload_photo(self, local_path: str, filename: str) -> str:
        with open(local_path, "rb") as fh:
            blob = fh.read()
        file_id = f"file-{filename}-{self._id_counter}"
        self._id_counter += 1
        self.photo_store[file_id] = blob
        self.upload_count += 1
        return file_id

    def download_photo(self, file_id: str, dest_path: str) -> None:
        blob = self.photo_store[file_id]
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as fh:
            fh.write(blob)
        self.download_count += 1
