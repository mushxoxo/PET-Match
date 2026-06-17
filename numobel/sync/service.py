"""UI-facing sync facade: owns the worker thread, timers, and offline FSM.

:class:`SyncService` is the single object the UI talks to. It hides the
threading: a :class:`~numobel.sync.worker.SyncWorker` is moved onto a private
:class:`~PySide6.QtCore.QThread`, and every public method merely *emits* an
internal request signal that is connected to the matching worker slot with a
``Qt.QueuedConnection`` — the canonical, thread-safe way to invoke a slot on
another thread's event loop. Results come back as the worker's signals, which
this service relays (or post-processes through small FSM handlers) to its own
public signals.

Responsibilities beyond plain relaying:

* **Debounce/coalescing.** A burst of catalog edits (each firing the mutation
  hook) is collapsed into a single push via a single-shot debounce timer.
* **Retry.** While offline, a periodic timer re-attempts the push until the
  worker reports it came back online.
* **Offline notify-once FSM.** The user is told "you're offline" exactly once
  per offline episode; the notice is re-armed only after a successful
  online/push/pull.

This module keeps the worker's no-top-level-``google`` discipline: it imports
only ``worker``/``mutations``/``state``/``db`` and PySide6.
"""

from __future__ import annotations

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, Signal

from numobel import db, mutations
from numobel.sync import state
from numobel.sync.worker import STATUS_DISCONNECTED, SyncWorker


class SyncService(QObject):
    """Owns the sync worker thread + timers + mutation listener + offline FSM."""

    # -- Public, UI-facing signals (relayed/derived from the worker) -------- #
    statusChanged = Signal(str)
    pullFinished = Signal(dict)
    pushFinished = Signal(int)
    conflictDetected = Signal(dict, dict)
    errored = Signal(str, str)
    #: Emitted EXACTLY ONCE per offline episode; re-armed after going online.
    offlineNotice = Signal()
    #: Auth succeeded on a fresh connect: the UI must drive spreadsheet selection.
    needsSpreadsheet = Signal()
    #: Carries adoptable NUMOBEL spreadsheet rows for the UI to offer.
    spreadsheetsListed = Signal(list)

    # -- Internal request signals (queued, cross-thread, -> worker slots) --- #
    _connectRequested = Signal(str, str)
    _pushRequested = Signal()
    _pullRequested = Signal()
    _resolveRequested = Signal(str)
    _disconnectRequested = Signal()
    _linkRequested = Signal(str)
    _listRequested = Signal()

    def __init__(
        self,
        db_path,
        backend_factory=None,
        authorizer=None,
        debounce_ms=2500,
        retry_ms=30000,
        start=True,
        parent=None,
    ):
        super().__init__(parent)
        self._db_path = db_path

        # Worker on its own thread (sqlite connections cannot cross threads, so
        # the worker opens its own from the same file lazily on its thread).
        self._thread = QThread(self)
        self._worker = SyncWorker(db_path, backend_factory, authorizer)
        self._worker.moveToThread(self._thread)

        # Requests -> worker slots (queued so they run on the worker's thread).
        self._connectRequested.connect(
            self._worker.requestConnect, Qt.QueuedConnection
        )
        self._pushRequested.connect(self._worker.requestPush, Qt.QueuedConnection)
        self._pullRequested.connect(self._worker.requestPull, Qt.QueuedConnection)
        self._resolveRequested.connect(
            self._worker.resolveConflict, Qt.QueuedConnection
        )
        self._disconnectRequested.connect(
            self._worker.requestDisconnect, Qt.QueuedConnection
        )
        self._linkRequested.connect(
            self._worker.requestLinkSpreadsheet, Qt.QueuedConnection
        )
        self._listRequested.connect(
            self._worker.requestListSpreadsheets, Qt.QueuedConnection
        )

        # Worker signals -> public relays / FSM handlers.
        self._worker.statusChanged.connect(self.statusChanged)
        self._worker.statusChanged.connect(self._on_status_changed)
        self._worker.conflictDetected.connect(self.conflictDetected)
        self._worker.errored.connect(self.errored)
        self._worker.pullFinished.connect(self._on_pull_finished)
        self._worker.pushFinished.connect(self._on_push_finished)
        self._worker.offline.connect(self._on_offline)
        self._worker.online.connect(self._on_online)
        self._worker.needsSpreadsheet.connect(self.needsSpreadsheet)
        self._worker.spreadsheetsListed.connect(self.spreadsheetsListed)

        # Timers live on the creating (UI) thread; owned by the service.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(debounce_ms)
        self._debounce.timeout.connect(self._fire_push)

        self._retry = QTimer(self)
        self._retry.setInterval(retry_ms)
        self._retry.timeout.connect(self._fire_push)

        # Offline notify-once latch.
        self._offline_notified = False

        # Listen for committed catalog writes (fires on the UI thread).
        mutations.register_listener(self._on_mutation)
        self._listener_registered = True

        # Worker->service relays are live; severed exactly once in shutdown().
        self._relays_connected = True

        if start:
            self._thread.start()

    # --------------------------------------------------------------------- #
    # Public methods (thin: each emits the matching request signal so the
    # actual work runs on the worker thread).
    # --------------------------------------------------------------------- #
    def connect(self, client_id="", client_secret=""):
        """Authenticate + link the catalog to a spreadsheet (on the worker).

        Call with no arguments to use the bundled OAuth client; pass an explicit
        id/secret only for the manual (advanced) fallback path.
        """
        self._connectRequested.emit(client_id, client_secret)

    def link_spreadsheet(self, spreadsheet_id_or_empty=""):
        """Link the authenticated catalog to a spreadsheet (on the worker).

        Pass an empty string to create a brand-new sheet; pass an id or full
        Sheets URL to adopt an existing NUMOBEL spreadsheet.
        """
        self._linkRequested.emit(spreadsheet_id_or_empty)

    def list_spreadsheets(self):
        """Request the list of adoptable NUMOBEL spreadsheets (on the worker)."""
        self._listRequested.emit()

    def pull(self):
        """Pull the cloud catalog down (on the worker)."""
        self._pullRequested.emit()

    def push(self):
        """Push the local catalog up (on the worker)."""
        self._pushRequested.emit()

    def resolve_conflict(self, choice):
        """Resolve a detected conflict by keeping ``"local"`` or ``"cloud"``."""
        self._resolveRequested.emit(choice)

    def disconnect(self):
        """Clear all sync state (on the worker).

        Tears down local sync timing immediately on this (UI) thread: the call
        is user-initiated here, so stopping the timers is thread-correct. This
        guarantees no debounced or retried push fires once the user has
        disconnected — the worker's ``requestDisconnect`` clears the token and
        emits only ``statusChanged("Not connected")``, so it never trips the
        online/push/pull handlers that would otherwise stop ``_retry``.
        """
        self._stop_timers()
        self._disconnectRequested.emit()

    def shutdown(self):
        """Stop timers, unregister the listener, and join the thread.

        Idempotent — safe to call more than once (e.g. from teardown after an
        earlier explicit shutdown).
        """
        if self._listener_registered:
            mutations.unregister_listener(self._on_mutation)
            self._listener_registered = False
        self._stop_timers()
        # Sever the internal worker->handler relays so a late queued emission
        # (e.g. from a slot already mid-flight on the worker thread) can't deliver
        # into a torn-down consumer. Idempotent: each disconnect is wrapped so a
        # second shutdown() (where they're already severed) stays safe. The public
        # service signals themselves are left intact.
        self._disconnect_worker_relays()
        if self._thread.isRunning():
            # Close the worker's sqlite connection ON ITS OWN THREAD before the
            # event loop exits: a queued `close` runs on the worker thread, and
            # wait() (below) blocks until the thread — and thus that queued call —
            # has finished. This checkpoints the WAL instead of letting the
            # connection be GC'd uncleanly after the thread quits.
            QMetaObject.invokeMethod(self._worker, "close", Qt.QueuedConnection)
            self._thread.quit()
            self._thread.wait()
        else:
            # Thread never started (e.g. start=False): close directly here.
            self._worker.close()

    def _disconnect_worker_relays(self):
        """Disconnect the worker->service signal relays (idempotent).

        Gated on ``_relays_connected`` so a second ``shutdown()`` skips the work
        entirely (Qt only *warns* — it doesn't raise — on a redundant disconnect,
        so the latch is what actually keeps it quiet). The try/except is a belt-
        and-braces guard for any single relay Qt still rejects.
        """
        if not self._relays_connected:
            return
        self._relays_connected = False
        relays = (
            (self._worker.statusChanged, self.statusChanged),
            (self._worker.statusChanged, self._on_status_changed),
            (self._worker.conflictDetected, self.conflictDetected),
            (self._worker.errored, self.errored),
            (self._worker.pullFinished, self._on_pull_finished),
            (self._worker.pushFinished, self._on_push_finished),
            (self._worker.offline, self._on_offline),
            (self._worker.online, self._on_online),
            (self._worker.needsSpreadsheet, self.needsSpreadsheet),
            (self._worker.spreadsheetsListed, self.spreadsheetsListed),
        )
        for signal, slot in relays:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                # Qt raises if the connection was already severed (e.g. a second
                # shutdown() call) — safe to ignore.
                pass

    def _stop_timers(self):
        """Stop both timers and re-arm the offline notice (UI-thread only)."""
        self._debounce.stop()
        self._retry.stop()
        self._offline_notified = False

    # --------------------------------------------------------------------- #
    # FSM / internal handlers
    # --------------------------------------------------------------------- #
    def _on_mutation(self, action, entity, entity_id):
        """A catalog write committed (UI thread): persist pending + debounce.

        Unlinked catalogs do nothing. Guarded so a sync hiccup can never break
        a catalog write (mirrors ``mutations._notify``'s own swallowing).
        """
        try:
            c = db.connect(self._db_path)
            try:
                if not state.is_linked(c):
                    return
                state.set_pending(c, True)
            finally:
                c.close()
            self._debounce.start()  # coalesce a burst into one push
        except Exception:  # noqa: BLE001 - sync must never break a write
            pass

    def _on_status_changed(self, status):
        """Guard: a worker-initiated disconnect must also stop local timers."""
        if status == STATUS_DISCONNECTED:
            self._stop_timers()

    def _fire_push(self):
        """Debounce or retry timer fired: request a push on the worker."""
        self._pushRequested.emit()

    def _on_offline(self):
        """Worker reported offline: keep retrying, and notify the user once."""
        self._retry.start()
        if not self._offline_notified:
            self._offline_notified = True
            self.offlineNotice.emit()

    def _on_online(self):
        """Worker reported online: re-arm the notice and stop retrying."""
        self._offline_notified = False
        self._retry.stop()

    def _on_push_finished(self, revision):
        """Push succeeded (implies online): reset offline state, then relay."""
        self._offline_notified = False
        self._retry.stop()
        self.pushFinished.emit(revision)

    def _on_pull_finished(self, summary):
        """Pull succeeded (implies online): reset offline state, then relay."""
        self._offline_notified = False
        self._retry.stop()
        self.pullFinished.emit(summary)
