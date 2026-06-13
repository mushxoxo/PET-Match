"""Smoke tests for the NUMOBEL SQLite layer."""

from numobel import db


def test_schema_roundtrip(tmp_path):
    db_path = str(tmp_path / "numobel.db")
    conn = db.connect(db_path)
    try:
        db.create_schema(conn)

        # Insert a brand.
        cur = conn.execute(
            "INSERT INTO brands(code, name, has_sheet) VALUES(?, ?, ?)",
            ("ACME", "Acme Tiles", 1),
        )
        brand_id = cur.lastrowid

        # Insert a product referencing the brand.
        cur = conn.execute(
            "INSERT INTO products(brand_id, sku, color_name, thickness) "
            "VALUES(?, ?, ?, ?)",
            (brand_id, "SKU-1", "Ocean Blue", 12.5),
        )
        product_id = cur.lastrowid

        # Insert a price.
        conn.execute(
            "INSERT INTO prices(seller, mrp, dp) VALUES(?, ?, ?)",
            ("SellerCo", 100.0, 80.0),
        )

        # Insert an audit row.
        conn.execute(
            "INSERT INTO audit_log(ts, action, entity, entity_id, details) "
            "VALUES(?, ?, ?, ?, ?)",
            ("2026-06-13T00:00:00", "create", "product", product_id, "test"),
        )
        conn.commit()

        # Settings round-trip.
        assert db.get_setting(conn, "missing", "fallback") == "fallback"
        db.set_setting(conn, "theme", "dark")
        assert db.get_setting(conn, "theme") == "dark"
        db.set_setting(conn, "theme", "light")
        assert db.get_setting(conn, "theme") == "light"

        # Assert inserted rows round-trip.
        brand = conn.execute(
            "SELECT * FROM brands WHERE id = ?", (brand_id,)
        ).fetchone()
        assert brand["code"] == "ACME"
        assert brand["has_sheet"] == 1

        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        assert product["brand_id"] == brand_id
        assert product["sku"] == "SKU-1"
        assert product["thickness"] == 12.5

        price = conn.execute("SELECT * FROM prices").fetchone()
        assert price["seller"] == "SellerCo"
        assert price["mrp"] == 100.0

        audit = conn.execute("SELECT * FROM audit_log").fetchone()
        assert audit["entity"] == "product"
        assert audit["entity_id"] == product_id
    finally:
        conn.close()


def test_reset_catalog(tmp_path):
    db_path = str(tmp_path / "numobel.db")
    conn = db.connect(db_path)
    try:
        db.create_schema(conn)
        cur = conn.execute(
            "INSERT INTO brands(code) VALUES(?)", ("ACME",)
        )
        brand_id = cur.lastrowid
        conn.execute(
            "INSERT INTO products(brand_id, sku) VALUES(?, ?)",
            (brand_id, "SKU-1"),
        )
        conn.execute("INSERT INTO prices(seller) VALUES(?)", ("SellerCo",))
        # audit_log and settings should be preserved.
        conn.execute(
            "INSERT INTO audit_log(action) VALUES(?)", ("seed",)
        )
        db.set_setting(conn, "keep", "yes")
        conn.commit()

        db.reset_catalog(conn)

        assert conn.execute("SELECT COUNT(*) FROM brands").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
        assert db.get_setting(conn, "keep") == "yes"
    finally:
        conn.close()
