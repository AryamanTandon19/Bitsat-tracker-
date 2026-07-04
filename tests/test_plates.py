from app.plates import (fuzzy_match, levenshtein, looks_like_indian_plate,
                        normalize_plate, repair_plate)


def test_normalize():
    assert normalize_plate("wb 02 ab 1234") == "WB02AB1234"
    assert normalize_plate("WB-02-AB-1234") == "WB02AB1234"
    assert normalize_plate("dl8caf5031") == "DL8CAF5031"
    assert normalize_plate("  ka.05.mn.8899 ") == "KA05MN8899"
    assert normalize_plate("") == ""
    assert normalize_plate(None) == ""


def test_indian_plate_format():
    assert looks_like_indian_plate("WB02AB1234")
    assert looks_like_indian_plate("DL8CAF5031")
    assert looks_like_indian_plate("KA05M1234")          # single series letter
    assert not looks_like_indian_plate("12ABCD3456")
    assert not looks_like_indian_plate("WB02AB12")


def test_repair_common_ocr_confusions():
    # 0 read instead of O in state code, letter instead of digit at the end
    assert repair_plate("W802AB1234") == "WB02AB1234"    # 8 -> B in position 2
    assert repair_plate("WB02AB123O") == "WB02AB1230"    # O -> 0 in the number
    # already valid plates pass through untouched
    assert repair_plate("KA05MN8899") == "KA05MN8899"


def test_levenshtein():
    assert levenshtein("", "") == 0
    assert levenshtein("ABC", "ABC") == 0
    assert levenshtein("ABC", "ABD") == 1
    assert levenshtein("ABC", "AC") == 1
    assert levenshtein("WB02AB1234", "WB02AB1284") == 1
    assert levenshtein("WB02AB1234", "KA05MN8899") > 1


def test_fuzzy_match():
    registry = ["WB02AB1234", "DL8CAF5031", "KA05MN8899"]
    assert fuzzy_match("WB02AB1234", registry) == "WB02AB1234"   # exact
    assert fuzzy_match("WB02AB1284", registry) == "WB02AB1234"   # 1 OCR miss
    assert fuzzy_match("WB02XY9999", registry) is None           # too far
    assert fuzzy_match("", registry) is None
    assert fuzzy_match("MH12CD4567", []) is None
