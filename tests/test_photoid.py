"""Photo identification pipeline: scan -> product details -> TCGdex candidates."""
import pytest
from PIL import Image

from app import cardcrop, identify, owned_repository as owned, photoid
from app.tcgtracking import ScanError, ScanHit


def row(cid, name, number, set_code="SSP", set_name="Surging Sparks"):
    return {"id": cid, "name": name, "clean_name": name, "card_number": number, "set_code": set_code,
            "set_name": set_name, "hp": None}


def product(name, number, abbr="SSP", set_name="Surging Sparks"):
    return {"id": 1, "name": name, "base_name": name, "number": number, "set_abbr": abbr,
            "set_name": set_name, "rarity": ""}


class Scanner:
    """Scripted scanner: `scans` are returned in order (an Exception is raised); `products` by id."""
    def __init__(self, scans, products):
        self.scans, self.products, self.scan_calls = list(scans), products, 0

    def scan(self, jpeg):
        self.scan_calls += 1
        r = self.scans.pop(0)
        if isinstance(r, Exception):
            raise r
        assert jpeg[:2] == b"\xff\xd8"
        return r

    def product(self, pid):
        r = self.products.get(pid)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "card.png"
    Image.new("RGB", (300, 400), "gray").save(path)      # no card outline in it: whole-photo fallback
    return path


CATALOG = [row("sv8-57", "Pikachu ex", "57/191"), row("sv8-58", "Pikachu ex", "58/191"),
           row("sv3-30", "Pikachu ex", "30/197", "OBF", "Obsidian Flames")]


def run(image, scanner, search=None, local=None, **kw):
    return photoid.identify_photo(image, scanner, search or (lambda n, num: CATALOG), local or (lambda n: []), **kw)


def test_best_hit_becomes_the_reading_and_the_exact_card_wins(image):
    scanner = Scanner([[ScanHit(1, 91), ScanHit(2, 60)]],
                      {1: product("Pikachu ex", "057/191"), 2: product("Pikachu ex", "030/197", "OBF", "Obsidian Flames")})
    result = run(image, scanner)
    assert scanner.scan_calls == 1                                   # good match: no retries
    assert result.reading.name == "Pikachu ex" and result.top_score == 91 and result.query == "Pikachu ex"
    best = result.candidates[0]
    assert best.card["id"] == "sv8-57" and "number" in best.reasons and "photo 91%" in best.reasons
    assert identify.is_confident(result.candidates)
    assert len({c.card["id"] for c in result.candidates}) == len(result.candidates)    # no duplicates


def test_a_second_hit_for_a_different_printing_is_offered_too(image):
    scanner = Scanner([[ScanHit(1, 88), ScanHit(2, 84)]],
                      {1: product("Pikachu ex", "057/191"), 2: product("Pikachu ex", "030/197", "OBF", "Obsidian Flames")})
    ids = [c.card["id"] for c in run(image, scanner).candidates]
    assert ids[0] == "sv8-57" and "sv3-30" in ids


def test_weak_match_is_retried_upside_down_and_the_better_result_is_used(image):
    scanner = Scanner([[ScanHit(1, 20)], [ScanHit(2, 90)]],
                      {1: product("Raichu", "26"), 2: product("Pikachu ex", "057/191")})
    result = run(image, scanner, min_score=50)
    assert scanner.scan_calls == 2 and result.reading.name == "Pikachu ex"


def test_when_the_outline_was_found_the_whole_photo_is_the_last_attempt(image, monkeypatch):
    img = Image.new("RGB", (420, 587), "gray")
    monkeypatch.setattr(photoid.cardcrop, "crop_card", lambda i: cardcrop.Crop(img, True, Image.new("RGB", (300, 400))))
    scanner = Scanner([[ScanHit(1, 10)], [ScanHit(1, 15)], [ScanHit(2, 88)]],
                      {1: product("Raichu", "26"), 2: product("Pikachu ex", "057/191")})
    result = run(image, scanner)
    assert scanner.scan_calls == 3 and result.reading.name == "Pikachu ex" and result.crop_found


def test_a_failed_retry_never_throws_away_a_usable_first_result(image):
    scanner = Scanner([[ScanHit(1, 40)], ScanError("busy")], {1: product("Pikachu ex", "057/191")})
    assert run(image, scanner).candidates[0].card["id"] == "sv8-57"


def test_scan_failure_is_reported_as_an_error(image):
    result = run(image, Scanner([ScanError("The scan service has no card data available right now.")], {}))
    assert result.error == "The scan service has no card data available right now." and not result.candidates


def test_no_hits_is_not_an_error(image):
    result = run(image, Scanner([[], []], {}))
    assert result.error is None and not result.candidates and result.reading is None


def test_unreadable_product_details_are_explained(image):
    result = run(image, Scanner([[ScanHit(1, 90)]], {1: None}))
    assert not result.candidates and "details couldn't be looked up" in result.note
    result = run(image, Scanner([[ScanHit(1, 90)]], {1: ScanError("x")}))
    assert "details couldn't be looked up" in result.note


def test_tcgdex_trouble_falls_back_to_the_local_cache_and_says_so(image):
    def down(name, number):
        raise RuntimeError("TCGdex is down")
    result = run(image, Scanner([[ScanHit(1, 90)]], {1: product("Pikachu ex", "057/191")}),
                 search=down, local=lambda n: [CATALOG[0]])
    assert result.note == "TCGdex is down" and result.candidates[0].card["id"] == "sv8-57"


def test_each_distinct_name_and_number_is_searched_once(image):
    calls = []
    scanner = Scanner([[ScanHit(1, 90), ScanHit(2, 85)]],
                      {1: product("Pikachu ex", "057/191"), 2: product("Pikachu ex", "57")})
    run(image, scanner, search=lambda n, num: calls.append((n, num)) or CATALOG)
    assert len(calls) == 1


def test_unreadable_image_file(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"not an image")
    assert run(bad, Scanner([], {})).error == "That photo couldn't be read."


@pytest.mark.parametrize("variants, expected", [
    (["Reverse Holofoil", "Holofoil"], "Holofoil"), (["Normal", "Holofoil"], "Normal"),
    (["Reverse Holofoil"], "Reverse Holofoil"), ([], "Normal"),
])
def test_default_variant(variants, expected):
    assert owned.default_variant({"variants": variants, "prices": {}}) == expected
