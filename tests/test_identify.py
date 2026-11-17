"""Scoring a card description (name, number, set) against published card records."""
from app import identify
from app.identify import Reading


def card(cid, name, number, set_code, set_name="X", hp="60"):
    return {"id": cid, "name": name, "clean_name": name, "card_number": number,
            "set_code": set_code, "set_name": set_name, "hp": hp}


def test_exact_match_beats_other_printings():
    reading = Reading(name="Pikachu", number="025/185", set_total="185", set_code="SVI", hp="60")
    ranked = identify.rank(reading, [
        card("a", "Pikachu", "025/185", "SVI"),
        card("b", "Pikachu", "058/198", "SVI"),
        card("c", "Pikachu", "025/165", "MEW"),
    ])
    assert ranked[0].card["id"] == "a"
    assert identify.is_confident(ranked)


def test_leading_zeros_and_prefixes_compare_equal():
    reading = Reading(name="Pikachu", number="SWSH039")
    assert "number" in identify.score(reading, card("a", "Pikachu", "SWSH39", "SWSH")).reasons
    reading = Reading(name="Pikachu", number="25/185")
    assert "number" in identify.score(reading, card("a", "Pikachu", "025/185", "SVI")).reasons


def test_ambiguous_is_not_confident():
    reading = Reading(name="Pikachu")
    ranked = identify.rank(reading, [card("a", "Pikachu", "1/10", "A"), card("b", "Pikachu", "2/10", "B")])
    assert not identify.is_confident(ranked)
