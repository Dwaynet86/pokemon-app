"""Card finishes (Normal, Holofoil, Reverse Holofoil, ...): ordering, short labels and "master set" progress.

Pure helpers, so the rules are easy to test. A card row is the dict from `catalog_repository.set_cards`:
`variants` are the finishes the card exists in, `owned_variants` the ones this collection owns, `qty` the copies.
"""
ORDER = ["Normal", "Holofoil", "Reverse Holofoil", "1st Edition", "1st Edition Holofoil", "Promo"]
SHORT = {"Normal": "N", "Holofoil": "H", "Reverse Holofoil": "R", "1st Edition": "1st", "1st Edition Holofoil": "1H", "Promo": "P"}


def sort_key(name: str) -> tuple[int, str]:
    return (ORDER.index(name), "") if name in ORDER else (len(ORDER), name)


def options(cards: list[dict]) -> list[str]:
    """Every finish that exists somewhere in these cards, in the usual order."""
    return sorted({v for c in cards for v in (c.get("variants") or [])}, key=sort_key)


def lacks(card: dict, finish: str) -> bool:
    """The card exists in this finish and you don't own that finish yet (whatever else you own)."""
    return finish in (card.get("variants") or []) and finish not in (card.get("owned_variants") or [])


def progress(card: dict, master: bool) -> tuple[int, int]:
    """(owned, total) for one card. Normally a card counts once. In master-set mode every published finish counts.

    A card whose finishes aren't known yet (details not downloaded) counts once either way.
    """
    existing = list(card.get("variants") or [])
    if not master or not existing:
        return (1 if card.get("qty") else 0, 1)
    return (len(set(existing) & set(card.get("owned_variants") or [])), len(existing))


def totals(cards: list[dict], master: bool) -> tuple[int, int]:
    """(owned, total) for a whole expansion."""
    parts = [progress(c, master) for c in cards]
    return sum(p[0] for p in parts), sum(p[1] for p in parts)
