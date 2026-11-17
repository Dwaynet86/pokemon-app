"""TCGdex pricing: parse the TCGplayer block of a card record."""
import pytest

from app import tcgdex as t


def block(**rows):
    return {"tcgplayer": {"updated": "2026-11-10T00:00:00Z", "unit": "USD", **rows},
            "cardmarket": {"unit": "EUR", "avg": 9.9, "trend": 9.9}}


def row(market=None, mid=None):
    return {"lowPrice": 1.0, "midPrice": mid, "highPrice": 9.0, "marketPrice": market, "directLowPrice": None}


def test_market_price_per_finish_with_the_docs_key_names():
    prices = t.normalize_prices(block(normal=row(market=0.5), reverse=row(market=1.25), holo=row(market=4.5)))
    assert prices == {"Normal": 0.5, "Reverse Holofoil": 1.25, "Holofoil": 4.5}


def test_longer_key_names_and_first_edition_are_understood():
    prices = t.normalize_prices(block(**{"reverse-holofoil": row(market=2.0), "holofoil": row(market=3.0),
                                         "1st-edition-holofoil": row(market=50.0)}))
    assert prices == {"Reverse Holofoil": 2.0, "Holofoil": 3.0, "1st Edition": 50.0}


def test_mid_price_is_used_when_there_is_no_market_price_and_junk_is_skipped():
    prices = t.normalize_prices(block(normal=row(market=None, mid=0.8), holo=row(market=0), reverse="n/a", mystery=row(market=5)))
    assert prices == {"Normal": 0.8}


@pytest.mark.parametrize("pricing", [None, {}, "x", {"cardmarket": {"avg": 3}}, {"tcgplayer": None},
                                     {"tcgplayer": {"unit": "EUR", "normal": {"marketPrice": 1}}}])
def test_no_usable_tcgplayer_prices(pricing):
    assert t.normalize_prices(pricing) == {}          # Cardmarket (EUR) is never used for the USD estimate


def test_full_card_record_carries_its_prices_and_a_missing_block_is_fine():
    raw = {"id": "swsh3-136", "localId": "136", "name": "Furret", "variants": {"normal": True},
           "pricing": block(normal=row(market=0.25))}
    assert t.normalize_detail(raw)["prices"] == {"Normal": 0.25}
    assert t.normalize_detail({"id": "x-1", "localId": "1", "name": "Y"})["prices"] == {}
