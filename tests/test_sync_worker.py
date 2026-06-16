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
# connect
# --------------------------------------------------------------------------- #
def test_connect_seeds_empty_sheet(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    fake = FakeBackend()
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestConnect("client-id", "client-secret")
    QApplication.processEvents()

    pushes = [e for e in events if e[0] == "push"]
    assert pushes and pushes[-1][1] == 1  # revision 1
    assert "online" in _kinds(events)
    assert _last_status(events) == worker.STATUS_SYNCED
    assert "error" not in _kinds(events)

    conn = w._conn()
    assert state.is_linked(conn) is True
    assert state.get_spreadsheet_id(conn) == fake.spreadsheet_id
    assert state.get_photo_folder_id(conn) == fake.photo_folder_id
    assert state.get_token_json(conn) == '{"token": "fake"}'


def test_connect_adopts_populated_sheet(app, tmp_path):
    # Build a populated sheet from a separate db.
    other = tmp_path / "other.db"
    _seed_db(other)
    other_conn = db.connect(str(other))
    fake = FakeBackend()
    engine.push(other_conn, fake)
    engine.push(other_conn, fake)
    engine.push(other_conn, fake)
    other_conn.close()
    assert fake.meta["revision"] == "3"

    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    events = _capture(w)

    w.requestConnect("id", "sec")
    QApplication.processEvents()

    pulls = [e for e in events if e[0] == "pull"]
    assert pulls and pulls[-1][1]["revision"] == 3
    assert "push" not in _kinds(events)
    assert _last_status(events) == worker.STATUS_SYNCED


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #
def _connected_worker(tmp_path, fake):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    w = worker.SyncWorker(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
    )
    w.requestConnect("id", "sec")
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
