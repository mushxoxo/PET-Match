"""Sync settings/state helpers backed by the ``settings`` table.

Pure-stdlib and offline-testable: this module deliberately imports no
``google`` / ``googleapiclient`` libraries. It stores the OAuth client creds,
the raw OAuth token JSON, the linked spreadsheet/photo-folder ids, the sync
revision watermark, and the device identity under namespaced ``settings`` keys.

Settings values are TEXT, so booleans are encoded as ``"1"`` / ``"0"`` and ints
as their decimal string. Reads decode them back. ``numobel.db.set_setting``
commits but does NOT fire the mutation listener hook, so persisting sync state
can never trigger a sync push.
"""

from __future__ import annotations

import sqlite3
import uuid

from numobel import db

# --------------------------------------------------------------------------- #
# Namespaced settings keys
# --------------------------------------------------------------------------- #
KEY_OAUTH_CLIENT_ID = "oauth_client_id"
KEY_OAUTH_CLIENT_SECRET = "oauth_client_secret"
KEY_OAUTH_TOKEN_JSON = "oauth_token_json"
KEY_SPREADSHEET_ID = "sync_spreadsheet_id"
KEY_PHOTO_FOLDER_ID = "sync_photo_folder_id"
KEY_LAST_SYNCED_REVISION = "sync_last_synced_revision"
KEY_DEVICE_ID = "sync_device_id"
KEY_PENDING = "sync_pending"
KEY_LINKED = "sync_linked"

#: Every sync key cleared on Disconnect EXCEPT the device id (which persists).
_CLEARABLE_KEYS = (
    KEY_OAUTH_CLIENT_ID,
    KEY_OAUTH_CLIENT_SECRET,
    KEY_OAUTH_TOKEN_JSON,
    KEY_SPREADSHEET_ID,
    KEY_PHOTO_FOLDER_ID,
    KEY_LAST_SYNCED_REVISION,
    KEY_PENDING,
    KEY_LINKED,
)


def _delete_setting(conn: sqlite3.Connection, key: str) -> None:
    """Remove a settings key entirely (and commit)."""
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Device identity
# --------------------------------------------------------------------------- #
def get_device_id(conn: sqlite3.Connection) -> str:
    """Return this install's device id, lazily creating a stable one if absent."""
    device_id = db.get_setting(conn, KEY_DEVICE_ID)
    if not device_id:
        device_id = uuid.uuid4().hex
        db.set_setting(conn, KEY_DEVICE_ID, device_id)
    return device_id


# --------------------------------------------------------------------------- #
# OAuth client credentials
# --------------------------------------------------------------------------- #
def get_client_id(conn: sqlite3.Connection):
    """Return the stored OAuth client id, or ``None``."""
    return db.get_setting(conn, KEY_OAUTH_CLIENT_ID)


def get_client_secret(conn: sqlite3.Connection):
    """Return the stored OAuth client secret, or ``None``."""
    return db.get_setting(conn, KEY_OAUTH_CLIENT_SECRET)


def set_client_credentials(
    conn: sqlite3.Connection, client_id: str, client_secret: str
) -> None:
    """Persist the OAuth client id and secret together."""
    db.set_setting(conn, KEY_OAUTH_CLIENT_ID, client_id)
    db.set_setting(conn, KEY_OAUTH_CLIENT_SECRET, client_secret)


# --------------------------------------------------------------------------- #
# OAuth token JSON (raw string; auth.py converts to/from a Credentials object)
# --------------------------------------------------------------------------- #
def get_token_json(conn: sqlite3.Connection):
    """Return the raw OAuth token JSON string, or ``None``."""
    return db.get_setting(conn, KEY_OAUTH_TOKEN_JSON)


def set_token_json(conn: sqlite3.Connection, token_json) -> None:
    """Store (or, with ``None``, clear) the raw OAuth token JSON string."""
    if token_json is None:
        _delete_setting(conn, KEY_OAUTH_TOKEN_JSON)
    else:
        db.set_setting(conn, KEY_OAUTH_TOKEN_JSON, token_json)


# --------------------------------------------------------------------------- #
# Spreadsheet / photo folder ids
# --------------------------------------------------------------------------- #
def get_spreadsheet_id(conn: sqlite3.Connection):
    """Return the linked spreadsheet id, or ``None``."""
    return db.get_setting(conn, KEY_SPREADSHEET_ID)


def set_spreadsheet_id(conn: sqlite3.Connection, v) -> None:
    """Store the linked spreadsheet id."""
    db.set_setting(conn, KEY_SPREADSHEET_ID, v)


def get_photo_folder_id(conn: sqlite3.Connection):
    """Return the linked Drive photo-folder id, or ``None``."""
    return db.get_setting(conn, KEY_PHOTO_FOLDER_ID)


def set_photo_folder_id(conn: sqlite3.Connection, v) -> None:
    """Store the linked Drive photo-folder id."""
    db.set_setting(conn, KEY_PHOTO_FOLDER_ID, v)


# --------------------------------------------------------------------------- #
# Revision watermark
# --------------------------------------------------------------------------- #
def get_last_synced_revision(conn: sqlite3.Connection) -> int:
    """Return the last synced revision (defaults to 0)."""
    raw = db.get_setting(conn, KEY_LAST_SYNCED_REVISION)
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def set_last_synced_revision(conn: sqlite3.Connection, revision: int) -> None:
    """Store the last synced revision."""
    db.set_setting(conn, KEY_LAST_SYNCED_REVISION, str(int(revision)))


# --------------------------------------------------------------------------- #
# Pending flag
# --------------------------------------------------------------------------- #
def is_pending(conn: sqlite3.Connection) -> bool:
    """Whether a sync push is pending (local changes not yet pushed)."""
    return db.get_setting(conn, KEY_PENDING) == "1"


def set_pending(conn: sqlite3.Connection, value: bool) -> None:
    """Set the pending-push flag."""
    db.set_setting(conn, KEY_PENDING, "1" if value else "0")


# --------------------------------------------------------------------------- #
# Linked flag
# --------------------------------------------------------------------------- #
def is_linked(conn: sqlite3.Connection) -> bool:
    """Whether sync is linked: the flag is truthy AND a target sheet is set."""
    if db.get_setting(conn, KEY_LINKED) != "1":
        return False
    return bool(get_spreadsheet_id(conn))


def set_linked(conn: sqlite3.Connection, value: bool) -> None:
    """Set the linked flag."""
    db.set_setting(conn, KEY_LINKED, "1" if value else "0")


# --------------------------------------------------------------------------- #
# Disconnect
# --------------------------------------------------------------------------- #
def clear(conn: sqlite3.Connection) -> None:
    """Remove all sync settings for a Disconnect, preserving the device id."""
    for key in _CLEARABLE_KEYS:
        _delete_setting(conn, key)
