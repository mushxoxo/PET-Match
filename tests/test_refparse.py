from numobel.importer.refparse import parse_ref, ParsedRef


def test_simple_brand_number():
    r = parse_ref("AT22")
    assert r is not None
    assert r.brand_code == "AT"
    assert r.number == "22"
    assert r.code == "AT22"
    assert r.name is None
    assert r.normalized == "AT22"
    assert r.raw == "AT22"


def test_long_prefix():
    r = parse_ref("PCP45")
    assert r.brand_code == "PCP"
    assert r.code == "PCP45"


def test_space_between_prefix_and_number():
    r = parse_ref("PCP 27")
    assert r.brand_code == "PCP"
    assert r.number == "27"
    assert r.code == "PCP27"


def test_space_with_default_brand_matches():
    r = parse_ref("PCP 27", default_brand="PCP")
    assert r.brand_code == "PCP"
    assert r.number == "27"
    assert r.code == "PCP27"


def test_trailing_dash_no_name():
    r = parse_ref("BA14-")
    assert r.brand_code == "BA"
    assert r.number == "14"
    assert r.code == "BA14"
    assert r.name is None


def test_dash_before_name():
    r = parse_ref("ACP19-Violet")
    assert r.brand_code == "ACP"
    assert r.number == "19"
    assert r.code == "ACP19"
    assert r.name == "Violet"


def test_dash_before_multiword_name():
    r = parse_ref("AT3-Sky Grey")
    assert r.brand_code == "AT"
    assert r.code == "AT3"
    assert r.name == "Sky Grey"


def test_leading_zeros_preserved():
    r = parse_ref("NW01133-Snow White")
    assert r.brand_code == "NW"
    assert r.number == "01133"
    assert r.code == "NW01133"
    assert r.name == "Snow White"


def test_dash_between_prefix_number_and_name():
    r = parse_ref("NU-321-Jet Black")
    assert r.brand_code == "NU"
    assert r.number == "321"
    assert r.code == "NU321"
    assert r.name == "Jet Black"


def test_dash_between_prefix_and_number_no_name():
    r = parse_ref("EA-127")
    assert r.brand_code == "EA"
    assert r.number == "127"
    assert r.code == "EA127"
    assert r.name is None


def test_bare_number_uses_default_brand():
    r = parse_ref("27", default_brand="PCP")
    assert r.brand_code == "PCP"
    assert r.number == "27"
    assert r.code == "PCP27"


def test_bare_number_no_default():
    r = parse_ref("27")
    assert r.brand_code is None
    assert r.number == "27"
    assert r.code == "27"


def test_float_whole_number_with_default_brand():
    r = parse_ref(27.0, default_brand="PCP")
    assert r.code == "PCP27"


def test_text_no_digits_new_add():
    assert parse_ref("NEW ADD") is None


def test_text_no_digits_ok():
    assert parse_ref("ok") is None


def test_url_no_digits():
    assert parse_ref("https://example.com/foo") is None


def test_empty_string():
    assert parse_ref("") is None


def test_none():
    assert parse_ref(None) is None


def test_whitespace_only():
    assert parse_ref("   ") is None


def test_greys_whites_blacks():
    assert parse_ref("Greys Whites Blacks") is None


def test_int_input():
    r = parse_ref(45)
    assert r.number == "45"
    assert r.code == "45"


def test_default_brand_uppercased():
    r = parse_ref("27", default_brand="pcp")
    assert r.brand_code == "PCP"
    assert r.code == "PCP27"


def test_parsedref_is_namedtuple():
    r = parse_ref("AT22")
    assert isinstance(r, ParsedRef)
    assert r._fields == ("brand_code", "number", "code", "name", "normalized", "raw")
