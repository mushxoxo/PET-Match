"""Tests for sync settings/state helpers (offline, pure-stdlib)."""

from __future__ import annotations

import sqlite3

from numobel import db
from numobel.sync import state


def _make_db() -> sqlite3.Connection:
    conn = db.connect(":memory:")
    db.create_schema(conn)
    return conn


def test_device_id_stable_and_lazy():
    conn = _make_db()
    first = state.get_device_id(conn)
    assert first  # non-empty
    second = state.get_device_id(conn)
    assert first == second  # stable across calls
    # Persisted under the namespaced key.
    assert db.get_setting(conn, state.KEY_DEVICE_ID) == first


def test_client_credentials_round_trip():
    conn = _make_db()
    assert state.get_client_id(conn) is None
    assert state.get_client_secret(conn) is None
    state.set_client_credentials(conn, "cid.apps", "secret-xyz")
    assert state.get_client_id(conn) == "cid.apps"
    assert state.get_client_secret(conn) == "secret-xyz"


def test_token_json_round_trip_and_none():
    conn = _make_db()
    assert state.get_token_json(conn) is None
    state.set_token_json(conn, '{"token": "abc"}')
    assert state.get_token_json(conn) == '{"token": "abc"}'
    state.set_token_json(conn, None)
    assert state.get_token_json(conn) is None


def test_spreadsheet_and_folder_ids():
    conn = _make_db()
    assert state.get_spreadsheet_id(conn) is None
    assert state.get_photo_folder_id(conn) is None
    state.set_spreadsheet_id(conn, "sheet-123")
    state.set_photo_folder_id(conn, "folder-456")
    assert state.get_spreadsheet_id(conn) == "sheet-123"
    assert state.get_photo_folder_id(conn) == "folder-456"


def test_revision_default_and_round_trip():
    conn = _make_db()
    assert state.get_last_synced_revision(conn) == 0
    state.set_last_synced_revision(conn, 7)
    assert state.get_last_synced_revision(conn) == 7


def test_pending_round_trip():
    conn = _make_db()
    assert state.is_pending(conn) is False
    state.set_pending(conn, True)
    assert state.is_pending(conn) is True
    state.set_pending(conn, False)
    assert state.is_pending(conn) is False


def test_linked_requires_flag_and_spreadsheet():
    conn = _make_db()
    assert state.is_linked(conn) is False
    state.set_linked(conn, True)
    # Flag alone is not enough: a link needs a target sheet.
    assert state.is_linked(conn) is False
    state.set_spreadsheet_id(conn, "sheet-123")
    assert state.is_linked(conn) is True
    state.set_linked(conn, False)
    assert state.is_linked(conn) is False


def test_clear_resets_state_but_preserves_device_id():
    conn = _make_db()
    device = state.get_device_id(conn)
    state.set_client_credentials(conn, "cid", "secret")
    state.set_token_json(conn, '{"token": "abc"}')
    state.set_spreadsheet_id(conn, "sheet-123")
    state.set_photo_folder_id(conn, "folder-456")
    state.set_linked(conn, True)
    state.set_pending(conn, True)
    state.set_last_synced_revision(conn, 9)
    assert state.is_linked(conn) is True

    state.clear(conn)

    assert state.is_linked(conn) is False
    assert state.get_token_json(conn) is None
    assert state.get_client_id(conn) is None
    assert state.get_spreadsheet_id(conn) is None
    assert state.get_last_synced_revision(conn) == 0
    assert state.is_pending(conn) is False
    # Device identity survives a disconnect.
    assert state.get_device_id(conn) == device
