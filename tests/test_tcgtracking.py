"""TCG Tracking scan client: requests, error mapping, retries, tolerant product parsing."""
import pytest
import requests

from app import tcgtracking as tt


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    tt._products.clear()


def test_scan_sends_a_multipart_image_for_pokemon_and_sorts_hits():
    session = Session(Resp(body={"success": True, "results": [{"product_id": 2, "score": 78},
                                                              {"product_id": 1, "score": 91.5}]}))
    hits = tt.TCGTracking(session=session).scan(b"JPEGDATA")
    assert [(h.product_id, h.score) for h in hits] == [(1, 91.5), (2, 78.0)]
    method, url, kw = session.calls[0]
    assert method == "POST" and url == "https://openapi.tcgtracking.com/v1/scan"
    assert kw["data"] == {"game_id": 3, "limit": 5}
    assert kw["files"]["image"] == ("card.jpg", b"JPEGDATA", "image/jpeg")


def test_scores_on_a_zero_to_one_scale_become_percentages_and_junk_rows_are_skipped():
    session = Session(Resp(body={"results": [{"product_id": 7, "score": 0.9}, {"score": 50}, "x",
                                             {"product_id": "abc", "score": 1}]}))
    hits = tt.TCGTracking(session=session).scan(b"x")
    assert [(h.product_id, round(h.score)) for h in hits] == [(7, 90)]


@pytest.mark.parametrize("status", [400, 413, 422, 429, 503, 500])
def test_http_errors_become_readable_messages(status):
    with pytest.raises(tt.ScanError) as exc:
        tt.TCGTracking(session=Session(Resp(status))).scan(b"x")
    assert exc.value.args[0] and "Traceback" not in exc.value.args[0]


def test_one_retry_on_gateway_errors_and_connection_failures():
    ok = Resp(body={"success": True, "results": [{"product_id": 1, "score": 80}]})
    assert tt.TCGTracking(session=Session(Resp(502), ok)).scan(b"x")[0].product_id == 1
    assert tt.TCGTracking(session=Session(requests.ConnectionError(), ok)).scan(b"x")
    with pytest.raises(tt.ScanError, match="reach"):
        tt.TCGTracking(session=Session(requests.ConnectionError(), requests.ConnectionError())).scan(b"x")
    s = Session(Resp(422))                               # client errors are not retried
    with pytest.raises(tt.ScanError):
        tt.TCGTracking(session=s).scan(b"x")
    assert len(s.calls) == 1


def test_unsuccessful_or_unreadable_responses():
    for resp in (Resp(body={"success": False}), Resp(body=None), Resp(body=["nope"])):
        with pytest.raises(tt.ScanError):
            tt.TCGTracking(session=Session(resp)).scan(b"x")


@pytest.mark.parametrize("body", [
    {"id": 5, "name": "Pikachu ex - 057/191", "clean_name": "Pikachu ex", "set_name": "Surging Sparks",
     "set_abbr": "SSP", "number": "057/191", "rarity": "Double Rare"},
    {"product": {"id": 5, "name": "Pikachu ex", "set_name": "Surging Sparks", "set_abbr": "SSP", "number": "057/191"}},
    {"data": [{"name": "Pikachu ex - 057/191", "set_name": "Surging Sparks", "set_abbreviation": "SSP"}]},
], ids=["flat", "wrapped", "wrapped list"])
def test_product_shapes_are_all_understood(body):
    product = tt.TCGTracking(session=Session(Resp(body=body))).product(5)
    assert product["base_name"] == "Pikachu ex" and product["number"] == "057/191"
    assert product["set_abbr"] == "SSP" and product["set_name"] == "Surging Sparks" and product["id"] == 5


def test_products_are_remembered():
    session = Session(Resp(body={"name": "Pikachu", "number": "25"}))
    client = tt.TCGTracking(session=session)
    assert client.product(9)["base_name"] == "Pikachu" and client.product(9)["number"] == "25"
    assert len(session.calls) == 1
    assert tt.TCGTracking(session=Session(Resp(body={"nothing": 1}))).product(10) is None


@pytest.mark.parametrize("raw, base, number", [
    ("Pikachu ex - 057/191", "Pikachu ex", "057/191"),
    ("Charizard (Reverse Holofoil) - 4/102", "Charizard", "4/102"),
    ("Pikachu V - SWSH039", "Pikachu V", "SWSH039"),
    ("Professor's Research (Professor Oak) - 147/165", "Professor's Research", "147/165"),
    ("Mewtwo & Mew-GX - 71/236", "Mewtwo & Mew-GX", "71/236"),
    ("Ho-Oh ex", "Ho-Oh ex", ""), ("Porygon-Z", "Porygon-Z", ""), ("Mr. Mime", "Mr. Mime", ""),
    ("Magnemite - 53/102 (1st Edition)", "Magnemite", "53/102"), ("", "", ""),
])
def test_names_are_reduced_to_the_card_name(raw, base, number):
    assert tt.base_name(raw) == base and tt.number_from_name(raw) == number
