"""Tests for the sync error taxonomy + offline classification policy."""

from __future__ import annotations

import socket

from numobel.sync import errors


class _FakeHttpError(Exception):
    """Stand-in for an exception that exposes an HTTP status code."""

    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _GoogleResp:
    def __init__(self, status):
        self.status = status


class _GoogleHttpError(Exception):
    """Stand-in for googleapiclient.errors.HttpError (``.resp.status``)."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = _GoogleResp(status)


def test_is_offline_error_true_for_connectivity_errors():
    assert errors.is_offline_error(ConnectionError()) is True
    assert errors.is_offline_error(TimeoutError()) is True
    assert errors.is_offline_error(socket.gaierror()) is True


def test_is_offline_error_true_for_transient_status():
    assert errors.is_offline_error(_FakeHttpError(503)) is True
    assert errors.is_offline_error(_GoogleHttpError(500)) is True


def test_is_offline_error_false_for_404_and_value_error():
    assert errors.is_offline_error(_FakeHttpError(404)) is False
    assert errors.is_offline_error(ValueError("nope")) is False


def test_is_offline_error_true_for_named_transport_errors():
    class TransportError(Exception):
        pass

    class ServerNotFoundError(Exception):
        pass

    assert errors.is_offline_error(TransportError()) is True
    assert errors.is_offline_error(ServerNotFoundError()) is True


def test_http_status_of_extracts_common_shapes():
    assert errors.http_status_of(_FakeHttpError(429)) == 429
    assert errors.http_status_of(_GoogleHttpError(502)) == 502
    assert errors.http_status_of(ValueError("x")) is None


def test_http_status_of_never_raises():
    class Weird:
        @property
        def status_code(self):
            raise RuntimeError("boom")

    # Must not propagate the property's exception.
    assert errors.http_status_of(Weird()) is None


def test_conflict_error_carries_revisions():
    exc = errors.ConflictError(local_revision=3, cloud_revision=7)
    assert exc.local_revision == 3
    assert exc.cloud_revision == 7
    assert isinstance(exc, errors.SyncError)


def test_error_hierarchy():
    assert issubclass(errors.ConflictError, errors.SyncError)
    assert issubclass(errors.AuthError, errors.SyncError)
    assert issubclass(errors.SheetMissingError, errors.SyncError)
