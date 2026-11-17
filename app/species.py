"""The National Pokedex and card-name -> Pokemon matching."""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "species.json"

# Card types that never feature a Pokemon, even when the name contains one.
NON_POKEMON_TYPES = ("trainer", "energy")

# Generation number -> (first dex number, last dex number), for the filter on the Pokemon page.
GENERATIONS = {1: (1, 151), 2: (152, 251), 3: (252, 386), 4: (387, 493), 5: (494, 649),
               6: (650, 721), 7: (722, 809), 8: (810, 905), 9: (906, 1025)}
GENERATION_NAMES = {1: "Kanto", 2: "Johto", 3: "Hoenn", 4: "Sinnoh", 5: "Unova",
                    6: "Kalos", 7: "Alola", 8: "Galar", 9: "Paldea"}


def _fold(text: str) -> str:
    """Lowercase, straighten apostrophes, drop accents (Flabébé -> flabebe)."""
    text = text.replace("’", "'").replace("‘", "'")
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text.casefold()


@lru_cache(maxsize=1)
def load() -> list[tuple[int, str, int]]:
    return [tuple(row) for row in json.loads(DATA_FILE.read_text(encoding="utf-8"))]


@lru_cache(maxsize=1)
def _matcher() -> tuple[re.Pattern, dict[str, int]]:
    keys: dict[str, int] = {}
    for dex, name, _gen in load():
        keys[_fold(name)] = dex
    keys["nidoran f"] = keys.get("nidoran♀", 29)       # cards often spell the symbol out
    keys["nidoran m"] = keys.get("nidoran♂", 32)
    # Longest first so "Mr. Mime" beats "Mime" and "Mime Jr." beats "Mime".
    alternatives = "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])"), keys


def species_for_card(name: str, card_type: str | None = None) -> list[int]:
    """Dex numbers of the Pokemon named on a card (empty for Trainers and Energy)."""
    if card_type and any(t in card_type.casefold() for t in NON_POKEMON_TYPES):
        return []
    pattern, keys = _matcher()
    found: list[int] = []
    for match in pattern.finditer(_fold(name)):
        dex = keys[match.group(0)]
        if dex not in found:
            found.append(dex)
    return found


def generation_of(dex: int) -> int:
    for gen, (lo, hi) in GENERATIONS.items():
        if lo <= dex <= hi:
            return gen
    return 9
