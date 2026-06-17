"""Headless tests for wiring the Google SyncService into the UI (M6).

Runs Qt offscreen. The service is constructed with ``start=False`` (no real
worker thread) and a faked backend/authorizer; the UI is driven by emitting the
service's PUBLIC signals directly and by calling the window's handlers. Existing
local import/export flows are exercised by other test modules and must stay
green — here we only cover the sync wiring and the no-service back-compat path.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from numobel import db  # noqa: E402
from numobel.sync import state  # noqa: E402
from numobel.sync.service import SyncService  # noqa: E402
from numobel.ui.main_window import _ONBOARDING, _SHELL, MainWindow  # noqa: E402
from tests.sync_fakes import FakeBackend  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "numobel.db")


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    db.create_schema(c)
    c.commit()
    yield c
    c.close()


def _seed_catalog(conn):
    conn.execute(
        "INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)"
    )
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name) VALUES (10,1,'AT5','Aqua')"
    )
    conn.commit()


@pytest.fixture
def service(db_path):
    svc = SyncService(
        db_path,
        backend_factory=lambda c: FakeBackend(),
        authorizer=lambda a, b: '{"token":"x"}',
        start=False,
    )
    yield svc
    svc.shutdown()


@pytest.fixture(autouse=True)
def _silence_dialogs(monkeypatch):
    """Keep modal dialogs from blocking headless runs (overridable per-test)."""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )


# --------------------------------------------------------------------------- #
# Back-compat: no service
# --------------------------------------------------------------------------- #
def test_no_service_constructs_and_google_menu_safe(app, conn):
    window = MainWindow(conn)
    try:
        assert window._sync is None
        # The &Google menu exists.
        titles = [a.text() for a in window.menuBar().actions()]
        assert any("Google" in t for t in titles)
        # Triggering Google actions must not crash.
        window._connect_google()
        window._google_pull()
        window._google_push()
        window._google_disconnect()
        window._google_from_onboarding()
        window.onboarding.google_requested.emit()
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# Status bar
# --------------------------------------------------------------------------- #
def test_status_label_updates_on_status_changed(app, conn, service):
    window = MainWindow(conn, sync_service=service)
    try:
        service.statusChanged.emit("Offline")
        assert window._sync_status.text() == "Offline"
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# Pull refresh
# --------------------------------------------------------------------------- #
def test_pull_finished_refreshes_and_shows_shell(app, conn, service):
    _seed_catalog(conn)
    window = MainWindow(conn, sync_service=service)
    try:
        service.pullFinished.emit({"revision": 1, "tables": {}, "photos": 0})
        assert window._outer.currentIndex() == _SHELL
    finally:
        window.close()


def test_pull_finished_empty_catalog_shows_onboarding(app, conn, service):
    window = MainWindow(conn, sync_service=service)
    try:
        service.pullFinished.emit({"revision": 1, "tables": {}, "photos": 0})
        assert window._outer.currentIndex() == _ONBOARDING
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# Conflict dialog
# --------------------------------------------------------------------------- #
def _patch_conflict_choice(monkeypatch, label):
    """Make the conflict QMessageBox.exec() pick the button matching ``label``."""
    chosen = {}

    def fake_exec(self):
        for btn in self.buttons():
            if btn.text() == label:
                chosen["btn"] = btn
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec, raising=False)
    monkeypatch.setattr(
        QMessageBox, "clickedButton", lambda self: chosen.get("btn"), raising=False
    )


def test_conflict_keep_local(app, conn, service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "resolve_conflict", lambda c: calls.append(c))
    _patch_conflict_choice(monkeypatch, "Keep my version")
    window = MainWindow(conn, sync_service=service)
    try:
        service.conflictDetected.emit({"revision": 1}, {"revision": 3})
        assert calls == ["local"]
    finally:
        window.close()


def test_conflict_keep_cloud(app, conn, service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "resolve_conflict", lambda c: calls.append(c))
    _patch_conflict_choice(monkeypatch, "Keep cloud version")
    window = MainWindow(conn, sync_service=service)
    try:
        service.conflictDetected.emit({"revision": 1}, {"revision": 3})
        assert calls == ["cloud"]
    finally:
        window.close()


def test_conflict_dismissed_leaves_pending(app, conn, service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "resolve_conflict", lambda c: calls.append(c))
    # exec() returns with no clicked button -> dismissed.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0, raising=False)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None, raising=False)
    window = MainWindow(conn, sync_service=service)
    try:
        service.conflictDetected.emit({"revision": 1}, {"revision": 3})
        assert calls == []
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# Offline notice
# --------------------------------------------------------------------------- #
def test_offline_notice_shows_info(app, conn, service, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a))
    )
    window = MainWindow(conn, sync_service=service)
    try:
        service.offlineNotice.emit()
        assert len(shown) == 1
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_auth_error_warns(app, conn, service, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a))
    )
    window = MainWindow(conn, sync_service=service)
    try:
        service.errored.emit("auth", "token expired")
        assert len(shown) == 1
        # The auth wording mentions reconnecting.
        assert "Reconnect" in shown[0][1]
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# Onboarding "Load from Google…"
# --------------------------------------------------------------------------- #
def test_onboarding_google_not_linked_collects_credentials(
    app, conn, service, monkeypatch
):
    called = []
    # Force the manual-paste fallback (no bundled client) for this test.
    monkeypatch.setattr(
        "numobel.sync.oauth_client.has_bundled_client", lambda: False
    )
    monkeypatch.setattr(
        MainWindow,
        "_collect_google_credentials",
        lambda self: called.append(True) or None,
    )
    window = MainWindow(conn, sync_service=service)
    try:
        assert not state.is_linked(conn)
        window.onboarding.google_requested.emit()
        assert called == [True]
    finally:
        window.close()


def test_onboarding_google_linked_pulls(app, conn, service, monkeypatch):
    pulls = []
    monkeypatch.setattr(service, "pull", lambda: pulls.append(True))
    state.set_spreadsheet_id(conn, "sheet-123")
    state.set_linked(conn, True)
    window = MainWindow(conn, sync_service=service)
    try:
        assert state.is_linked(conn)
        window.onboarding.google_requested.emit()
        assert pulls == [True]
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# Connect dialog credential collection
# --------------------------------------------------------------------------- #
def test_connect_uses_collected_credentials(app, conn, service, monkeypatch):
    connects = []
    monkeypatch.setattr(
        "numobel.sync.oauth_client.has_bundled_client", lambda: False
    )
    monkeypatch.setattr(service, "connect", lambda i, s: connects.append((i, s)))
    monkeypatch.setattr(
        MainWindow, "_collect_google_credentials", lambda self: ("id-1", "secret-1")
    )
    window = MainWindow(conn, sync_service=service)
    try:
        window._connect_google()
        assert connects == [("id-1", "secret-1")]
    finally:
        window.close()


def test_connect_cancel_does_nothing(app, conn, service, monkeypatch):
    connects = []
    monkeypatch.setattr(
        "numobel.sync.oauth_client.has_bundled_client", lambda: False
    )
    monkeypatch.setattr(service, "connect", lambda i, s: connects.append((i, s)))
    monkeypatch.setattr(
        MainWindow, "_collect_google_credentials", lambda self: None
    )
    window = MainWindow(conn, sync_service=service)
    try:
        window._connect_google()
        assert connects == []
    finally:
        window.close()


def test_connect_uses_bundled_client_without_dialog(app, conn, service, monkeypatch):
    """With a bundled client, Connect opens the browser with NO blocking dialog."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        "numobel.sync.oauth_client.has_bundled_client", lambda: True
    )
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a))

    collected = []
    monkeypatch.setattr(
        MainWindow,
        "_collect_google_credentials",
        lambda self: collected.append(True) or ("x", "y"),
    )
    connects = []
    monkeypatch.setattr(
        service, "connect", lambda *a: connects.append(a)
    )
    window = MainWindow(conn, sync_service=service)
    try:
        window._connect_google()
        # No paste dialog, NO blocking info box, and connect() fired immediately
        # with no creds (the worker resolves the bundled client).
        assert collected == []
        assert infos == []
        assert connects == [()]
    finally:
        window.close()


# --------------------------------------------------------------------------- #
# closeEvent
# --------------------------------------------------------------------------- #
def test_close_event_shuts_down_service(app, conn, service, monkeypatch):
    calls = []
    real_shutdown = service.shutdown
    monkeypatch.setattr(
        service, "shutdown", lambda: (calls.append(True), real_shutdown())[1]
    )
    window = MainWindow(conn, sync_service=service)
    window.close()
    assert calls and calls[0] is True


# --------------------------------------------------------------------------- #
# Startup sync decision (app._startup_sync)
# --------------------------------------------------------------------------- #
class _SyncRecorder:
    """Records push/pull calls without needing a real SyncService."""

    def __init__(self):
        self.pushes = 0
        self.pulls = 0

    def push(self):
        self.pushes += 1

    def pull(self):
        self.pulls += 1


def test_startup_sync_linked_pending_pushes(app, conn):
    state.set_spreadsheet_id(conn, "sheet-1")
    state.set_linked(conn, True)
    state.set_pending(conn, True)
    rec = _SyncRecorder()
    from numobel import app as numobel_app

    numobel_app._startup_sync(rec, conn)
    assert (rec.pushes, rec.pulls) == (1, 0)


def test_startup_sync_linked_not_pending_pulls(app, conn):
    state.set_spreadsheet_id(conn, "sheet-1")
    state.set_linked(conn, True)
    state.set_pending(conn, False)
    rec = _SyncRecorder()
    from numobel import app as numobel_app

    numobel_app._startup_sync(rec, conn)
    assert (rec.pushes, rec.pulls) == (0, 1)


def test_startup_sync_not_linked_does_nothing(app, conn):
    assert not state.is_linked(conn)
    rec = _SyncRecorder()
    from numobel import app as numobel_app

    numobel_app._startup_sync(rec, conn)
    assert (rec.pushes, rec.pulls) == (0, 0)
