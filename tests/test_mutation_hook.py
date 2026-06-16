"""Tests for the mutation listener hook (sync push trigger)."""

from __future__ import annotations

import sqlite3

import pytest

from numobel import db, mutations


def _make_db() -> sqlite3.Connection:
    conn = db.connect(":memory:")
    db.create_schema(conn)
    conn.execute(
        "INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)"
    )
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name) "
        "VALUES (10, 1, 'AT5', 'Gothic Grey')"
    )
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def _clean_listeners():
    """Ensure no listener leaks across tests."""
    yield
    mutations._listeners.clear()


def test_listener_notified_on_commit():
    conn = _make_db()
    events = []
    mutations.register_listener(lambda *a: events.append(a))
    bid = mutations.add_brand(conn, "ZE", "Zenith", has_sheet=True)
    assert events == [("add_brand", "brand", bid)]


def test_listener_called_exactly_once():
    conn = _make_db()
    calls = []
    mutations.register_listener(lambda *a: calls.append(a))
    mutations.add_brand(conn, "ZE")
    assert len(calls) == 1


def test_failed_mutation_does_not_notify():
    conn = _make_db()
    events = []
    mutations.register_listener(lambda *a: events.append(a))
    with pytest.raises(mutations.MutationError):
        mutations.add_brand(conn, "at")  # duplicate code -> raises before commit
    assert events == []


def test_unregister_stops_notifications():
    conn = _make_db()
    events = []

    def listener(*a):
        events.append(a)

    mutations.register_listener(listener)
    mutations.add_brand(conn, "ZE")
    mutations.unregister_listener(listener)
    mutations.add_brand(conn, "YA")
    assert len(events) == 1
    assert events[0][0] == "add_brand"


def test_register_is_idempotent():
    conn = _make_db()
    events = []

    def listener(*a):
        events.append(a)

    mutations.register_listener(listener)
    mutations.register_listener(listener)  # second register is a no-op
    mutations.add_brand(conn, "ZE")
    assert len(events) == 1


def test_listener_exception_does_not_break_mutation():
    conn = _make_db()

    def boom(*a):
        raise RuntimeError("listener blew up")

    mutations.register_listener(boom)
    # Mutation must still succeed despite the failing listener.
    bid = mutations.add_brand(conn, "ZE")
    row = conn.execute("SELECT code FROM brands WHERE id=?", (bid,)).fetchone()
    assert row["code"] == "ZE"


def test_set_image_action_passed_through():
    conn = _make_db()
    events = []
    mutations.register_listener(lambda *a: events.append(a))
    mutations.set_product_image(conn, 10, "/imgs/a.png")
    mutations.set_product_image(conn, 10, None)
    assert events == [
        ("set_image", "product", 10),
        ("clear_image", "product", 10),
    ]
