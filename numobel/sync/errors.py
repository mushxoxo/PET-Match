"""Sync exception taxonomy + offline/transient classification policy.

This is a *policy* module for the orchestration layer (M4). It lets the sync
loop decide, for any exception bubbling up from a :class:`~numobel.sync.backend.Backend`,
whether to "retry quietly later" (a transient connectivity blip — the user is
simply offline) or to "surface to the user" (auth broken, sheet deleted,
genuine conflict, or an unexpected programming error).

It deliberately imports NO ``google`` / ``googleapiclient`` / ``httplib2``
libraries: it cannot, because M3a must stay offline-testable. Classification is
therefore done by DUCK TYPING — exception *class names* and best-effort HTTP
status extraction — rather than ``isinstance`` against google types. M3b's
``GoogleBackend`` is responsible for either raising these typed errors directly
(``AuthError``/``SheetMissingError``/``ConflictError``) or letting a raw
``HttpError`` propagate so :func:`is_offline_error` can sort it.
"""

from __future__ import annotations

import socket

# --------------------------------------------------------------------------- #
# Exception hierarchy
# --------------------------------------------------------------------------- #
class SyncError(Exception):
    """Base class for all sync-specific errors."""


class ConflictError(SyncError):
    """The cloud revision diverged from our last-synced revision.

    Raised when a push/pull detects that someone else advanced the spreadsheet
    past the revision we last saw, so a blind overwrite would lose their work.
    Carries the two revisions so the orchestration layer can report / merge.
    """

    def __init__(self, local_revision=None, cloud_revision=None, message=None):
        self.local_revision = local_revision
        self.cloud_revision = cloud_revision
        if message is None:
            message = (
                f"cloud revision {cloud_revision!r} diverged from last-synced "
                f"revision {local_revision!r}"
            )
        super().__init__(message)


class AuthError(SyncError):
    """Credentials are missing, invalid, or expired beyond recovery.

    Signals that interactive re-authentication is required; retrying silently
    will never succeed. M3b maps unrecoverable 401/403 / token-refresh failures
    here.
    """


class SheetMissingError(SyncError):
    """The linked spreadsheet was deleted or is otherwise not found (404)."""


class NotNumobelSheetError(SyncError):
    """The selected/pasted spreadsheet isn't a NUMOBEL catalog sheet."""


# --------------------------------------------------------------------------- #
# HTTP status extraction (best-effort, never raises)
# --------------------------------------------------------------------------- #
def http_status_of(exc) -> int | None:
    """Best-effort extract an HTTP status code from an exception, or ``None``.

    Different client libraries expose the status in different shapes. We probe
    the common ones, newest-googleapiclient-first:

    * ``exc.status_code`` (requests-style / many wrappers)
    * ``exc.resp.status`` (googleapiclient ``HttpError``)
    * ``exc.status`` (httplib2 / generic)

    The value is coerced to ``int`` when possible. Any attribute access or
    coercion failure is swallowed and yields ``None`` — this helper must be
    safe to call on *any* object.
    """
    for getter in (
        lambda e: getattr(e, "status_code", None),
        lambda e: getattr(getattr(e, "resp", None), "status", None),
        lambda e: getattr(e, "status", None),
    ):
        try:
            value = getter(exc)
        except Exception:  # pragma: no cover - defensive only
            value = None
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


# HTTP statuses we treat as transient (worth a quiet retry).
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# Exception class names (from libraries we don't import) that mean "no network".
_OFFLINE_CLASS_NAMES = frozenset({"ServerNotFoundError", "TransportError"})


def is_offline_error(exc) -> bool:
    """Return ``True`` for transient connectivity errors worth a quiet retry.

    Treated as offline/transient:

    * stdlib ``socket.gaierror`` (DNS resolution failed),
    * ``ConnectionError`` and its subclasses (refused/reset/aborted),
    * ``TimeoutError`` (and the alias ``socket.timeout``),
    * any exception whose class name is in :data:`_OFFLINE_CLASS_NAMES`
      (``httplib2.ServerNotFoundError`` / ``google.auth`` ``TransportError`` —
      matched by name since we can't import them here),
    * any exception carrying a transient HTTP status (429/500/502/503/504).

    Everything else (including a 404 or a plain ``ValueError``) returns
    ``False`` so it surfaces to the user.
    """
    # socket.gaierror is a subclass of OSError, so check it explicitly first.
    if isinstance(exc, (socket.gaierror, ConnectionError, TimeoutError)):
        return True

    if type(exc).__name__ in _OFFLINE_CLASS_NAMES:
        return True

    status = http_status_of(exc)
    if status in _TRANSIENT_STATUSES:
        return True

    return False
