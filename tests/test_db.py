"""Smoke tests for the NUMOBEL SQLite layer."""

from numobel import audit, db


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


def test_create_schema_audit_has_uuid_column():
    """Fresh schema includes ``uuid`` as the last audit_log column."""
    conn = db.connect(":memory:")
    try:
        db.create_schema(conn)
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")]
        assert "uuid" in cols
        assert cols[-1] == "uuid"
    finally:
        conn.close()


def test_log_change_sets_uuid():
    """log_change mints a 32-hex-char uuid; successive calls differ."""
    conn = db.connect(":memory:")
    try:
        db.create_schema(conn)
        audit.log_change(conn, "create", "product", 1, {"k": "v"})
        audit.log_change(conn, "create", "product", 2, {"k": "w"})
        conn.commit()

        uuids = [
            row["uuid"]
            for row in conn.execute("SELECT uuid FROM audit_log ORDER BY id")
        ]
        assert len(uuids) == 2
        for u in uuids:
            assert u is not None
            assert len(u) == 32
            int(u, 16)  # all hex digits
        assert uuids[0] != uuids[1]
    finally:
        conn.close()


def test_migrate_adds_and_backfills_audit_uuid():
    """migrate() adds the uuid column and backfills legacy rows distinctly."""
    conn = db.connect(":memory:")
    try:
        # Build a LEGACY audit_log by hand: the old shape WITHOUT uuid.
        conn.execute(
            "CREATE TABLE audit_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT, action TEXT, entity TEXT, "
            "entity_id INTEGER, details TEXT)"
        )
        conn.execute(
            "INSERT INTO audit_log(ts, action, entity, entity_id, details) "
            "VALUES(?, ?, ?, ?, ?)",
            ("2026-06-13T00:00:00", "create", "product", 1, "a"),
        )
        conn.execute(
            "INSERT INTO audit_log(ts, action, entity, entity_id, details) "
            "VALUES(?, ?, ?, ?, ?)",
            ("2026-06-13T00:00:01", "update", "product", 1, "b"),
        )
        conn.commit()

        db.migrate(conn)

        cols = [row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")]
        assert "uuid" in cols

        uuids = [
            row["uuid"]
            for row in conn.execute("SELECT uuid FROM audit_log ORDER BY id")
        ]
        assert len(uuids) == 2
        assert all(u is not None for u in uuids)
        assert len(set(uuids)) == 2
    finally:
        conn.close()


def test_migrate_backfills_uuid_after_interrupted_add():
    """migrate() backfills a NULL uuid when the column already exists.

    Simulates a crash between ``ALTER TABLE ADD COLUMN uuid`` (which
    auto-commits) and the backfill: the column is present but a row's uuid is
    still NULL. A subsequent migrate() must fill it and ensure the unique index.
    """
    conn = db.connect(":memory:")
    try:
        # Modern schema (column present), then force the interrupted state.
        db.create_schema(conn)
        conn.execute(
            "INSERT INTO audit_log(ts, action, entity, entity_id, details) "
            "VALUES(?, ?, ?, ?, ?)",
            ("2026-06-13T00:00:00", "create", "product", 1, "a"),
        )
        conn.execute("UPDATE audit_log SET uuid = NULL")
        conn.commit()
        assert (
            conn.execute(
                "SELECT uuid FROM audit_log"
            ).fetchone()["uuid"]
            is None
        )

        db.migrate(conn)

        row = conn.execute("SELECT uuid FROM audit_log").fetchone()
        assert row["uuid"] is not None
        assert len(row["uuid"]) == 32
        int(row["uuid"], 16)  # all hex digits

        indexes = {
            r["name"]
            for r in conn.execute("PRAGMA index_list(audit_log)")
        }
        assert "uidx_audit_log_uuid" in indexes
    finally:
        conn.close()
