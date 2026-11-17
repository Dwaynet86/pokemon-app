"""PokeWallet client (prices only): parsing, rate-limit handling, error mapping."""
import pytest

from app import pokewallet as pw

RAW = {"id": "pk_1", "card_info": {"name": "Pikachu ex", "clean_name": "Pikachu ex", "set_name": "Surging Sparks",
                                   "set_code": "SSP", "set_id": 99, "card_number": "057/191", "hp": 190,
                                   "product_type": "card"},
       "tcgplayer": {"prices": [{"sub_type_name": "Holofoil", "market_price": "4.5", "mid_price": 5},
                                {"sub_type_name": "Normal", "market_price": None, "mid_price": 1.25}]},
       "cardmarket": {"prices": [{"variant_type": "Holo", "trend": 3.9}]}}


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def fresh_rate():
    pw.rate.hour_remaining = pw.rate.day_remaining = pw.rate.hour_limit = pw.rate.day_limit = None


def test_normalize_card_keeps_only_usd_prices():
    c = pw.normalize_card(RAW)
    assert c["id"] == "pk_1" and c["card_number"] == "057/191" and c["set_code"] == "SSP"
    assert c["prices"] == {"Holofoil": 4.5, "Normal": 1.25}     # EUR CardMarket not mixed in


def test_sealed_and_garbage_are_skipped():
    assert pw.normalize_card({**RAW, "card_info": {**RAW["card_info"], "product_type": "sealed"}}) is None
    assert pw.normalize_card({"card_info": {}}) is None and pw.normalize_card("x") is None


def test_search_sends_key_and_tracks_limits():
    session = Session(Resp(body={"results": [RAW], "pagination": {"total": 1, "total_pages": 1}},
                           headers={"X-RateLimit-Remaining-Hour": "42", "X-RateLimit-Limit-Hour": "100"}))
    out = pw.PokeWallet("k", session=session).search("pikachu")
    assert len(out["cards"]) == 1 and pw.rate.hour_remaining == 42 and pw.rate.hour_limit == 100
    url, kw = session.calls[0]
    assert url.endswith("/search") and kw["headers"]["X-API-Key"] == "k" and kw["params"]["q"] == "pikachu"


@pytest.mark.parametrize("status, exc", [(429, pw.RateLimited), (401, pw.PokeWalletError),
                                         (403, pw.PokeWalletError), (404, pw.PokeWalletError),
                                         (500, pw.PokeWalletError)])
def test_http_errors_map_to_safe_exceptions(status, exc):
    with pytest.raises(exc):
        pw.PokeWallet("k", session=Session(Resp(status))).search("x")


def test_missing_key_and_exhausted_budget_make_no_request():
    session = Session()
    with pytest.raises(pw.NotConfigured):
        pw.PokeWallet("", session=session).search("x")
    pw.rate.hour_remaining = 0
    with pytest.raises(pw.RateLimited):
        pw.PokeWallet("k", session=session).search("x")
    assert session.calls == []
