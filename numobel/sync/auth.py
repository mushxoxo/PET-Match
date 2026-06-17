"""OAuth 2.0 installed-app (loopback) flow for the Google Sheets sync.

The OAuth *Desktop app* client id + secret come from the bundled client
(:mod:`numobel.sync.oauth_client`) by default, or — as a fallback — from values
the user pastes into the Connect dialog. From those two strings we synthesize
the installed-app client config dict that ``google_auth_oauthlib`` expects, run
the loopback consent flow once (the browser opens, the user picks an account and
authorizes), and from then on persist (and silently refresh) the token JSON.

All ``google`` / ``google_auth_oauthlib`` imports are done LAZILY inside the
functions that need them, so this module — and the pure :func:`build_client_config`
helper — can be imported and unit-tested without the libraries installed. Only
:func:`build_client_config` is pure; the rest touch the network / browser.
"""

from __future__ import annotations

import json

from numobel.sync import errors

#: Single scope: per-file Drive access (only files this app creates). This one
#: scope also authorizes the Sheets API on our app-created spreadsheet, so we do
#: NOT request the broader ``.../spreadsheets`` scope. ``drive.file`` is
#: *non-sensitive*, which means no Google verification review, no "unverified
#: app" warning, and no 7-day refresh-token expiry — the user just authorizes.
#:
#: NOTE: a token previously granted for both drive.file + spreadsheets still
#: works (it's a superset); a fresh consent now requests only drive.file.
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
]


def build_client_config(client_id: str, client_secret: str) -> dict:
    """Build the installed-app client config dict (pure — no google import).

    Returns the shape ``InstalledAppFlow.from_client_config`` expects for a
    Desktop-app OAuth client.
    """
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def run_oauth_flow(client_id: str, client_secret: str) -> str:
    """Run the loopback installed-app consent flow; return the token JSON string.

    Blocks on a browser + local loopback server, so this must run on a worker
    thread, never the UI thread. Returns ``creds.to_json()``.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        build_client_config(client_id, client_secret), scopes=SCOPES
    )
    creds = flow.run_local_server(port=0, prompt="consent")
    return creds.to_json()


def credentials_from_token_json(token_json: str):
    """Build (and if needed refresh) ``Credentials`` from a token JSON string.

    Refreshes in place when the credentials are expired but carry a refresh
    token. Raises :class:`errors.AuthError` if no usable credential can be
    produced (malformed JSON, missing fields, or a refresh that fails).
    """
    from google.oauth2.credentials import Credentials

    try:
        info = json.loads(token_json)
    except (TypeError, ValueError) as exc:
        raise errors.AuthError(f"malformed token JSON: {exc}") from exc

    try:
        creds = Credentials.from_authorized_user_info(info, SCOPES)
    except (ValueError, KeyError) as exc:
        raise errors.AuthError(f"unusable credentials: {exc}") from exc

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request

        try:
            creds.refresh(Request())
        except Exception as exc:  # google.auth.exceptions.RefreshError etc.
            raise errors.AuthError(f"token refresh failed: {exc}") from exc
        return creds

    raise errors.AuthError("credentials are invalid and cannot be refreshed")


def ensure_fresh(token_json: str):
    """Return ``(credentials, possibly_updated_token_json)``.

    The token JSON differs from the input after a refresh (the access token /
    expiry rotated), so callers should persist the returned string.
    """
    creds = credentials_from_token_json(token_json)
    return creds, creds.to_json()
