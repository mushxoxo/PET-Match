"""Tests for the user-write layer (audit + mutations)."""

from __future__ import annotations

import sqlite3

import pytest

from numobel import audit, db, mutations


def _make_db() -> sqlite3.Connection:
    conn = db.connect(":memory:")
    db.create_schema(conn)
    conn.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)")
    conn.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (2,'BA','Bajaj',1)")
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name) VALUES (10, 1, 'AT5', 'Gothic Grey')"
    )
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name) VALUES (20, 2, 'BA14', 'Slate')"
    )
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name) VALUES (30, 2, 'BA15', 'Ash')"
    )
    conn.commit()
    return conn


def _audit_actions(conn) -> list[str]:
    return [r["action"] for r in audit.get_audit_log(conn)]


# --- photos -------------------------------------------------------------- #
def test_set_and_clear_image_logs():
    conn = _make_db()
    mutations.set_product_image(conn, 10, "/imgs/a.png")
    assert conn.execute("SELECT image_path FROM products WHERE id=10").fetchone()[0] == "/imgs/a.png"
    mutations.set_product_image(conn, 10, None)
    assert conn.execute("SELECT image_path FROM products WHERE id=10").fetchone()[0] is None
    assert _audit_actions(conn) == ["clear_image", "set_image"]


def test_set_image_bad_product():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.set_product_image(conn, 999, "/x.png")


# --- links --------------------------------------------------------------- #
def test_add_link_resolved_user_sourced():
    conn = _make_db()
    link_id = mutations.add_link(conn, 10, 20, note="hand match")
    row = conn.execute("SELECT * FROM color_links WHERE id=?", (link_id,)).fetchone()
    assert row["status"] == "resolved"
    assert row["source"] == "user"
    assert row["to_product_id"] == 20
    assert row["to_brand_code"] == "BA"
    assert row["note"] == "hand match"
    assert "add_link" in _audit_actions(conn)


def test_add_link_rejects_self_link():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.add_link(conn, 10, 10)


def test_add_link_rejects_duplicate_either_direction():
    conn = _make_db()
    mutations.add_link(conn, 10, 20)
    with pytest.raises(mutations.MutationError):
        mutations.add_link(conn, 10, 20)
    with pytest.raises(mutations.MutationError):
        mutations.add_link(conn, 20, 10)


def test_remove_link_logs_and_deletes():
    conn = _make_db()
    link_id = mutations.add_link(conn, 10, 20)
    mutations.remove_link(conn, link_id)
    assert conn.execute("SELECT COUNT(*) FROM color_links").fetchone()[0] == 0
    assert "remove_link" in _audit_actions(conn)


def test_remove_link_bad_id():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.remove_link(conn, 123)


def test_resolve_link_promotes_external_to_resolved():
    conn = _make_db()
    cur = conn.execute(
        "INSERT INTO color_links(from_product_id, to_brand_code, raw_ref, "
        "normalized, status, source) VALUES (10, 'PCP', 'PCP27', 'PCP27', "
        "'external', 'import')"
    )
    conn.commit()
    link_id = int(cur.lastrowid)
    mutations.resolve_link(conn, link_id, 20)
    row = conn.execute("SELECT * FROM color_links WHERE id=?", (link_id,)).fetchone()
    assert row["status"] == "resolved"
    assert row["source"] == "user"
    assert row["to_product_id"] == 20
    assert row["raw_ref"] == "PCP27"  # provenance preserved
    assert "resolve_link" in _audit_actions(conn)


# --- prices -------------------------------------------------------------- #
def test_update_price_field_logs_old_and_new():
    conn = _make_db()
    pid = int(
        conn.execute(
            "INSERT INTO prices(seller, mrp) VALUES ('Acme', 100.0)"
        ).lastrowid
    )
    conn.commit()
    mutations.update_price_field(conn, pid, "mrp", 150.0)
    assert conn.execute("SELECT mrp FROM prices WHERE id=?", (pid,)).fetchone()[0] == 150.0
    log = audit.get_audit_log(conn)[0]
    assert log["action"] == "update_price"
    import json

    details = json.loads(log["details"])
    assert details["old"] == 100.0 and details["new"] == 150.0


def test_update_price_rejects_unknown_field():
    conn = _make_db()
    pid = int(conn.execute("INSERT INTO prices(seller) VALUES ('X')").lastrowid)
    conn.commit()
    with pytest.raises(mutations.MutationError):
        mutations.update_price_field(conn, pid, "id", 5)
    with pytest.raises(mutations.MutationError):
        mutations.update_price_field(conn, pid, "evil; DROP TABLE prices", 5)


def test_audit_log_newest_first():
    conn = _make_db()
    mutations.set_product_image(conn, 10, "/a.png")
    mutations.set_product_image(conn, 20, "/b.png")
    rows = audit.get_audit_log(conn)
    assert rows[0]["entity_id"] == 20
    assert rows[1]["entity_id"] == 10
