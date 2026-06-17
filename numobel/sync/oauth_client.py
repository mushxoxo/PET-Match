"""Resolve the bundled Google OAuth *Desktop app* client (id + secret).

NUMOBEL ships a single pre-configured OAuth client so end users never have to
create their own Google Cloud project or paste credentials — they just click
"Connect Google…" and authorize in the browser. The client is resolved, in
order, from:

1. Environment variables ``NUMOBEL_GOOGLE_CLIENT_ID`` + ``NUMOBEL_GOOGLE_CLIENT_SECRET``.
2. A ``google_client.json`` file next to the app — either ``db.base_dir()`` (the
   exe's own directory when frozen, the repo root from source) or, for a frozen
   single-file build, the PyInstaller unpack dir (``sys._MEIPASS``). Both the raw
   Google download shape ``{"installed": {...}}`` and a flat
   ``{"client_id": ..., "client_secret": ...}`` are accepted.

For a *Desktop app* OAuth client the "secret" is not confidential — a desktop
app cannot keep one and the OAuth security model relies on the ``localhost``
redirect, not the secret — so shipping it is fine. When no client is configured
the helpers return ``None`` / ``False`` and the UI falls back to the manual
paste dialog.

No ``google`` import here: this is pure config resolution.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from numobel import db

ENV_CLIENT_ID = "NUMOBEL_GOOGLE_CLIENT_ID"
ENV_CLIENT_SECRET = "NUMOBEL_GOOGLE_CLIENT_SECRET"
CLIENT_FILENAME = "google_client.json"


def _from_env() -> tuple[str, str] | None:
    cid = (os.environ.get(ENV_CLIENT_ID) or "").strip()
    sec = (os.environ.get(ENV_CLIENT_SECRET) or "").strip()
    return (cid, sec) if cid and sec else None


def _candidate_dirs() -> list[Path]:
    """Directories to look for ``google_client.json`` in, most-specific first."""
    dirs = [db.base_dir()]
    bundle = getattr(sys, "_MEIPASS", None)  # set only in a frozen build
    if bundle:
        dirs.append(Path(bundle))
    return dirs


def _from_file() -> tuple[str, str] | None:
    for base in _candidate_dirs():
        path = base / CLIENT_FILENAME
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # unreadable / malformed → try the next location
        block = data.get("installed") or data.get("web") or data
        if not isinstance(block, dict):
            continue
        cid = str(block.get("client_id") or "").strip()
        sec = str(block.get("client_secret") or "").strip()
        if cid and sec:
            return cid, sec
    return None


def get_bundled_client() -> tuple[str, str] | None:
    """Return ``(client_id, client_secret)`` from env or the bundled file, else ``None``."""
    return _from_env() or _from_file()


def has_bundled_client() -> bool:
    """True when a bundled OAuth client is configured (env var pair or file)."""
    return get_bundled_client() is not None
