"""Tests for the NUMOBEL read-query layer (numobel.search).

Builds a self-contained fixture DB in tmp_path; does NOT depend on a
populated numobel.db.
"""

import pytest

from numobel import db, search


@pytest.fixture
def conn(tmp_path):
    """An in-tmp_path SQLite connection seeded with brands/products/links/prices."""
    db_path = str(tmp_path / "numobel.db")
    c = db.connect(db_path)
    db.create_schema(c)

    # Brands: AT and Bajaj have sheets, PCP does not.
    brand_ids = {}
    for code, name, has_sheet in [
        ("AT", "Asian Tiles", 1),
        ("BAJAJ", "Bajaj", 1),
        ("PCP", "Premium Color Plus", 0),
    ]:
        cur = c.execute(
            "INSERT INTO brands(code, name, has_sheet) VALUES(?, ?, ?)",
            (code, name, has_sheet),
        )
        brand_ids[code] = cur.lastrowid

    # Products.
    product_ids = {}
    products = [
        ("AT", "AT-100", "Gothic Grey", "GG"),
        ("AT", "AT-101", "Pink Salt", "PS"),
        ("AT", "AT-102", "Sky Grey", "SG"),
        ("BAJAJ", "BJ-200", "Pink Salt", "PS2"),
        ("BAJAJ", "BJ-201", "Ocean Blue", "OB"),
        ("PCP", "PCP-300", "Rose Gold", "RG"),
    ]
    for code, sku, color, label in products:
        cur = c.execute(
            "INSERT INTO products(brand_id, sku, color_name, self_label) "
            "VALUES(?, ?, ?, ?)",
            (brand_ids[code], sku, color, label),
        )
        product_ids[sku] = cur.lastrowid

    # A resolved link: AT Pink Salt -> BAJAJ Pink Salt. migrate() (below) folds
    # this into a shared color group.
    c.execute(
        "INSERT INTO color_links(from_product_id, to_product_id, to_brand_code, "
        "raw_ref, normalized, status, source) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            product_ids["AT-101"],
            product_ids["BJ-200"],
            "BAJAJ",
            "Pink Salt",
            "pink salt",
            "resolved",
            "import",
        ),
    )
    # An external link: AT Gothic Grey -> PCP (raw ref, no resolved product).
    c.execute(
        "INSERT INTO color_links(from_product_id, to_product_id, to_brand_code, "
        "raw_ref, normalized, status, source) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            product_ids["AT-100"],
            None,
            "PCP",
            "PCP Smoke",
            "pcp smoke",
            "external",
            "import",
        ),
    )

    # Prices.
    c.execute(
        "INSERT INTO prices(seller, mrp, dp) VALUES(?, ?, ?)",
        ("Zeta", 200.0, 150.0),
    )
    c.execute(
        "INSERT INTO prices(seller, mrp, dp) VALUES(?, ?, ?)",
        ("Alpha", 100.0, 80.0),
    )
    c.commit()

    # Fold the resolved link into a color group, matching production startup.
    db.migrate(c)

    try:
        yield c, product_ids, brand_ids
    finally:
        c.close()


def test_search_color_substring(conn):
    c, _, _ = conn
    rows = search.search_products(c, "pink", scope="all")
    colors = {r["color_name"] for r in rows}
    assert "Pink Salt" in colors
    assert "Sky Grey" not in colors


def test_search_case_insensitive(conn):
    c, _, _ = conn
    rows = search.search_products(c, "PINK", scope="all")
    colors = {r["color_name"] for r in rows}
    assert "Pink Salt" in colors


def test_search_brand_detection(conn):
    c, _, _ = conn
    rows = search.search_products(c, "Bajaj", scope="all")
    assert rows, "expected Bajaj products"
    assert all(r["brand_code"] == "BAJAJ" for r in rows)
    assert {r["sku"] for r in rows} == {"BJ-200", "BJ-201"}


def test_search_brand_detection_by_code(conn):
    c, _, _ = conn
    rows = search.search_products(c, "AT", scope="all")
    assert rows
    assert all(r["brand_code"] == "AT" for r in rows)


def test_brand_code_filter_restricts(conn):
    c, _, _ = conn
    # 'pink' matches in both AT and BAJAJ; brand_code should restrict to AT.
    rows = search.search_products(
        c, "pink", scope="color", brand_code="AT"
    )
    assert rows
    assert all(r["brand_code"] == "AT" for r in rows)
    assert {r["sku"] for r in rows} == {"AT-101"}


def test_empty_query_returns_all(conn):
    c, _, _ = conn
    rows = search.search_products(c, "", scope="all")
    assert len(rows) == 6  # all products
    # Ordered by brand_name then color_name.
    brand_names = [r["brand_name"] for r in rows]
    assert brand_names == sorted(brand_names)


def test_empty_query_with_brand_filter(conn):
    c, _, _ = conn
    rows = search.search_products(c, "  ", scope="all", brand_code="BAJAJ")
    assert {r["sku"] for r in rows} == {"BJ-200", "BJ-201"}


def test_search_includes_brand_columns(conn):
    c, _, _ = conn
    rows = search.search_products(c, "Ocean", scope="color")
    assert rows
    row = rows[0]
    assert row["brand_code"] == "BAJAJ"
    assert row["brand_name"] == "Bajaj"


def test_get_product(conn):
    c, product_ids, _ = conn
    pid = product_ids["AT-101"]
    row = search.get_product(c, pid)
    assert row is not None
    assert row["color_name"] == "Pink Salt"
    assert row["brand_code"] == "AT"
    assert search.get_product(c, 999999) is None


def test_get_similar_colors_family_is_symmetric(conn):
    c, product_ids, _ = conn
    at_pink = product_ids["AT-101"]
    bj_pink = product_ids["BJ-200"]

    # AT Pink Salt sees BAJAJ Pink Salt as a family member...
    from_at = search.get_similar_colors(c, at_pink)
    members = [l for l in from_at if l["kind"] == "member"]
    assert len(members) == 1
    assert members[0]["member_product_id"] == bj_pink
    assert members[0]["other_product_id"] == bj_pink
    assert members[0]["status"] == "resolved"
    assert members[0]["other_label"] == "BAJAJ BJ-200 Pink Salt"

    # ...and the relationship is symmetric from the other side.
    from_bj = search.get_similar_colors(c, bj_pink)
    members = [l for l in from_bj if l["kind"] == "member"]
    assert len(members) == 1
    assert members[0]["member_product_id"] == at_pink
    assert members[0]["other_label"] == "AT AT-101 Pink Salt"


def test_get_similar_colors_transitive_closure(conn):
    """A third color linked to one family member appears for all of them."""
    c, product_ids, _ = conn
    from numobel import mutations

    # Sky Grey is added to the Pink Salt family via AT Pink Salt only...
    sky = product_ids["AT-102"]
    mutations.add_to_family(c, product_ids["AT-101"], sky)

    # ...yet BAJAJ Pink Salt (linked only to AT Pink Salt) now sees Sky Grey.
    bj_members = {
        l["member_product_id"]
        for l in search.get_similar_colors(c, product_ids["BJ-200"])
        if l["kind"] == "member"
    }
    assert sky in bj_members
    assert product_ids["AT-101"] in bj_members


def test_get_similar_colors_external(conn):
    c, product_ids, _ = conn
    at_gothic = product_ids["AT-100"]
    links = search.get_similar_colors(c, at_gothic)
    pending = [l for l in links if l["kind"] == "pending"]
    assert len(pending) == 1
    ext = pending[0]
    assert ext["status"] == "external"
    assert ext["other_product_id"] is None
    assert ext["link_id"] is not None
    assert ext["raw_ref"] == "PCP Smoke"
    assert "PCP Smoke" in ext["other_label"]
    assert "[PCP]" in ext["other_label"]


def test_get_prices_roundtrip(conn):
    c, _, _ = conn
    rows = search.get_prices(c)
    assert [r["seller"] for r in rows] == ["Alpha", "Zeta"]
    assert rows[0]["mrp"] == 100.0
    assert rows[1]["dp"] == 150.0


def test_list_brands_all(conn):
    c, _, _ = conn
    rows = search.list_brands(c)
    codes = [r["code"] for r in rows]
    assert set(codes) == {"AT", "BAJAJ", "PCP"}


def test_list_brands_only_with_sheet(conn):
    c, _, _ = conn
    rows = search.list_brands(c, only_with_sheet=True)
    codes = {r["code"] for r in rows}
    assert codes == {"AT", "BAJAJ"}
    assert "PCP" not in codes
