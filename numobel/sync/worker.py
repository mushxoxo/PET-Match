"""Background sync worker: Qt slots that run the synchronous orchestration.

:class:`SyncWorker` is a thin, thread-agnostic :class:`~PySide6.QtCore.QObject`
that wraps the (already-built, fully-tested) :mod:`numobel.sync.engine`
push/pull/conflict orchestration. In production a service (M5b) moves an
instance onto a real :class:`~PySide6.QtCore.QThread` and drives its slots via
queued connections; the loopback OAuth flow and the blocking Sheets/Drive HTTP
calls therefore run OFF the UI thread. Results are reported purely via Qt
signals — the worker never returns values to or touches the UI directly.

The worker is deliberately callable WITHOUT a real thread: every slot can be
invoked synchronously in a test and asserted on via ``QSignalSpy`` / connected
recorders. Each slot is also *total*: it catches every exception and routes it
to a signal through :meth:`SyncWorker._handle_error`, so a slot never raises out
into the event loop (where it would otherwise be swallowed or crash the thread).

sqlite connections cannot cross threads, so the worker opens its OWN connection
lazily, on first use, from the ``db_path`` it is given (the same file the UI
thread uses). It is cached for the life of the worker (i.e. the life of its
thread).

This module stays free of any top-level ``google`` import: the default
backend-factory and authorizer import the google-touching modules lazily, so
importing :mod:`numobel.sync.worker` never pulls in ``googleapiclient``.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from numobel import db
from numobel.sync import engine, errors, state

# --------------------------------------------------------------------------- #
# UI-facing status strings (single source of truth for the status pill).
# --------------------------------------------------------------------------- #
STATUS_CONNECTING = "Connecting…"
STATUS_SYNCED = "Synced"
STATUS_PENDING = "Pending…"
STATUS_OFFLINE = "Offline"
STATUS_ERROR = "Error"
STATUS_DISCONNECTED = "Not connected"


def _default_backend_factory(conn):
    """Build a ``GoogleBackend`` from persisted state (google imported lazily).

    Reads the stored OAuth token JSON, refreshes the credentials, persists the
    refreshed token back if it rotated, and returns a backend bound to the
    linked spreadsheet / photo-folder ids. Raises :class:`errors.AuthError` when
    no token has been stored yet.
    """
    from numobel.sync import auth
    from numobel.sync.google_backend import GoogleBackend

    token_json = state.get_token_json(conn)
    if not token_json:
        raise errors.AuthError("not authenticated: no OAuth token stored")

    creds, fresh = auth.ensure_fresh(token_json)
    if fresh != token_json:
        state.set_token_json(conn, fresh)

    return GoogleBackend(
        creds,
        state.get_spreadsheet_id(conn),
        state.get_photo_folder_id(conn),
    )


def _default_authorizer(client_id: str, client_secret: str) -> str:
    """Run the loopback OAuth flow (google imported lazily); return token JSON."""
    from numobel.sync import auth

    return auth.run_oauth_flow(client_id, client_secret)


class SyncWorker(QObject):
    """QObject whose slots run sync on a background thread, reporting via signals.

    Construct with the DB file ``db_path`` it should open its own connection
    from. ``backend_factory`` (``(conn) -> Backend``) and ``authorizer``
    (``(client_id, client_secret) -> token_json``) default to the real
    google-backed implementations and are injected with fakes in tests.
    """

    statusChanged = Signal(str)
    pullFinished = Signal(dict)
    pushFinished = Signal(int)
    conflictDetected = Signal(dict, dict)
    errored = Signal(str, str)
    offline = Signal()
    online = Signal()

    def __init__(self, db_path, backend_factory=None, authorizer=None, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._backend_factory = backend_factory or _default_backend_factory
        self._authorizer = authorizer or _default_authorizer
        self._conn_cache = None

    # ----------------------------------------------------------------- #
    # Lazy per-thread connection
    # ----------------------------------------------------------------- #
    def _conn(self):
        """Return this worker's sqlite connection, opening it on first use.

        Opened lazily so that, in production, ``connect`` happens INSIDE the
        worker's thread (sqlite connections are not shareable across threads).
        """
        if self._conn_cache is None:
            self._conn_cache = db.connect(self._db_path)
        return self._conn_cache

    # ----------------------------------------------------------------- #
    # Slots
    # ----------------------------------------------------------------- #
    @Slot(str, str)
    def requestConnect(self, client_id: str, client_secret: str) -> None:
        """Authenticate, ensure the spreadsheet, then seed-or-adopt the sheet."""
        self.statusChanged.emit(STATUS_CONNECTING)
        try:
            conn = self._conn()

            state.set_client_credentials(conn, client_id, client_secret)
            token = self._authorizer(client_id, client_secret)
            state.set_token_json(conn, token)

            backend = self._backend_factory(conn)
            ids = backend.ensure_spreadsheet()
            state.set_spreadsheet_id(conn, ids["spreadsheet_id"])
            state.set_photo_folder_id(conn, ids["photo_folder_id"])
            state.set_linked(conn, True)

            # Seed-or-adopt: a sheet that already carries data (revision > 0) is
            # adopted by pulling it down; an empty sheet is seeded with our local
            # catalog by pushing.
            cloud_rev = int(backend.read_meta().get("revision", 0) or 0)
            if cloud_rev > 0:
                r = engine.pull(conn, backend)
                self.pullFinished.emit(
                    {"revision": r.revision, "tables": r.tables, "photos": r.photos}
                )
            else:
                r = engine.push(conn, backend)
                self.pushFinished.emit(r.revision)

            self.online.emit()
            self.statusChanged.emit(STATUS_SYNCED)
        except Exception as exc:  # noqa: BLE001 - slot must never raise
            self._handle_error(exc)

    @Slot()
    def requestPush(self) -> None:
        """Push the local catalog to the cloud."""
        try:
            conn = self._conn()
            backend = self._backend_factory(conn)
            r = engine.push(conn, backend)
            self.pushFinished.emit(r.revision)
            self.online.emit()
            self.statusChanged.emit(STATUS_SYNCED)
        except Exception as exc:  # noqa: BLE001 - slot must never raise
            self._handle_error(exc)

    @Slot()
    def requestPull(self) -> None:
        """Pull the cloud catalog down, replacing the local one."""
        try:
            conn = self._conn()
            backend = self._backend_factory(conn)
            r = engine.pull(conn, backend)
            self.pullFinished.emit(
                {"revision": r.revision, "tables": r.tables, "photos": r.photos}
            )
            self.online.emit()
            self.statusChanged.emit(STATUS_SYNCED)
        except Exception as exc:  # noqa: BLE001 - slot must never raise
            self._handle_error(exc)

    @Slot(str)
    def resolveConflict(self, choice: str) -> None:
        """Resolve a detected conflict by keeping ``"local"`` or ``"cloud"`` edits."""
        try:
            conn = self._conn()
            backend = self._backend_factory(conn)
            result = engine.resolve_conflict(conn, backend, choice)
            if isinstance(result, engine.PushResult):
                self.pushFinished.emit(result.revision)
            elif isinstance(result, engine.PullResult):
                self.pullFinished.emit(
                    {
                        "revision": result.revision,
                        "tables": result.tables,
                        "photos": result.photos,
                    }
                )
            self.online.emit()
            self.statusChanged.emit(STATUS_SYNCED)
        except Exception as exc:  # noqa: BLE001 - slot must never raise
            self._handle_error(exc)

    @Slot()
    def requestDisconnect(self) -> None:
        """Clear all sync state and report the disconnected status."""
        try:
            conn = self._conn()
            state.clear(conn)
            state.set_linked(conn, False)  # explicit/safe; clear() already drops it
            self.statusChanged.emit(STATUS_DISCONNECTED)
        except Exception as exc:  # noqa: BLE001 - slot must never raise
            self._handle_error(exc)

    # ----------------------------------------------------------------- #
    # Shared exception classifier
    # ----------------------------------------------------------------- #
    def _handle_error(self, exc) -> None:
        """Route an exception to the right signal + status (never re-raises)."""
        if isinstance(exc, errors.ConflictError):
            # A conflict is unresolved-pending, awaiting the user's choice — not
            # an error.
            self.conflictDetected.emit(
                {"revision": exc.local_revision},
                {"revision": exc.cloud_revision},
            )
            self.statusChanged.emit(STATUS_PENDING)
        elif isinstance(exc, errors.AuthError):
            self.errored.emit("auth", str(exc))
            self.statusChanged.emit(STATUS_ERROR)
        elif isinstance(exc, errors.SheetMissingError):
            self.errored.emit("sheet_missing", str(exc))
            self.statusChanged.emit(STATUS_ERROR)
        elif errors.is_offline_error(exc):
            self.offline.emit()
            self.statusChanged.emit(STATUS_OFFLINE)
        else:
            self.errored.emit("error", f"{type(exc).__name__}: {exc}")
            self.statusChanged.emit(STATUS_ERROR)
