"""Tests for the M5a background SyncWorker (signal-based sync slots).

The worker is exercised WITHOUT a real QThread: its slots are invoked
synchronously and emissions are captured with ``QSignalSpy``. Follows the
repo's offscreen-Qt convention (no pytest-qt). A temp FILE db is used so the
worker's own lazily-opened connection persists state across slot calls.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel import db  # noqa: E402
from numobel.sync import engine, state, worker  # noqa: E402
from numobel.sync.errors import AuthError  # noqa: E402
from tests.sync_fakes import FakeBackend  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate_images(tmp_path, monkeypatch):
    """Keep photo logic self-contained in a tmp dir (no photos -> empty maps)."""
    images = tmp_path / "images"
    images.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(db, "images_dir", lambda: images)
    yield


def _seed_db(path):
    """Create a small catalog (brand + products) at ``path`` and close it."""
    conn = db.connect(str(path))
    db.create_schema(conn)
    conn.executescript(
        """
        INSERT INTO brands(id, code, name, has_sheet) VALUES (1, 'AT', 'Acme', 1);
        INSERT INTO products
            (id, brand_id, sku, shade_no, color_name, source_sheet) VALUES
            (100, 1, 'AT05', '5', 'Crimson', 'AT'),
            (101, 1, 'AT06', '6', 'Scarlet', 'AT');
        """
    )
    conn.commit()
    conn.close()


def _link_db(path):
    """Mark the catalog at ``path`` as linked (flag + a target spreadsheet id)."""
    conn = db.connect(str(path))
    state.set_spreadsheet_id(conn, "sheet-xyz")
    state.set_linked(conn, True)
    conn.commit()
    conn.close()


def _capture(worker_obj):
    """Connect every signal to a list-appending recorder; return the record."""
    events: list = []
    worker_obj.statusChanged.connect(lambda s: events.append(("status", s)))
    worker_obj.pullFinished.connect(lambda d: events.append(("pull", d)))
    worker_obj.pushFinished.connect(lambda r: events.append(("push", r)))
    worker_obj.conflictDetected.connect(
        lambda lo, cl: events.append(("conflict", lo, cl))
    )
    worker_obj.errored.connect(lambda k, m: events.append(("error", k, m)))
    worker_obj.offline.connect(lambda: events.append(("offline",)))
    worker_obj.online.connect(lambda: events.append(("online",)))
    return events


def _kinds(events):
    return [e[0] for e in events]


def _last_status(events):
    return [e[1] for e in events if e[0] == "status"][-1]


# --------------------------------------------------------------------------- #
# connect (auth only — linking a spreadsheet is a separate step)
# --------------------------------------------------------------------------- #
def test_connect_unlinked_emits_needs_spreadsheet(app, tmp_path):
    """Unlinked connect authenticates only: stores the token, then asks the UI to
    pick a spreadsheet. It must NOT ensure/seed/adopt or mark the db linked."""
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    fake = FakeBackend()
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)
    needs = []
    w.needsSpreadsheet.connect(lambda: needs.append(1))

    w.requestConnect("client-id", "client-secret")
    QApplication.processEvents()

    assert needs == [1]  # UI asked to pick a spreadsheet
    assert "push" not in _kinds(events)
    assert "pull" not in _kinds(events)
    assert worker.STATUS_SYNCED not in [e[1] for e in events if e[0] == "status"]
    assert "error" not in _kinds(events)

    conn = w._conn()
    assert state.is_linked(conn) is False  # link is a separate step
    assert state.get_token_json(conn) == '{"token": "fake"}'  # token IS stored


def test_connect_already_linked_reconnect_syncs(app, tmp_path):
    """Backward-compat: reconnecting an already-linked catalog seeds-or-adopts by
    revision (pull when cloud is ahead, push when it's empty)."""
    # Cloud ahead -> pull on reconnect.
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    _link_db(db_path)
    pull_fake = FakeBackend()
    pull_fake.meta = {"revision": "4"}
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: pull_fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)
    needs = []
    w.needsSpreadsheet.connect(lambda: needs.append(1))

    w.requestConnect("id", "sec")
    QApplication.processEvents()

    assert needs == []  # already linked: no UI prompt
    pulls = [e for e in events if e[0] == "pull"]
    assert pulls and pulls[-1][1]["revision"] == 4
    assert "online" in _kinds(events)
    assert _last_status(events) == worker.STATUS_SYNCED

    # Empty cloud -> push on reconnect.
    db_path2 = tmp_path / "numobel2.db"
    _seed_db(db_path2)
    _link_db(db_path2)
    push_fake = FakeBackend()  # revision 0
    w2 = worker.SyncWorker(
        str(db_path2),
        backend_factory=lambda conn: push_fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events2 = _capture(w2)

    w2.requestConnect("id", "sec")
    QApplication.processEvents()

    pushes = [e for e in events2 if e[0] == "push"]
    assert pushes and pushes[-1][1] == 1
    assert _last_status(events2) == worker.STATUS_SYNCED


def test_connect_uses_bundled_client_when_creds_blank(app, tmp_path, monkeypatch):
    """Blank creds → resolve the bundled client; don't persist its secret. The
    auth half runs (token stored), then the UI is asked to pick a spreadsheet."""
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    fake = FakeBackend()
    monkeypatch.setattr(
        worker.oauth_client, "get_bundled_client",
        lambda: ("bundled-id", "bundled-secret"),
    )
    seen = []
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: seen.append((cid, csec)) or '{"token": "fake"}',
    )
    events = _capture(w)
    needs = []
    w.needsSpreadsheet.connect(lambda: needs.append(1))

    w.requestConnect("", "")  # the UI's bundled path emits empty creds
    QApplication.processEvents()

    assert seen == [("bundled-id", "bundled-secret")]  # bundled creds were used
    assert needs == [1]  # then asks the UI to pick a spreadsheet
    conn = w._conn()
    assert state.get_token_json(conn) == '{"token": "fake"}'  # token IS stored
    # The bundled secret is NOT persisted to settings (only an explicit paste is).
    assert state.get_client_id(conn) is None
    assert state.get_client_secret(conn) is None


def test_connect_no_creds_and_no_bundled_client_errors(app, tmp_path, monkeypatch):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    monkeypatch.setattr(
        worker.oauth_client, "get_bundled_client", lambda: None
    )
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: FakeBackend(),
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestConnect("", "")
    QApplication.processEvents()

    errors_seen = [e for e in events if e[0] == "error"]
    assert errors_seen and errors_seen[-1][1] == "auth"
    assert _last_status(events) == worker.STATUS_ERROR


# --------------------------------------------------------------------------- #
# requestLinkSpreadsheet (create-new OR adopt-existing)
# --------------------------------------------------------------------------- #
def _authed_db(path):
    """Seed + store an OAuth token (UNLINKED): the post-connect link starting point."""
    _seed_db(path)
    conn = db.connect(str(path))
    state.set_token_json(conn, '{"token": "fake"}')
    conn.commit()
    conn.close()


def test_link_spreadsheet_empty_creates_and_seeds(app, tmp_path):
    """Empty arg → create a brand-new sheet, persist ids, link, push (seed)."""
    db_path = tmp_path / "numobel.db"
    _authed_db(db_path)
    fake = FakeBackend()
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestLinkSpreadsheet("")
    QApplication.processEvents()

    pushes = [e for e in events if e[0] == "push"]
    assert pushes and pushes[-1][1] == 1  # seeded at revision 1
    assert "pull" not in _kinds(events)
    assert "online" in _kinds(events)
    assert _last_status(events) == worker.STATUS_SYNCED
    assert "error" not in _kinds(events)

    conn = w._conn()
    assert state.is_linked(conn) is True
    assert state.get_spreadsheet_id(conn) == fake.spreadsheet_id
    assert state.get_photo_folder_id(conn) == fake.photo_folder_id


def test_link_spreadsheet_id_adopts_full_sheet(app, tmp_path):
    """Non-empty arg over a populated sheet → adopt by pulling it down."""
    db_path = tmp_path / "numobel.db"
    _authed_db(db_path)
    fake = FakeBackend()
    fake.spreadsheet_id = "sheet-abc"
    # Populate the sheet with real data + revision via three pushes from a peer db.
    other = tmp_path / "other.db"
    _seed_db(other)
    other_conn = db.connect(str(other))
    engine.push(other_conn, fake)
    engine.push(other_conn, fake)
    engine.push(other_conn, fake)
    other_conn.close()
    assert fake.meta["revision"] == "3"
    fake.meta["photo_folder_id"] = "folderX"  # adopt resolves the folder from meta

    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestLinkSpreadsheet("sheet-abc")
    QApplication.processEvents()

    pulls = [e for e in events if e[0] == "pull"]
    assert pulls and pulls[-1][1]["revision"] == 3
    assert "push" not in _kinds(events)
    assert _last_status(events) == worker.STATUS_SYNCED

    conn = w._conn()
    assert state.is_linked(conn) is True
    assert state.get_spreadsheet_id(conn) == "sheet-abc"
    assert state.get_photo_folder_id(conn) == "folderX"


def test_link_spreadsheet_id_adopts_empty_sheet_seeds(app, tmp_path):
    """Non-empty arg over an empty (revision 0) sheet → seed by pushing."""
    db_path = tmp_path / "numobel.db"
    _authed_db(db_path)
    fake = FakeBackend()  # revision 0
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestLinkSpreadsheet("sheet-empty")
    QApplication.processEvents()

    pushes = [e for e in events if e[0] == "push"]
    assert pushes and pushes[-1][1] == 1
    assert "pull" not in _kinds(events)
    assert _last_status(events) == worker.STATUS_SYNCED

    conn = w._conn()
    assert state.is_linked(conn) is True
    assert state.get_spreadsheet_id(conn) == "sheet-empty"


def test_link_spreadsheet_foreign_sheet_errors(app, tmp_path):
    """Adopting a non-NUMOBEL sheet errors ('not_numobel') and does NOT link."""
    db_path = tmp_path / "numobel.db"
    _authed_db(db_path)
    fake = FakeBackend()
    fake.adopt_should_reject = True
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestLinkSpreadsheet("foreign-sheet")
    QApplication.processEvents()

    errs = [e for e in events if e[0] == "error"]
    assert errs and errs[-1][1] == "not_numobel"
    assert _last_status(events) == worker.STATUS_ERROR
    conn = w._conn()
    assert state.is_linked(conn) is False


# --------------------------------------------------------------------------- #
# requestListSpreadsheets
# --------------------------------------------------------------------------- #
def test_list_spreadsheets_emits_results(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _authed_db(db_path)
    fake = FakeBackend()
    fake.listed = [{"id": "x", "name": "X", "modifiedTime": "t"}]
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)
    listed = []
    w.spreadsheetsListed.connect(lambda rows: listed.append(rows))

    w.requestListSpreadsheets()
    QApplication.processEvents()

    assert listed == [[{"id": "x", "name": "X", "modifiedTime": "t"}]]
    assert "status" not in _kinds(events)  # no status change on success
    assert "error" not in _kinds(events)


def test_list_spreadsheets_offline_routes_error(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _authed_db(db_path)

    def offline_factory(conn):
        raise ConnectionError("no network")

    w = worker.SyncWorker(
        str(db_path),
        backend_factory=offline_factory,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)
    listed = []
    w.spreadsheetsListed.connect(lambda rows: listed.append(rows))

    w.requestListSpreadsheets()
    QApplication.processEvents()

    assert listed == []
    assert "offline" in _kinds(events)
    assert _last_status(events) == worker.STATUS_OFFLINE


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #
def _connected_worker(tmp_path, fake):
    """Connect + link a fresh catalog (seeds the sheet at revision 1)."""
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    w.requestConnect("id", "sec")  # auth only
    QApplication.processEvents()
    w.requestLinkSpreadsheet("")  # create-new + seed -> linked at revision 1
    QApplication.processEvents()
    return w


def test_push_success(app, tmp_path):
    fake = FakeBackend()
    w = _connected_worker(tmp_path, fake)  # seeds at revision 1
    conn = w._conn()
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, source_sheet) "
        "VALUES (102, 1, 'AT07', 'AT')"
    )
    conn.commit()
    events = _capture(w)

    w.requestPush()
    QApplication.processEvents()

    pushes = [e for e in events if e[0] == "push"]
    assert pushes and pushes[-1][1] == 2  # incremented
    assert _last_status(events) == worker.STATUS_SYNCED
    assert "error" not in _kinds(events)


def test_push_conflict(app, tmp_path):
    fake = FakeBackend()
    w = _connected_worker(tmp_path, fake)  # watermark == 1
    # Force the sheet revision ahead of our watermark.
    fake.meta["revision"] = "5"
    events = _capture(w)

    w.requestPush()
    QApplication.processEvents()

    conflicts = [e for e in events if e[0] == "conflict"]
    assert conflicts, _kinds(events)
    _, local, cloud = conflicts[-1]
    assert local == {"revision": 1}
    assert cloud == {"revision": 5}
    assert _last_status(events) == worker.STATUS_PENDING
    assert "error" not in _kinds(events)


def test_push_offline(app, tmp_path):
    class OfflineBackend(FakeBackend):
        def write_all(self, data):
            raise ConnectionError("no network")

    fake = FakeBackend()
    w = _connected_worker(tmp_path, fake)
    # Swap factory to an offline backend for the push.
    offline_be = OfflineBackend()
    offline_be.meta = dict(fake.meta)  # same revision so no conflict
    w._backend_factory = lambda conn: offline_be
    events = _capture(w)

    # Slot must never raise.
    w.requestPush()
    QApplication.processEvents()

    assert "offline" in _kinds(events)
    assert _last_status(events) == worker.STATUS_OFFLINE
    assert "error" not in _kinds(events)


def test_push_auth_error(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    _link_db(db_path)  # linked: requestPush proceeds to the backend factory

    def boom_factory(conn):
        raise AuthError("re-auth required")

    w = worker.SyncWorker(
        str(db_path),
        backend_factory=boom_factory,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestPush()  # must not raise
    QApplication.processEvents()

    errs = [e for e in events if e[0] == "error"]
    assert errs and errs[-1][1] == "auth"
    assert _last_status(events) == worker.STATUS_ERROR


# --------------------------------------------------------------------------- #
# resolveConflict
# --------------------------------------------------------------------------- #
def test_resolve_conflict_local(app, tmp_path):
    fake = FakeBackend()
    w = _connected_worker(tmp_path, fake)
    fake.meta["revision"] = "5"  # cloud ahead -> divergence
    events = _capture(w)

    w.resolveConflict("local")
    QApplication.processEvents()

    pushes = [e for e in events if e[0] == "push"]
    assert pushes and pushes[-1][1] == 6  # cloud + 1
    assert _last_status(events) == worker.STATUS_SYNCED


def test_resolve_conflict_cloud(app, tmp_path):
    fake = FakeBackend()
    w = _connected_worker(tmp_path, fake)
    fake.meta["revision"] = "5"
    events = _capture(w)

    w.resolveConflict("cloud")
    QApplication.processEvents()

    pulls = [e for e in events if e[0] == "pull"]
    assert pulls and pulls[-1][1]["revision"] == 5
    assert _last_status(events) == worker.STATUS_SYNCED


# --------------------------------------------------------------------------- #
# pull
# --------------------------------------------------------------------------- #
def test_pull_success(app, tmp_path):
    fake = FakeBackend()
    w = _connected_worker(tmp_path, fake)
    events = _capture(w)

    w.requestPull()
    QApplication.processEvents()

    pulls = [e for e in events if e[0] == "pull"]
    assert pulls and pulls[-1][1]["revision"] == 1
    assert "online" in _kinds(events)
    assert _last_status(events) == worker.STATUS_SYNCED


# --------------------------------------------------------------------------- #
# disconnect
# --------------------------------------------------------------------------- #
def test_disconnect(app, tmp_path):
    fake = FakeBackend()
    w = _connected_worker(tmp_path, fake)
    conn = w._conn()
    assert state.is_linked(conn) is True
    events = _capture(w)

    w.requestDisconnect()
    QApplication.processEvents()

    assert _last_status(events) == worker.STATUS_DISCONNECTED
    assert state.is_linked(conn) is False
    assert state.get_token_json(conn) is None


# --------------------------------------------------------------------------- #
# slot never raises (explicit)
# --------------------------------------------------------------------------- #
def test_slot_never_raises_on_backend_explosion(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    _link_db(db_path)  # linked: requestPush reaches the (exploding) factory

    def boom_factory(conn):
        raise RuntimeError("kaboom")

    w = worker.SyncWorker(
        str(db_path),
        backend_factory=boom_factory,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    # Explicit: invoking the slot returns normally despite the explosion.
    w.requestPush()
    QApplication.processEvents()

    errs = [e for e in events if e[0] == "error"]
    assert errs and errs[-1][1] == "error"
    assert _last_status(events) == worker.STATUS_ERROR


# --------------------------------------------------------------------------- #
# requestPush early-returns before a link exists (quiet retry)
# --------------------------------------------------------------------------- #
def test_push_noop_when_not_linked(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)  # NOT linked

    def boom_factory(conn):  # must never be reached
        raise AssertionError("backend factory called while unlinked")

    w = worker.SyncWorker(
        str(db_path),
        backend_factory=boom_factory,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestPush()  # early-returns: no factory, no signals
    QApplication.processEvents()

    assert events == []  # quiet: nothing emitted to spin the retry timer


# --------------------------------------------------------------------------- #
# close() — WAL checkpoint hygiene, idempotent
# --------------------------------------------------------------------------- #
def test_close_is_idempotent_and_nulls_connection(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: FakeBackend(),
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )

    w._conn()  # force the lazy connection open
    assert w._conn_cache is not None

    w.close()
    assert w._conn_cache is None

    # Second close (and a close before any open) must not raise.
    w.close()
    assert w._conn_cache is None
