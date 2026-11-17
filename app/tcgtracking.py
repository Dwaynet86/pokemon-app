"""TCG Tracking Open API client (https://openapi.tcgtracking.com): the card image scanner.

`POST /v1/scan` compares a photo of a card with the artwork of every card in a game and returns
the closest products as {product_id, score}. It needs no API key. The product ids are TCGplayer
product ids, so each hit is looked up (`GET /v1/products/{id}`) to learn its name, number and set,
and that is then matched to a card in our TCGdex catalog (see photoid.py).

Documented limits (from the API's own page): the image must be an upright, mostly full-card crop
of at most 100 KB; there is no border detection; no rate limit is published. Parsing here is
deliberately tolerant, because the product endpoint's exact shape isn't documented for Pokemon.
"""
import re
import threading
import time
from dataclasses import dataclass

import requests

USER_AGENT = "pokemon-card-collection/1.0"
PRODUCT_TTL = 6 * 3600          # seconds a looked-up product is remembered by this process
POKEMON_GAME_ID = 3


class ScanError(Exception):
    """Any failure scanning or looking up a card. The message is safe to show to the user."""


@dataclass
class ScanHit:
    product_id: int
    score: float          # match percentage, 0-100


class TCGTracking:
    def __init__(self, base_url: str = "https://openapi.tcgtracking.com/v1", game_id: int = POKEMON_GAME_ID,
                 timeout: int = 30, session=None):
        self.base_url = base_url.rstrip("/")
        self.game_id = game_id
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    # ---- transport ----------------------------------------------------------
    def _request(self, method: str, path: str, retries: int = 1, **kwargs):
        last: ScanError | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last = ScanError("Couldn't reach the scan service.")
                last.__cause__ = exc
            else:
                if resp.status_code in (502, 504):
                    last = ScanError("The scan service is busy. Try again in a moment.")
                elif resp.status_code < 400:
                    return resp
                else:
                    raise _http_error(resp.status_code)
            if attempt < retries:
                time.sleep(1.0)
        raise last

    def _json(self, resp):
        try:
            return resp.json()
        except ValueError as exc:
            raise ScanError("The scan service sent a response that couldn't be read.") from exc

    # ---- endpoints ----------------------------------------------------------
    def scan(self, jpeg: bytes, limit: int = 5) -> list[ScanHit]:
        """Match a photo of a card. `jpeg` must be upright, mostly full-card, and at most 100 KB."""
        resp = self._request("POST", "/scan", data={"game_id": self.game_id, "limit": limit},
                             files={"image": ("card.jpg", jpeg, "image/jpeg")})
        data = self._json(resp)
        if not isinstance(data, dict) or data.get("success") is False:
            raise ScanError("The scan service couldn't match that photo.")
        hits = []
        for row in data.get("results") or []:
            if not isinstance(row, dict):
                continue
            try:
                pid, score = int(row["product_id"]), float(row.get("score", 0))
            except (KeyError, TypeError, ValueError):
                continue
            hits.append(ScanHit(pid, score * 100 if 0 < score <= 1 else score))   # tolerate a 0-1 scale
        return sorted(hits, key=lambda h: -h.score)

    def product(self, product_id: int) -> dict | None:
        """Name, number and set of a scanned product (remembered for a few hours)."""
        with _lock:
            hit = _products.get(product_id)
        if hit and time.monotonic() - hit[0] < PRODUCT_TTL:
            return hit[1]
        resp = self._request("GET", f"/products/{int(product_id)}")
        product = normalize_product(self._json(resp), product_id)
        if product:
            with _lock:
                _products[product_id] = (time.monotonic(), product)
        return product

    def categories(self) -> list[dict]:
        """Games the API covers (to confirm Pokemon's id)."""
        data = self._json(self._request("GET", "/categories"))
        rows = data if isinstance(data, list) else (data.get("categories") or data.get("data") or []) \
            if isinstance(data, dict) else []
        return [r for r in rows if isinstance(r, dict)]


_products: dict[int, tuple[float, dict | None]] = {}
_lock = threading.Lock()


def _http_error(status: int) -> ScanError:
    return ScanError({
        400: "The scan service rejected that photo.",
        413: "That photo is too large to scan.",
        422: "The scan service couldn't read that photo.",
        429: "The scan service is rate limiting requests. Try again in a minute.",
        503: "The scan service has no card data available right now.",
    }.get(status, f"The scan service returned an error ({status})."))


# ---- normalisation ---------------------------------------------------------------

_NUMBER_SUFFIX = re.compile(r"\s*-\s*([A-Za-z]{0,6}\d+[A-Za-z]?(?:\s*/\s*[A-Za-z]{0,6}\d+)?)\s*(?=\(|$)")
_PARENS = re.compile(r"\s*\([^)]*\)")


def base_name(name: str) -> str:
    """'Pikachu ex - 057/191 (Reverse Holofoil)' -> 'Pikachu ex'."""
    text = _PARENS.sub("", name or "")
    text = _NUMBER_SUFFIX.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def number_from_name(name: str) -> str:
    match = _NUMBER_SUFFIX.search(_PARENS.sub("", name or ""))
    return match.group(1).replace(" ", "") if match else ""


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _product_dict(raw) -> dict | None:
    """The product object inside a response that may or may not wrap it."""
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not isinstance(raw, dict):
        return None
    if raw.get("name") or raw.get("clean_name"):
        return raw
    for key in ("product", "data", "result", "products"):
        inner = _product_dict(raw.get(key))
        if inner:
            return inner
    return None


def normalize_product(raw, product_id: int | None = None) -> dict | None:
    p = _product_dict(raw)
    if p is None:
        return None
    full = _text(p.get("name")) or _text(p.get("clean_name"))
    clean = _text(p.get("clean_name")) or full
    number = _text(p.get("number")) or number_from_name(full)
    return {
        "id": product_id if product_id is not None else p.get("id"),
        "name": full, "base_name": base_name(clean) or base_name(full), "number": number,
        "set_name": _text(p.get("set_name")), "set_abbr": _text(p.get("set_abbr") or p.get("set_abbreviation")),
        "rarity": _text(p.get("rarity")),
    }
