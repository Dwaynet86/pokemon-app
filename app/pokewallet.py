"""PokeWallet API client (https://pokewallet.io/api-docs). Used for PRICES only.

The free plan allows 100 requests/hour and 1000/day, so the app asks for a card's price once
when you add it (and when you press "Refresh price"), then keeps it in Postgres. Card data and
artwork come from TCGdex (see tcgdex.py). Parsing is tolerant: a missing field is None.
"""
import threading

import requests

from .cardnum import number_sort


class PokeWalletError(Exception):
    """Any failure talking to PokeWallet. The message is safe to show to the user."""


class NotConfigured(PokeWalletError):
    pass


class RateLimited(PokeWalletError):
    pass


class _RateState:
    """Last-seen request allowance, from the headers PokeWallet sends (shared by the process)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.hour_remaining: int | None = None
        self.day_remaining: int | None = None
        self.hour_limit: int | None = None
        self.day_limit: int | None = None

    def update(self, headers) -> None:
        def num(name):
            try:
                return int(headers.get(name))
            except (TypeError, ValueError):
                return None
        with self.lock:
            for attr, header in (("hour_remaining", "X-RateLimit-Remaining-Hour"),
                                 ("day_remaining", "X-RateLimit-Remaining-Day"),
                                 ("hour_limit", "X-RateLimit-Limit-Hour"),
                                 ("day_limit", "X-RateLimit-Limit-Day")):
                value = num(header)
                if value is not None:
                    setattr(self, attr, value)

    def exhausted(self, reserve: int = 0) -> bool:
        with self.lock:
            return ((self.hour_remaining is not None and self.hour_remaining <= reserve)
                    or (self.day_remaining is not None and self.day_remaining <= reserve))


rate = _RateState()


class PokeWallet:
    def __init__(self, api_key: str, base_url: str = "https://api.pokewallet.io",
                 timeout: int = 20, session=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _json(self, path: str, params: dict | None = None) -> dict:
        if not self.api_key:
            raise NotConfigured("PokeWallet isn't configured. Set POKEWALLET_API_KEY in .env.")
        if rate.exhausted():
            raise RateLimited("The PokeWallet request limit has been reached. Prices will update later.")
        try:
            resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout,
                                    headers={"X-API-Key": self.api_key, "Accept": "application/json"})
        except requests.RequestException as exc:
            raise PokeWalletError("Couldn't reach PokeWallet.") from exc
        rate.update(resp.headers)
        if resp.status_code == 429:
            raise RateLimited("The PokeWallet request limit has been reached. Prices will update later.")
        if resp.status_code in (401, 403):
            raise PokeWalletError("PokeWallet rejected the API key (or the endpoint needs a paid plan).")
        if resp.status_code == 404:
            raise PokeWalletError("PokeWallet has no record of that.")
        if resp.status_code >= 400:
            raise PokeWalletError(f"PokeWallet returned an error ({resp.status_code}).")
        try:
            data = resp.json()
        except ValueError as exc:
            raise PokeWalletError("PokeWallet sent a response that couldn't be read.") from exc
        return data if isinstance(data, dict) else {"data": data}

    def search(self, query: str, page: int = 1, limit: int = 100) -> dict:
        """Search by name, set code, card number, or set_id + number."""
        data = self._json("/search", {"q": query, "page": page, "limit": limit})
        cards = [c for c in (normalize_card(r) for r in data.get("results") or []) if c]
        paging = data.get("pagination") or {}
        return {"cards": cards, "total": paging.get("total", len(cards)),
                "total_pages": paging.get("total_pages") or 1}


# ---- normalisation -----------------------------------------------------------------

def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def normalize_card(raw: dict) -> dict | None:
    """Flatten one PokeWallet search result into the fields we match and price on."""
    if not isinstance(raw, dict):
        return None
    info = raw.get("card_info") if isinstance(raw.get("card_info"), dict) else raw
    card_id = _text(raw.get("id") or info.get("id"))
    name = _text(info.get("name")) or _text(info.get("clean_name"))
    if not card_id or not name:
        return None
    if (_text(info.get("product_type")) or "card").lower() != "card":
        return None   # sealed product isn't a card

    prices: dict[str, float | None] = {}
    tcg = raw.get("tcgplayer") if isinstance(raw.get("tcgplayer"), dict) else {}
    for row in tcg.get("prices") or []:      # TCGplayer prices are USD; CardMarket (EUR) is ignored
        variant = _text(row.get("sub_type_name"))
        if variant and variant not in prices:
            price = _price(row.get("market_price"))
            prices[variant] = price if price is not None else _price(row.get("mid_price"))

    number = _text(info.get("card_number"))
    return {"id": card_id, "name": name, "clean_name": _text(info.get("clean_name")) or name,
            "card_number": number, "number_sort": number_sort(number), "hp": _text(info.get("hp")),
            "set_name": _text(info.get("set_name")), "set_code": _text(info.get("set_code")),
            "prices": prices}
