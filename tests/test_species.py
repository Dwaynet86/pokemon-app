import pytest

from app import species


@pytest.mark.parametrize("name, card_type, expected", [
    ("Charizard ex", None, [6]), ("Pikachu & Zekrom-GX", None, [25, 644]),
    ("Mr. Mime", None, [122]), ("Mime Jr.", None, [439]), ("Rocket's Mewtwo", None, [150]),
    ("Flabébé", None, [669]), ("Nidoran♀", None, [29]), ("Farfetch'd", None, [83]),
    ("Professor's Research", None, []), ("Pikachu", "Trainer", []), ("Mew", "Basic Energy", []),
])
def test_species_for_card(name, card_type, expected):
    assert species.species_for_card(name, card_type) == expected


def test_pokedex_is_complete():
    rows = species.load()
    assert len(rows) == 1025 and rows[0][:2] == (1, "Bulbasaur")
    assert species.generation_of(25) == 1 and species.generation_of(1025) == 9
