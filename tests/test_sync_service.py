"""Tests for the M5b SyncService facade (offscreen Qt, no pytest-qt).

The service owns a worker thread, the debounce/retry timers, the mutation
listener, and the offline notify-once FSM. FSM-only tests drive the internal
handlers directly with ``start=False`` (no worker thread). One integration test
runs the real thread end-to-end against a FakeBackend, polling with a timeout.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel import db, mutations  # noqa: E402
from numobel.sync import state  # noqa: E402
from numobel.sync.service import SyncService  # noqa: E402
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


def _mark_linked(path):
    """Persist a linked state (flag + spreadsheet id) so is_linked() is True."""
    conn = db.connect(str(path))
    state.set_linked(conn, True)
    state.set_spreadsheet_id(conn, "fake-spreadsheet")
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# notify-once FSM (drive handlers directly, start=False)
# --------------------------------------------------------------------------- #
def test_offline_notice_once_and_rearm(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    svc = SyncService(str(db_path), start=False)
    try:
        notices = []
        svc.offlineNotice.connect(lambda: notices.append(1))

        svc._on_offline()
        svc._on_offline()
        assert len(notices) == 1  # notified exactly once per episode
        assert svc._retry.isActive()

        svc._on_online()
        assert svc._offline_notified is False
        assert not svc._retry.isActive()

        svc._on_offline()  # re-armed after going online
        assert len(notices) == 2
    finally:
        svc.shutdown()


def test_push_finished_resets_offline(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    svc = SyncService(str(db_path), start=False)
    try:
        pushes = []
        svc.pushFinished.connect(lambda r: pushes.append(r))

        svc._on_offline()
        assert svc._offline_notified is True
        assert svc._retry.isActive()

        svc._on_push_finished(5)
        assert svc._offline_notified is False
        assert not svc._retry.isActive()
        assert pushes == [5]
    finally:
        svc.shutdown()


def test_pull_finished_resets_offline(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    svc = SyncService(str(db_path), start=False)
    try:
        pulls = []
        svc.pullFinished.connect(lambda d: pulls.append(d))

        svc._on_offline()
        svc._on_pull_finished({"revision": 3})
        assert svc._offline_notified is False
        assert not svc._retry.isActive()
        assert pulls == [{"revision": 3}]
    finally:
        svc.shutdown()


def test_disconnect_stops_retry_timer(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    _mark_linked(db_path)
    svc = SyncService(str(db_path), retry_ms=20, start=False)
    try:
        fires = []
        svc._pushRequested.connect(lambda: fires.append(1))

        # Go offline: retry timer starts and the user is notified once.
        svc._on_offline()
        assert svc._retry.isActive() is True
        assert svc._offline_notified is True

        # User disconnects: timers must be torn down immediately.
        svc.disconnect()
        assert svc._retry.isActive() is False
        assert svc._debounce.isActive() is False
        assert svc._offline_notified is False

        # Prove no further push fires after disconnect.
        fires.clear()
        QTest.qWait(80)
        QApplication.processEvents()
        assert fires == []
    finally:
        svc.shutdown()


# --------------------------------------------------------------------------- #
# debounce coalescing
# --------------------------------------------------------------------------- #
def test_debounce_coalesces_mutations(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    _mark_linked(db_path)
    svc = SyncService(str(db_path), debounce_ms=50, start=False)
    try:
        fires = []
        svc._pushRequested.connect(lambda: fires.append(1))

        for _ in range(5):
            svc._on_mutation("update_product", "product", 1)

        QTest.qWait(150)
        QApplication.processEvents()

        assert len(fires) == 1  # coalesced into a single push

        conn = db.connect(str(db_path))
        assert state.is_pending(conn) is True  # persisted
        conn.close()
    finally:
        svc.shutdown()


def test_unlinked_mutation_ignored(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)  # NOT linked
    svc = SyncService(str(db_path), debounce_ms=50, start=False)
    try:
        fires = []
        svc._pushRequested.connect(lambda: fires.append(1))

        svc._on_mutation("update_product", "product", 1)
        QTest.qWait(150)
        QApplication.processEvents()

        assert fires == []  # no debounce timer started
        assert not svc._debounce.isActive()

        conn = db.connect(str(db_path))
        assert state.is_pending(conn) is False  # nothing persisted
        conn.close()
    finally:
        svc.shutdown()


# --------------------------------------------------------------------------- #
# listener lifecycle
# --------------------------------------------------------------------------- #
def test_shutdown_unregisters_listener_and_idempotent(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    svc = SyncService(str(db_path), start=False)

    assert svc._on_mutation in mutations._listeners
    svc.shutdown()
    assert svc._on_mutation not in mutations._listeners
    # Idempotent: a second shutdown must not raise.
    svc.shutdown()


# --------------------------------------------------------------------------- #
# link / list public methods reach the worker slots (start=False)
# --------------------------------------------------------------------------- #
def test_link_and_list_methods_emit_internal_requests(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    svc = SyncService(str(db_path), start=False)
    try:
        links = []
        lists = []
        svc._linkRequested.connect(lambda s: links.append(s))
        svc._listRequested.connect(lambda: lists.append(1))

        svc.link_spreadsheet("sheet-abc")
        svc.link_spreadsheet()  # default: create-new
        svc.list_spreadsheets()

        assert links == ["sheet-abc", ""]
        assert lists == [1]
    finally:
        svc.shutdown()


# --------------------------------------------------------------------------- #
# worker -> service relays (needsSpreadsheet / spreadsheetsListed)
# --------------------------------------------------------------------------- #
def test_needs_and_listed_relayed_then_severed_on_shutdown(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    svc = SyncService(str(db_path), start=False)
    needs = []
    listed = []
    svc.needsSpreadsheet.connect(lambda: needs.append(1))
    svc.spreadsheetsListed.connect(lambda rows: listed.append(rows))

    # Relayed while live.
    svc._worker.needsSpreadsheet.emit()
    svc._worker.spreadsheetsListed.emit([{"id": "x"}])
    QApplication.processEvents()
    assert needs == [1]
    assert listed == [[{"id": "x"}]]

    # After shutdown, the relays are severed: further worker emissions do not
    # reach the service's public signals.
    svc.shutdown()
    svc._worker.needsSpreadsheet.emit()
    svc._worker.spreadsheetsListed.emit([{"id": "y"}])
    QApplication.processEvents()
    assert needs == [1]
    assert listed == [[{"id": "x"}]]


# --------------------------------------------------------------------------- #
# thread integration (start=True)
# --------------------------------------------------------------------------- #
def test_thread_integration_connect_then_link_relays(app, tmp_path):
    db_path = tmp_path / "numobel.db"
    _seed_db(db_path)
    fake = FakeBackend()
    svc = SyncService(
        str(db_path),
        backend_factory=lambda conn: fake,
        authorizer=lambda cid, csec: '{"token": "fake"}',
        start=True,
    )
    try:
        needs = []
        done = []
        svc.needsSpreadsheet.connect(lambda: needs.append(1))
        svc.pushFinished.connect(lambda r: done.append(("push", r)))
        svc.pullFinished.connect(lambda d: done.append(("pull", d)))

        # Fresh connect: auth only, then the worker asks the UI to pick a sheet.
        svc.connect("client-id", "client-secret")
        for _ in range(40):  # up to ~2s
            if needs:
                break
            QTest.qWait(50)
            QApplication.processEvents()
        assert needs, "expected needsSpreadsheet to be relayed"

        # UI drives the link (create-new): seeds + links.
        svc.link_spreadsheet("")
        for _ in range(40):
            if done:
                break
            QTest.qWait(50)
            QApplication.processEvents()
        assert done, "expected a push/pull after linking"

        conn = db.connect(str(db_path))
        assert state.is_linked(conn) is True
        conn.close()
    finally:
        svc.shutdown()
