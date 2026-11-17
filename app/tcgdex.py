"""TCGdex client (https://tcgdex.dev): sets, cards, Pokedex numbers and artwork.

TCGdex needs no API key and publishes no hard rate limit (it asks bulk users to cache
locally, which this app does). Card artwork lives on a static host, so downloading it
doesn't touch the API at all.

Endpoints used (language is configured once, e.g. "en"):
    GET /v2/{lang}/sets                  every expansion (brief)
    GET /v2/{lang}/sets/{id}             one expansion with ALL its cards (id, localId, name, image)
    GET /v2/{lang}/cards/{id}            one card in full (rarity, hp, dexId, variants, prices, ...)
    GET /v2/{lang}/cards?name=pikachu    cards by name (partial, case-insensitive), not paginated
    {card.image}/{low|high}.webp         artwork (245x337 / 600x825)
"""
import re
import time
from datetime import date, datetime
from urllib.parse import quote

import requests

from .cardnum import display_number, number_sort

USER_AGENT = "pokemon-card-collection/1.0"

# TCGdex variant flag -> the label used for owned cards (matches PokeWallet's price labels)
VARIANT_LABELS = (("normal", "Normal"), ("reverse", "Reverse Holofoil"), ("holo", "Holofoil"),
                  ("firstEdition", "1st Edition"), ("wPromo", "Promo"))


# TCGplayer price-block key (lower-cased, punctuation removed) -> the label used for owned cards.
# The docs show "reverse" and "holo", other tools use the longer names, so accept both.
PRICE_KEYS = {"normal": "Normal", "unlimitednormal": "Normal",
              "reverse": "Reverse Holofoil", "reverseholo": "Reverse Holofoil", "reverseholofoil": "Reverse Holofoil",
              "holo": "Holofoil", "holofoil": "Holofoil", "unlimitedholofoil": "Holofoil",
              "1steditionnormal": "1st Edition", "1steditionholofoil": "1st Edition", "1stedition": "1st Edition"}


class TCGdexError(Exception):
    """Any failure talking to TCGdex. The message is safe to show to the user."""


class TCGdex:
    def __init__(self, base_url: str = "https://api.tcgdex.net/v2", lang: str = "en",
                 timeout: int = 20, session=None):
        self.base = f"{base_url.rstrip('/')}/{lang}"
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def _get(self, url: str, params: dict | None = None, retries: int = 2):
        last = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last = TCGdexError("Couldn't reach TCGdex.")
                last.__cause__ = exc
            else:
                if resp.status_code == 404:
                    raise TCGdexError("TCGdex has no record of that.")
                if resp.status_code < 400:
                    return resp
                last = TCGdexError(f"TCGdex returned an error ({resp.status_code}).")
                if resp.status_code < 500 and resp.status_code != 429:
                    raise last
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        raise last

    def _json(self, path: str, params: dict | None = None):
        try:
            return self._get(f"{self.base}{path}", params).json()
        except ValueError as exc:
            raise TCGdexError("TCGdex sent a response that couldn't be read.") from exc

    # ---- endpoints ----------------------------------------------------------
    def list_sets(self) -> list[dict]:
        data = self._json("/sets")
        return [s for s in (normalize_set(r) for r in data if isinstance(r, dict)) if s] if isinstance(data, list) else []

    def get_set(self, set_id: str) -> tuple[dict | None, list[dict]]:
        """One expansion and every card in it (one request)."""
        data = self._json(f"/sets/{quote(set_id, safe='')}")
        if not isinstance(data, dict):
            raise TCGdexError("TCGdex sent a response that couldn't be read.")
        info = normalize_set(data)
        cards = [c for c in (normalize_resume(r, info) for r in data.get("cards") or []) if c]
        return info, cards

    def get_card(self, card_id: str) -> dict | None:
        data = self._json(f"/cards/{quote(card_id, safe='')}")
        return normalize_detail(data) if isinstance(data, dict) else None

    def search_cards(self, name: str) -> list[dict]:
        """Cards whose name contains `name`. Brief records only (no set details, no rarity)."""
        data = self._json("/cards", {"name": name})
        return [c for c in (normalize_resume(r) for r in data if isinstance(r, dict)) if c] if isinstance(data, list) else []

    def download(self, url: str) -> bytes:
        """Fetch a static asset (artwork). Checked to really be a WebP so error pages aren't cached."""
        data = self._get(url).content
        if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            raise TCGdexError("TCGdex didn't return an image.")
        return data


# ---- normalisation -----------------------------------------------------------------

def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _asset(url, ext: str = "webp") -> str | None:
    """Set logos/symbols come without an extension."""
    url = _text(url)
    if not url:
        return None
    return url if url.rsplit("/", 1)[-1].count(".") else f"{url}.{ext}"


def _date(value) -> date | None:
    text = _text(value)
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date() if text else None
    except ValueError:
        return None


def set_id_of(card_id: str, local_id: str | None) -> str | None:
    """'swsh3-136' + '136' -> 'swsh3'."""
    if local_id and card_id.endswith(f"-{local_id}"):
        return card_id[: -(len(local_id) + 1)] or None
    return card_id.rsplit("-", 1)[0] if "-" in card_id else None


def normalize_set(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    set_id, name = _text(raw.get("id")), _text(raw.get("name"))
    if not set_id or not name:
        return None
    counts = raw.get("cardCount") if isinstance(raw.get("cardCount"), dict) else {}
    abbr = raw.get("abbreviation") or raw.get("abbreviations")
    serie = raw.get("serie") if isinstance(raw.get("serie"), dict) else {}
    return {
        "tcgdex_id": set_id, "name": name, "series": _text(serie.get("name")),
        "set_code": _text(abbr.get("official")) if isinstance(abbr, dict) else None,
        "release_date": _date(raw.get("releaseDate")),
        "card_count": _int(counts.get("total")), "card_count_official": _int(counts.get("official")),
        "logo_url": _asset(raw.get("logo")), "symbol_url": _asset(raw.get("symbol")),
    }


def normalize_resume(raw: dict, set_info: dict | None = None) -> dict | None:
    """A brief card record (from a set or a search): number, name and image only."""
    if not isinstance(raw, dict):
        return None
    card_id, name = _text(raw.get("id")), _text(raw.get("name"))
    if not card_id or not name:
        return None
    local_id = _text(raw.get("localId"))
    official = (set_info or {}).get("card_count_official")
    return {
        "id": card_id, "set_tcgdex_id": (set_info or {}).get("tcgdex_id") or set_id_of(card_id, local_id),
        "local_id": local_id, "name": name, "clean_name": name,
        "card_number": display_number(local_id, official), "number_sort": number_sort(local_id),
        "image_base": _text(raw.get("image")), "has_detail": False,
        "rarity": None, "card_type": None, "hp": None, "stage": None,
        "variants": [], "data": {}, "dex_ids": None, "set_info": set_info,
    }


def _money(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def normalize_prices(pricing) -> dict[str, float]:
    """{finish: USD market price} from a card's `pricing` block. Uses TCGplayer only (Cardmarket is EUR).

    Market price, or the mid price when there is no market price. Empty when the card has no TCGplayer prices.
    """
    block = pricing.get("tcgplayer") if isinstance(pricing, dict) else None
    if not isinstance(block, dict) or (_text(block.get("unit")) or "USD").upper() != "USD":
        return {}
    prices: dict[str, float] = {}
    for key, row in block.items():
        label = PRICE_KEYS.get(re.sub(r"[^a-z0-9]", "", str(key).lower()))
        if label and label not in prices and isinstance(row, dict):
            price = _money(row.get("marketPrice"))
            price = price if price is not None else _money(row.get("midPrice"))
            if price is not None:
                prices[label] = price
    return prices


def normalize_detail(raw: dict) -> dict | None:
    """A full card record, including rarity, HP, variants and Pokedex numbers."""
    card = normalize_resume(raw)
    if card is None:
        return None
    set_raw = raw.get("set") if isinstance(raw.get("set"), dict) else {}
    set_info = normalize_set(set_raw)
    if set_info:
        card["set_info"], card["set_tcgdex_id"] = set_info, set_info["tcgdex_id"]
        card["card_number"] = display_number(card["local_id"], set_info["card_count_official"])
    flags = raw.get("variants") if isinstance(raw.get("variants"), dict) else {}
    dex = raw.get("dexId")
    card.update(
        prices=normalize_prices(raw.get("pricing")),
        has_detail=True, rarity=_text(raw.get("rarity")), card_type=_text(raw.get("category")),
        hp=_text(raw.get("hp")), stage=_text(raw.get("stage")),
        variants=[label for key, label in VARIANT_LABELS if flags.get(key)],
        dex_ids=[d for d in (_int(x) for x in dex) if d] if isinstance(dex, list) else [],
        data={k: raw.get(k) for k in ("types", "illustrator", "evolveFrom", "regulationMark", "suffix")
              if raw.get(k) is not None},
    )
    return card
