"""Match a card description (name, number, set) to published TCGdex card records.

Used by the photo flow (photoid.py turns a scan hit into a Reading) and by manual search. The
match is scored on the fields below and the user confirms the result, so stored details always
come from the published record.
"""
import re
from dataclasses import dataclass, field

from .cardnum import normalize_number, number_total


@dataclass
class Reading:
    name: str = ""
    number: str = ""
    set_total: str = ""
    set_code: str = ""
    set_name: str = ""
    hp: str = ""


@dataclass
class Candidate:
    card: dict
    score: int
    reasons: list[str] = field(default_factory=list)


# ---- Matching --------------------------------------------------------------------

def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold().replace("é", "e")).strip()


def score(reading: Reading, card: dict) -> Candidate:
    """How well a published card record fits what was read from the photo (max ~100)."""
    pts, why = 0, []
    want, have = _norm(reading.name), _norm(card.get("clean_name") or card.get("name"))
    if want and want == have:
        pts += 40; why.append("name")
    elif want and have and (want in have or have in want):
        pts += 25; why.append("similar name")
    elif want and have:
        a, b = set(want.split()), set(have.split())
        pts += round(20 * len(a & b) / max(len(a | b), 1))

    if reading.number and normalize_number(reading.number) == normalize_number(card.get("card_number")):
        pts += 35; why.append("number")
    total = reading.set_total or number_total(reading.number)
    card_total = number_total(card.get("card_number"))
    if total and card_total and total == card_total:
        pts += 10; why.append("set size")
    if reading.set_code and _norm(card.get("set_code")) == _norm(reading.set_code):
        pts += 12; why.append("set code")
    elif reading.set_name and _norm(card.get("set_name")) == _norm(reading.set_name):
        pts += 10; why.append("set")
    if reading.hp and str((card.get("hp") or "")).strip() == reading.hp:
        pts += 5; why.append("HP")
    return Candidate(card, pts, why)


def rank(reading: Reading, cards: list[dict], limit: int = 6) -> list[Candidate]:
    seen, scored = set(), []
    for card in cards:
        if card["id"] in seen:
            continue
        seen.add(card["id"])
        scored.append(score(reading, card))
    scored.sort(key=lambda c: -c.score)
    return scored[:limit]


def is_confident(ranked: list[Candidate]) -> bool:
    """A clear winner: strong match and a real gap to the runner-up."""
    if not ranked or ranked[0].score < 80:
        return False
    return len(ranked) == 1 or ranked[0].score - ranked[1].score >= 12
