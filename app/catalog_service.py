"""Glue between TCGdex, the Postgres cache, the artwork cache on disk, and PokeWallet prices.

Everything fetched is stored locally, so each expansion is downloaded once:
  * the card list of an expansion is ONE request,
  * artwork comes from TCGdex's static image host (not the API) and is saved to disk,
  * full card records (rarity, HP, variants, Pokedex numbers) are filled in per card.
"""
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from urllib.parse import quote

from flask import current_app

from . import catalog_repository as catalog, identify, images
from .cardnum import normalize_number
from .pokewallet import PokeWallet, PokeWalletError
from .tcgdex import TCGdex, TCGdexError

log = logging.getLogger(__name__)
SIZES = ("low", "high")


def tcgdex() -> TCGdex:
    cfg = current_app.config
    return TCGdex(cfg["TCGDEX_BASE_URL"], cfg["TCGDEX_LANG"], cfg["TCGDEX_TIMEOUT"])


def pokewallet() -> PokeWallet:
    cfg = current_app.config
    return PokeWallet(cfg["POKEWALLET_API_KEY"], cfg["POKEWALLET_BASE_URL"], cfg["POKEWALLET_TIMEOUT"])


def prices_configured() -> bool:
    """True when the PokeWallet fallback is set up (TCGdex prices need no key)."""
    return bool(current_app.config["POKEWALLET_API_KEY"])


# ---- Sets and cards ---------------------------------------------------------------------

def ensure_sets(force: bool = False) -> int:
    """Make sure the list of expansions is cached (one request, only when empty or forced)."""
    if not force and catalog.sets_known():
        return 0
    return catalog.save_sets(tcgdex().list_sets())


def ensure_set_cards(set_pk: int, force: bool = False) -> dict:
    """Make sure an expansion's card list is cached (one request the first time, none after)."""
    row = catalog.get_set(set_pk)
    if row is None:
        raise TCGdexError("Unknown expansion.")
    if row["cards_fetched_at"] and not force:
        return row
    info, cards = tcgdex().get_set(row["tcgdex_id"])
    if not cards:
        raise TCGdexError("TCGdex returned no cards for that expansion.")
    from .db import get_conn
    with get_conn() as conn:
        if info:
            catalog.upsert_set(conn, info)
        catalog.save_resumes(cards, set_pk, conn=conn)
        conn.execute("UPDATE card_sets SET cards_fetched_at = now() WHERE id = %s", (set_pk,))
    return catalog.get_set(set_pk)


def ensure_detail(card_id: str) -> dict | None:
    """Make sure a card has its full record (one request if it doesn't yet)."""
    row = catalog.get_card(card_id)
    if row is None or row["has_detail"]:
        return row
    detail = tcgdex().get_card(card_id)
    if detail:
        catalog.save_detail(detail)
    return catalog.get_card(card_id)


def split_query(text: str) -> tuple[str, str]:
    """'Charizard ex 125/197' -> ('Charizard ex', '125'). The number part is optional."""
    match = re.match(r"^(.*?)[\s#]+([A-Za-z]{0,5}\d+[A-Za-z]?)(?:\s*/\s*\d+)?\s*$", text.strip())
    return (match.group(1).strip(), match.group(2)) if match and match.group(1).strip() else (text.strip(), "")


def search_cards(name: str, number: str = "", detail_for: int = 6) -> list[dict]:
    """Find cards by name (and number) on TCGdex, cache them, and return full rows.

    One search request, then one request per top candidate to fetch its full record
    (rarity, HP, variants), so the confirm screen can show real details.
    """
    ensure_sets()
    found = tcgdex().search_cards(name)
    wanted = normalize_number(number)
    if wanted:
        by_number = [c for c in found if normalize_number(c["local_id"]) == wanted]
        found = by_number or found
    key = name.casefold()
    found.sort(key=lambda c: (c["clean_name"].casefold() != key, not c["clean_name"].casefold().startswith(key),
                              len(c["clean_name"])))
    found = found[:40]
    catalog.save_resumes(found)
    rows = catalog.get_cards([c["id"] for c in found])
    top = identify.rank(identify.Reading(name=name, number=number), rows, limit=detail_for)
    for cand in top:
        try:
            ensure_detail(cand.card["id"])
        except TCGdexError:
            break
    return catalog.get_cards([c["id"] for c in found])


def ensure_cards(card_ids: list[str]) -> tuple[dict[str, str], int]:
    """Make sure every card id is cached, fetching unknown ones from TCGdex (a few at a time).

    Returns ({id: reason} for the ones that couldn't be found, number fetched).
    """
    known = {c["id"] for c in catalog.get_cards(card_ids)}
    missing = [i for i in card_ids if i not in known]
    if not missing:
        return {}, 0
    app = current_app._get_current_object()
    client = tcgdex()
    failures: dict[str, str] = {}

    def fetch(card_id: str) -> None:
        detail = client.get_card(card_id)
        if not detail:
            raise TCGdexError("TCGdex has no record of that.")
        catalog.save_detail(detail)

    with ThreadPoolExecutor(max_workers=app.config["SYNC_WORKERS"]) as pool:
        futures = {pool.submit(_in_context, app, fetch, i): i for i in missing}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                failures[futures[fut]] = str(exc) if isinstance(exc, TCGdexError) else "the lookup failed"
    stored = {c["id"] for c in catalog.get_cards(missing)}
    for card_id in missing:           # TCGdex answered with a different id than we asked for
        if card_id not in stored and card_id not in failures:
            failures[card_id] = "TCGdex has no record of that."
    return failures, len(stored)


def _in_context(app, work, item):
    """Worker threads need their own app context (the artwork folder comes from the app config)."""
    with app.app_context():
        return work(item)


# ---- Expansion download (background) ------------------------------------------------------

def start_set_sync(set_pk: int, force: bool = False) -> str:
    """Download an expansion in the background: card list, full records and all artwork.

    Returns "started", "running" (one is already in progress), "done" (already stored locally,
    nothing to do) or "off" (background jobs disabled). Idempotent: files and records already
    stored are skipped, so it also resumes an interrupted download.
    """
    app = current_app._get_current_object()
    row = catalog.get_set(set_pk)
    if row is None or not app.config["BACKGROUND_JOBS"]:
        return "off"
    if not force and row["images_synced_at"] and row["details_synced_at"] and row["sync_status"] != "running":
        return "done"
    if not catalog.claim_sync(set_pk):
        return "running"
    threading.Thread(target=_run_sync, args=(app, set_pk, force), daemon=True, name=f"sync-{set_pk}").start()
    return "started"


def _run_sync(app, set_pk: int, force: bool) -> None:
    with app.app_context():
        try:
            ensure_sets()
            ensure_set_cards(set_pk, force=force)
            cards = catalog.cards_for_sync(set_pk)
            has_fallback = bool(app.config["ART_FALLBACK_URLS"])
            images_todo = [c for c in cards if (c["image_base"] or has_fallback) and cached_art(c["id"], "low") is None
                           and not recently_missed(c["id"])]
            details_todo = [c for c in cards if force or not c["has_detail"]]
            total, done, failed = len(images_todo) + len(details_todo), 0, {"images": 0, "details": 0}
            catalog.update_progress(set_pk, 0, total)
            lock, client = threading.Lock(), tcgdex()

            def fetch_image(c):
                if fetch_art_for(c, "low") is None and c["image_base"]:
                    raise TCGdexError("no artwork found")     # a card TCGdex has no picture for isn't an error

            def fetch_detail(c):
                detail = client.get_card(c["id"])
                if detail:
                    catalog.save_detail(detail)

            for label, todo, work in (("images", images_todo, fetch_image), ("details", details_todo, fetch_detail)):
                with ThreadPoolExecutor(max_workers=app.config["SYNC_WORKERS"]) as pool:
                    futures = [pool.submit(_in_context, app, work, c) for c in todo]
                    for fut in as_completed(futures):
                        with lock:
                            done += 1
                            try:
                                fut.result()
                            except Exception as exc:  # one bad card shouldn't stop the set
                                failed[label] += 1
                                log.warning("sync %s %s failed: %s", set_pk, label, exc)
                            if done % 5 == 0 or done == total:
                                catalog.update_progress(set_pk, done, total)
            problems = [f"{n} {label}" for label, n in failed.items() if n]
            catalog.finish_sync(set_pk, not failed["images"], not failed["details"],
                                f"{' and '.join(problems)} couldn't be downloaded. Run it again to retry." if problems else None)
        except Exception as exc:
            log.exception("expansion download %s failed", set_pk)
            catalog.fail_sync(set_pk, str(exc) if isinstance(exc, TCGdexError) else "The download failed unexpectedly.")


# ---- Artwork cache ----------------------------------------------------------------------------

def _art_path(card_id: str, size: str):
    safe = "".join(ch for ch in card_id if ch.isalnum() or ch in "_-.")
    return images.catalog_dir() / f"{safe}-{size}.webp"


def cached_art(card_id: str, size: str = "low") -> bytes | None:
    path = _art_path(card_id, size)
    return path.read_bytes() if path.is_file() else None


def _store_art(card_id: str, size: str, data: bytes) -> None:
    path = _art_path(card_id, size)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch_art(card_id: str, image_base: str, size: str = "low") -> bytes:
    """Artwork from disk, downloading it once from TCGdex's image host if needed."""
    hit = cached_art(card_id, size)
    if hit:
        return hit
    data = tcgdex().download(f"{image_base}/{size}.webp")
    _store_art(card_id, size, data)
    return data


def store_art(card_id: str, sizes: dict[str, bytes]) -> None:
    """Save artwork (both sizes) and forget any earlier 'not found'."""
    for size, data in sizes.items():
        _store_art(card_id, size, data)
    _miss_path(card_id).unlink(missing_ok=True)


def clear_art(card_id: str) -> None:
    """Delete the stored artwork so it is downloaded again from the sources."""
    for size in SIZES:
        _art_path(card_id, size).unlink(missing_ok=True)
    _miss_path(card_id).unlink(missing_ok=True)


def _miss_path(card_id: str):
    return _art_path(card_id, "low").with_suffix(".miss")


def recently_missed(card_id: str) -> bool:
    path = _miss_path(card_id)
    minutes = current_app.config["ART_RETRY_MINUTES"]
    return path.is_file() and time.time() - path.stat().st_mtime < minutes * 60


def fallback_urls(card: dict) -> list[str]:
    """The alternate artwork addresses for a card (from ART_FALLBACK_URLS)."""
    local = card.get("local_id") or ""
    values = {"set_id": card.get("set_tcgdex_id") or (card["id"].rsplit("-", 1)[0] if "-" in card["id"] else ""),
              "local_id": local, "number": local.lstrip("0") or local}
    urls = []
    for template in current_app.config["ART_FALLBACK_URLS"]:
        try:
            urls.append(template.format(**{k: quote(str(v), safe="") for k, v in values.items()}))
        except (KeyError, IndexError):
            log.warning("ART_FALLBACK_URLS entry %r has an unknown placeholder", template)
    return urls


def fetch_art_for(card: dict, size: str = "low") -> bytes | None:
    """Artwork for a card: stored copy, else TCGdex, else the fallback sources. None if nobody has it.

    A failed search is remembered for ART_RETRY_MINUTES so a page full of missing cards doesn't re-ask on every view.
    """
    card_id = card["id"]
    hit = cached_art(card_id, size)
    if hit:
        return hit
    if recently_missed(card_id):
        return None
    if card.get("image_base"):
        try:
            return fetch_art(card_id, card["image_base"], size)
        except TCGdexError as exc:
            log.info("TCGdex artwork for %s unavailable: %s", card_id, exc)
    for url in fallback_urls(card):
        try:
            store_art(card_id, images.art_from_bytes(images.download_image(url)))
            return cached_art(card_id, size)
        except images.ImageError as exc:
            log.info("fallback artwork %s unavailable: %s", url, exc)
    _miss_path(card_id).touch()
    return None


# ---- Prices ----------------------------------------------------------------------------------
# TCGdex is the main source: every full card record includes TCGplayer prices, so a refresh is one
# request with nothing to match. PokeWallet is only a fallback for cards TCGdex has no price for.

def _pokewallet_price(card_id: str, row: dict) -> bool:
    """Fallback: find the card on PokeWallet and store its TCGplayer prices (1-2 requests)."""
    client = pokewallet()
    reading = identify.Reading(name=row["clean_name"], number=row["card_number"] or "",
                               set_code=row["set_code"] or "", set_name=row["set_name"] or "")
    queries = ([f"{row['clean_name']} {normalize_number(row['card_number'])}"] if row["card_number"] else [])
    queries.append(row["clean_name"])
    for query in queries:
        ranked = identify.rank(reading, client.search(query)["cards"], limit=3)
        if ranked and ranked[0].score >= 75 and ranked[0].card["prices"]:
            best = ranked[0].card
            catalog.save_prices(card_id, best["id"], best["prices"])
            return True
    return False


def refresh_price(card_id: str, fallback: bool = True) -> str | None:
    """Update one card's prices. Returns where they came from ("TCGdex" or "PokeWallet"), None if nobody has one.

    TCGdex errors propagate; a PokeWallet error (rate limit, bad key) only matters when the fallback was needed.
    """
    row = catalog.get_card(card_id)
    if row is None:
        return None
    detail = tcgdex().get_card(card_id)          # 1 request: also refreshes the card's other details
    if detail:
        catalog.save_detail(detail)
        if detail.get("prices"):
            return "TCGdex"
    if fallback and prices_configured() and _pokewallet_price(card_id, catalog.get_card(card_id) or row):
        return "PokeWallet"
    catalog.mark_price_checked(card_id)          # remember we looked; keeps any price already stored
    return None


def price_is_stale(row: dict, max_age_hours: int) -> bool:
    checked = row.get("prices_updated_at")
    if not checked:
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked > timedelta(hours=max_age_hours)


def refresh_price_later(card_id: str) -> None:
    """After adding a card: refresh its price in the background if it has none or it's more than a day old."""
    app = current_app._get_current_object()
    if not app.config["BACKGROUND_JOBS"]:
        return

    def run():
        with app.app_context():
            try:
                row = catalog.get_card(card_id)
                if row and price_is_stale(row, app.config["PRICE_MAX_AGE_HOURS"]):
                    refresh_price(card_id)
            except (TCGdexError, PokeWalletError) as exc:
                log.info("price lookup skipped: %s", exc)
            except Exception:
                log.exception("price lookup failed")

    threading.Thread(target=run, daemon=True, name=f"price-{card_id}").start()


# "Refresh all prices": one background run per collection at a time (per server process).
_bulk_lock = threading.Lock()
_bulk: dict[int, dict] = {}


def bulk_status(collection_id: int) -> dict | None:
    return _bulk.get(collection_id)


def start_bulk_price_refresh(collection_id: int, card_ids: list[str]) -> str:
    """Refresh every card in the list from TCGdex, then try PokeWallet for a few that still have no price.

    Returns "started", "running" or "off" (background jobs disabled).
    """
    app = current_app._get_current_object()
    if not app.config["BACKGROUND_JOBS"]:
        return "off"
    with _bulk_lock:
        if _bulk.get(collection_id, {}).get("status") == "running":
            return "running"
        _bulk[collection_id] = state = {"status": "running", "done": 0, "total": len(card_ids), "priced": 0,
                                        "unpriced": 0, "fallback": 0, "failed": 0, "note": None,
                                        "started": datetime.now(timezone.utc)}

    def work(card_id):
        priced = refresh_price(card_id, fallback=False) is not None
        with _bulk_lock:
            state["done"] += 1
            state["priced" if priced else "unpriced"] += 1
        return priced

    def run():
        with app.app_context():
            try:
                missing = []
                with ThreadPoolExecutor(max_workers=app.config["SYNC_WORKERS"]) as pool:
                    futures = {pool.submit(_in_context, app, work, i): i for i in card_ids}
                    for fut in as_completed(futures):
                        try:
                            if not fut.result():
                                missing.append(futures[fut])
                        except Exception as exc:
                            with _bulk_lock:
                                state["failed"] += 1
                                state["done"] += 1
                            log.warning("price refresh %s failed: %s", futures[fut], exc)
                if prices_configured():
                    try:
                        for card_id in missing[: app.config["PRICE_FALLBACK_MAX"]]:
                            row = catalog.get_card(card_id)
                            if row and _pokewallet_price(card_id, row):
                                with _bulk_lock:
                                    state["fallback"] += 1
                                    state["priced"] += 1
                                    state["unpriced"] -= 1
                    except PokeWalletError as exc:
                        state["note"] = f"PokeWallet stopped the fallback lookups: {exc}"
                if len(missing) > app.config["PRICE_FALLBACK_MAX"] and prices_configured():
                    state["note"] = (state["note"] or "") + (
                        f" Only the first {app.config['PRICE_FALLBACK_MAX']} cards without a TCGdex price were tried on"
                        " PokeWallet; run it again for the rest.")
                state["status"] = "done"
            except Exception:
                log.exception("bulk price refresh failed")
                state.update(status="failed", note="The refresh stopped unexpectedly. Run it again.")
            finally:
                state["finished"] = datetime.now(timezone.utc)

    threading.Thread(target=run, daemon=True, name=f"prices-{collection_id}").start()
    return "started"
