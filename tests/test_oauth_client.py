"""Tests for resolving the bundled Google OAuth client (env var + file shapes)."""

from __future__ import annotations

import json

from numobel import db
from numobel.sync import oauth_client


def _isolate(monkeypatch, tmp_path):
    """Point base_dir at an empty tmp dir and clear the env + frozen markers."""
    monkeypatch.setattr(db, "base_dir", lambda: tmp_path)
    monkeypatch.delenv(oauth_client.ENV_CLIENT_ID, raising=False)
    monkeypatch.delenv(oauth_client.ENV_CLIENT_SECRET, raising=False)
    # Ensure the _MEIPASS (frozen) branch is not taken in tests.
    monkeypatch.delattr("sys._MEIPASS", raising=False)


def test_none_when_nothing_configured(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert oauth_client.get_bundled_client() is None
    assert oauth_client.has_bundled_client() is False


def test_env_vars_take_priority(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv(oauth_client.ENV_CLIENT_ID, "env-id")
    monkeypatch.setenv(oauth_client.ENV_CLIENT_SECRET, "env-secret")
    assert oauth_client.get_bundled_client() == ("env-id", "env-secret")
    assert oauth_client.has_bundled_client() is True


def test_env_requires_both_values(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv(oauth_client.ENV_CLIENT_ID, "env-id")  # secret missing
    assert oauth_client.get_bundled_client() is None


def test_reads_google_installed_shape(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / oauth_client.CLIENT_FILENAME).write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "file-id.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-file-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        )
    )
    assert oauth_client.get_bundled_client() == (
        "file-id.apps.googleusercontent.com",
        "GOCSPX-file-secret",
    )


def test_reads_flat_shape(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / oauth_client.CLIENT_FILENAME).write_text(
        json.dumps({"client_id": "flat-id", "client_secret": "flat-secret"})
    )
    assert oauth_client.get_bundled_client() == ("flat-id", "flat-secret")


def test_malformed_file_returns_none(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / oauth_client.CLIENT_FILENAME).write_text("{ not valid json ")
    assert oauth_client.get_bundled_client() is None


def test_file_missing_secret_returns_none(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / oauth_client.CLIENT_FILENAME).write_text(
        json.dumps({"installed": {"client_id": "only-id"}})
    )
    assert oauth_client.get_bundled_client() is None
