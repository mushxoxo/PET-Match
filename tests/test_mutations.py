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


# --- brands & products --------------------------------------------------- #
def test_add_brand_creates_and_logs():
    conn = _make_db()
    bid = mutations.add_brand(conn, "ZE", "Zenith", has_sheet=True)
    row = conn.execute("SELECT * FROM brands WHERE id=?", (bid,)).fetchone()
    assert row["code"] == "ZE"
    assert row["name"] == "Zenith"
    assert row["has_sheet"] == 1
    assert "add_brand" in _audit_actions(conn)


def test_add_brand_rejects_duplicate_code_case_insensitive():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.add_brand(conn, "at")  # clashes with existing 'AT'


def test_add_brand_requires_code():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.add_brand(conn, "   ")


def test_add_product_creates_under_brand():
    conn = _make_db()
    pid = mutations.add_product(
        conn, brand_id=1, sku="AT9", color_name="Midnight", thickness=1.2
    )
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    assert row["brand_id"] == 1
    assert row["sku"] == "AT9"
    assert row["color_name"] == "Midnight"
    assert row["thickness"] == 1.2
    assert "add_product" in _audit_actions(conn)


def test_add_product_rejects_duplicate_sku_in_brand():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.add_product(conn, brand_id=1, sku="AT5")  # AT5 exists


def test_add_product_requires_known_brand():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.add_product(conn, brand_id=999, sku="X1")


def test_add_product_requires_sku_or_color():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.add_product(conn, brand_id=1)


# --- color families ------------------------------------------------------- #
def _group(conn, pid):
    return conn.execute(
        "SELECT color_group_id FROM products WHERE id=?", (pid,)
    ).fetchone()[0]


def test_add_to_family_groups_two_products():
    conn = _make_db()
    gid = mutations.add_to_family(conn, 10, 20)
    assert _group(conn, 10) == gid
    assert _group(conn, 20) == gid
    assert "add_to_family" in _audit_actions(conn)


def test_add_to_family_rejects_self():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.add_to_family(conn, 10, 10)


def test_add_to_family_rejects_already_same_family():
    conn = _make_db()
    mutations.add_to_family(conn, 10, 20)
    with pytest.raises(mutations.MutationError):
        mutations.add_to_family(conn, 10, 20)


def test_add_to_family_merges_two_existing_families():
    conn = _make_db()
    g1 = mutations.add_to_family(conn, 10, 20)  # {10, 20}
    mutations.add_to_family(conn, 30, 10)  # 30 joins; all share one group
    groups = {_group(conn, pid) for pid in (10, 20, 30)}
    assert len(groups) == 1
    assert g1 in groups


def test_remove_from_family_dissolves_a_pair():
    conn = _make_db()
    gid = mutations.add_to_family(conn, 10, 20)
    mutations.remove_from_family(conn, 20)
    # Removing one of a pair leaves no family: both end ungrouped, group gone.
    assert _group(conn, 10) is None
    assert _group(conn, 20) is None
    assert conn.execute(
        "SELECT COUNT(*) FROM color_groups WHERE id=?", (gid,)
    ).fetchone()[0] == 0
    assert "remove_from_family" in _audit_actions(conn)


def test_remove_from_family_keeps_group_with_others_remaining():
    conn = _make_db()
    mutations.add_to_family(conn, 10, 20)
    mutations.add_to_family(conn, 10, 30)  # family {10, 20, 30}
    mutations.remove_from_family(conn, 30)
    assert _group(conn, 30) is None
    assert _group(conn, 10) is not None
    assert _group(conn, 10) == _group(conn, 20)


def test_remove_from_family_rejects_ungrouped():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.remove_from_family(conn, 10)


def test_resolve_reference_adds_to_family_and_drops_link():
    conn = _make_db()
    cur = conn.execute(
        "INSERT INTO color_links(from_product_id, to_brand_code, raw_ref, "
        "normalized, status, source) VALUES (10, 'PCP', 'PCP27', 'PCP27', "
        "'external', 'import')"
    )
    conn.commit()
    link_id = int(cur.lastrowid)
    mutations.resolve_reference(conn, link_id, 20)
    # Family now includes 10 and 20; the pending reference is gone.
    assert _group(conn, 10) == _group(conn, 20)
    assert conn.execute(
        "SELECT COUNT(*) FROM color_links WHERE id=?", (link_id,)
    ).fetchone()[0] == 0
    assert "resolve_reference" in _audit_actions(conn)


def test_remove_reference_deletes_pending_link():
    conn = _make_db()
    cur = conn.execute(
        "INSERT INTO color_links(from_product_id, raw_ref, status, source) "
        "VALUES (10, 'XYZ', 'unresolved', 'import')"
    )
    conn.commit()
    link_id = int(cur.lastrowid)
    mutations.remove_reference(conn, link_id)
    assert conn.execute("SELECT COUNT(*) FROM color_links").fetchone()[0] == 0
    assert "remove_reference" in _audit_actions(conn)


def test_remove_reference_bad_id():
    conn = _make_db()
    with pytest.raises(mutations.MutationError):
        mutations.remove_reference(conn, 123)


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
