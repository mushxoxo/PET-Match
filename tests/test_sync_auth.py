"""Tests for the OAuth installed-app flow helpers.

``build_client_config`` is pure (no google import). The credential-parsing
tests are guarded with ``importorskip`` so the suite stays green when the google
libraries are absent. No test here touches the network or a browser.
"""

from __future__ import annotations

import pytest

from numobel.sync import auth
from numobel.sync import errors


def test_build_client_config_shape():
    cfg = auth.build_client_config("cid.apps", "secret-xyz")
    assert cfg == {
        "installed": {
            "client_id": "cid.apps",
            "client_secret": "secret-xyz",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def test_scopes_are_drive_file_only():
    # Non-sensitive drive.file only: no verification, no 7-day re-auth. The
    # broader (sensitive) spreadsheets scope is deliberately NOT requested.
    assert auth.SCOPES == ["https://www.googleapis.com/auth/drive.file"]
    assert not any("spreadsheets" in s for s in auth.SCOPES)


def test_credentials_from_malformed_token_raises_auth_error():
    pytest.importorskip("google.oauth2")
    with pytest.raises(errors.AuthError):
        auth.credentials_from_token_json("not-json{{{")


def test_credentials_from_incomplete_token_raises_auth_error():
    pytest.importorskip("google.oauth2")
    # Valid JSON but missing the fields Credentials needs.
    with pytest.raises(errors.AuthError):
        auth.credentials_from_token_json('{"foo": "bar"}')
