"""TCGdex client: parsing against the documented response shapes, retries, image checks."""
import pytest

from app import cardnum, tcgdex as t

SET = {"id": "swsh3", "name": "Darkness Ablaze", "logo": "https://assets.tcgdex.net/en/swsh/swsh3/logo",
       "symbol": "https://assets.tcgdex.net/univ/swsh/swsh3/symbol",
       "cardCount": {"total": 201, "official": 189}, "releaseDate": "2020-08-14",
       "serie": {"id": "swsh", "name": "Sword & Shield"}, "abbreviation": {"official": "DAA"},
       "cards": [{"id": "swsh3-1", "localId": "1", "name": "Butterfree V", "image": "https://assets.tcgdex.net/en/swsh/swsh3/1"},
                 {"id": "swsh3-201", "localId": "201", "name": "Capture Energy", "image": None}]}
CARD = {"category": "Pokemon", "id": "swsh3-136", "illustrator": "x", "image": "https://assets.tcgdex.net/en/swsh/swsh3/136",
        "localId": "136", "name": "Furret", "rarity": "Uncommon", "dexId": [162], "hp": 110, "types": ["Colorless"],
        "set": {"cardCount": {"official": 189, "total": 201}, "id": "swsh3", "name": "Darkness Ablaze",
                "logo": "https://assets.tcgdex.net/en/swsh/swsh3/logo"},
        "variants": {"firstEdition": False, "holo": False, "normal": True, "reverse": True, "wPromo": False},
        "stage": "Stage1"}


class Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content = status, body, content

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def get(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)


def test_normalize_set():
    s = t.normalize_set(SET)
    assert (s["tcgdex_id"], s["series"], s["set_code"], s["card_count"], s["card_count_official"]) == \
           ("swsh3", "Sword & Shield", "DAA", 201, 189)
    assert s["logo_url"].endswith("/logo.webp") and s["release_date"].year == 2020
    assert t.normalize_set({"name": "x"}) is None


def test_get_set_returns_all_cards_in_one_request():
    session = Session(Resp(body=SET))
    info, cards = t.TCGdex(session=session).get_set("swsh3")
    assert len(session.calls) == 1 and session.calls[0][0].endswith("/en/sets/swsh3")
    assert [c["card_number"] for c in cards] == ["1/189", "201/189"]      # printed style, secret rares included
    assert cards[0]["image_base"].endswith("/swsh3/1") and cards[1]["image_base"] is None
    assert all(not c["has_detail"] and c["set_tcgdex_id"] == "swsh3" for c in cards)


def test_normalize_detail():
    c = t.normalize_detail(CARD)
    assert c["has_detail"] and c["card_type"] == "Pokemon" and c["dex_ids"] == [162] and c["hp"] == "110"
    assert c["variants"] == ["Normal", "Reverse Holofoil"] and c["card_number"] == "136/189"
    assert c["set_info"]["name"] == "Darkness Ablaze"


def test_set_id_of_card():
    assert t.set_id_of("swsh3-136", "136") == "swsh3"
    assert t.set_id_of("sv04.5-001", "001") == "sv04.5"


def test_search_by_name_uses_filter_param():
    session = Session(Resp(body=[{"id": "swsh3-136", "localId": "136", "name": "Furret", "image": None}]))
    out = t.TCGdex(session=session).search_cards("furret")
    assert out[0]["id"] == "swsh3-136" and session.calls[0][1]["params"] == {"name": "furret"}


def test_errors_and_retries():
    with pytest.raises(t.TCGdexError):
        t.TCGdex(session=Session(Resp(404))).get_card("nope")
    ok = Session(Resp(500), Resp(body=CARD))                       # one 5xx is retried
    assert t.TCGdex(session=ok).get_card("swsh3-136")["name"] == "Furret"
    import requests
    down = Session(*[requests.ConnectionError()] * 3)
    with pytest.raises(t.TCGdexError):
        t.TCGdex(session=down).list_sets()


def test_download_only_accepts_real_webp():
    assert t.TCGdex(session=Session(Resp(content=b"RIFF\0\0\0\0WEBPdata"))).download("u").startswith(b"RIFF")
    with pytest.raises(t.TCGdexError):
        t.TCGdex(session=Session(Resp(content=b"<html>error</html>"))).download("u")


def test_cardnum():
    assert cardnum.normalize_number("025/185") == "25" and cardnum.normalize_number("TG05/TG30") == "TG5"
    assert cardnum.number_total("025/185") == "185" and cardnum.number_total("SWSH039") == ""
    assert cardnum.number_sort("SWSH039") == 39
    assert cardnum.display_number("25", 185) == "25/185" and cardnum.display_number("SWSH039", 185) == "SWSH039"
