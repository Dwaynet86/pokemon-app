"""Published card data cached from TCGdex: sets, cards, and card -> Pokemon links."""
from psycopg.types.json import Jsonb

from . import species as species_mod
from .db import get_conn, use_conn


# ---- Sets ---------------------------------------------------------------------

def upsert_set(conn, s: dict) -> int:
    """Insert or refresh a set. Fields a response didn't include never overwrite known values."""
    row = conn.execute(
        """INSERT INTO card_sets (tcgdex_id, name, series, set_code, release_date, card_count,
                                  card_count_official, logo_url, symbol_url)
           VALUES (%(tcgdex_id)s, %(name)s, %(series)s, %(set_code)s, %(release_date)s, %(card_count)s,
                   %(card_count_official)s, %(logo_url)s, %(symbol_url)s)
           ON CONFLICT (tcgdex_id) DO UPDATE SET
               name = EXCLUDED.name,
               series = COALESCE(EXCLUDED.series, card_sets.series),
               set_code = COALESCE(EXCLUDED.set_code, card_sets.set_code),
               release_date = COALESCE(EXCLUDED.release_date, card_sets.release_date),
               card_count = COALESCE(EXCLUDED.card_count, card_sets.card_count),
               card_count_official = COALESCE(EXCLUDED.card_count_official, card_sets.card_count_official),
               logo_url = COALESCE(EXCLUDED.logo_url, card_sets.logo_url),
               symbol_url = COALESCE(EXCLUDED.symbol_url, card_sets.symbol_url),
               updated_at = now()
           RETURNING id""",
        s,
    ).fetchone()
    return row["id"]


def save_sets(sets: list[dict]) -> int:
    with get_conn() as conn:
        for s in sets:
            upsert_set(conn, s)
    return len(sets)


def get_set(set_pk: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM card_sets WHERE id = %s", (set_pk,)).fetchone()


def mark_cards_fetched(set_pk: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE card_sets SET cards_fetched_at = now() WHERE id = %s", (set_pk,))


def sets_known() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT count(*) AS n FROM card_sets").fetchone()["n"]


# Master-set mode counts finishes, not cards: every finish a card exists in is one item, and one owned finish ticks it off.
# A card whose finishes aren't known yet (details not downloaded) counts once.
OWNED_FINISHES = """count(DISTINCT CASE WHEN jsonb_array_length(k.variants) = 0 THEN k.id
                                         WHEN k.variants ? oc.variant THEN k.id || '|' || oc.variant END)"""
TOTAL_FINISHES = """(SELECT set_id, sum(GREATEST(jsonb_array_length(variants), 1)) AS finishes FROM cards GROUP BY set_id)"""


def list_sets(collection_id: int, q: str = "", series: str = "", mine_only: bool = False, master: bool = False) -> list[dict]:
    """Every known set with how many distinct cards (or finishes, in master-set mode) this collection owns, and what they are worth."""
    where, params = [], [collection_id]
    if q:
        where.append("(s.name ILIKE %s OR s.set_code ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if series:
        where.append("s.series = %s")
        params.append(series)
    if mine_only:
        where.append("COALESCE(o.owned, 0) > 0")
    owned_expr = OWNED_FINISHES if master else "count(DISTINCT k.id)"
    total_expr = "COALESCE(t.finishes, s.card_count)" if master else "s.card_count"
    sql = f"""
        SELECT s.*, COALESCE(o.owned, 0) AS owned, COALESCE(o.value, 0) AS value, {total_expr} AS total
        FROM card_sets s
        LEFT JOIN (SELECT k.set_id, {owned_expr} AS owned,
                          COALESCE(sum(oc.quantity * COALESCE((k.prices ->> oc.variant)::numeric, 0)), 0) AS value
                     FROM owned_cards oc JOIN cards k ON k.id = oc.card_id
                    WHERE oc.collection_id = %s GROUP BY k.set_id) o ON o.set_id = s.id
        {'LEFT JOIN ' + TOTAL_FINISHES + ' t ON t.set_id = s.id' if master else ''}
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY s.release_date DESC NULLS LAST, s.name"""
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def series_list() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT series FROM card_sets WHERE series IS NOT NULL GROUP BY series ORDER BY max(release_date) DESC NULLS LAST"
        ).fetchall()
    return [r["series"] for r in rows]


def set_cards(collection_id: int, set_pk: int) -> list[dict]:
    """Every card in a set; `qty` > 0 marks the ones this collection owns."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT k.*, COALESCE(sum(oc.quantity), 0) AS qty,
                      array_remove(array_agg(DISTINCT oc.variant), NULL) AS owned_variants
               FROM cards k
               LEFT JOIN owned_cards oc ON oc.card_id = k.id AND oc.collection_id = %s
               WHERE k.set_id = %s
               GROUP BY k.id
               ORDER BY k.number_sort NULLS LAST, k.local_id, k.name""",
            (collection_id, set_pk),
        ).fetchall()


# ---- Expansion download jobs (state lives in the table so every worker process agrees) ----

STALE_AFTER = "30 minutes"


def claim_sync(set_pk: int) -> bool:
    """Mark a download as running. False if one is already running (and not stale)."""
    with get_conn() as conn:
        row = conn.execute(
            f"""UPDATE card_sets SET sync_status = 'running', sync_done = 0, sync_total = 0,
                       sync_message = NULL, sync_started_at = now()
                WHERE id = %s AND (sync_status <> 'running'
                                   OR sync_started_at < now() - interval '{STALE_AFTER}')
                RETURNING id""", (set_pk,)).fetchone()
    return row is not None


def update_progress(set_pk: int, done: int, total: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE card_sets SET sync_done = %s, sync_total = %s WHERE id = %s", (done, total, set_pk))


def finish_sync(set_pk: int, images_ok: bool, details_ok: bool, message: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE card_sets SET sync_status = 'idle', sync_message = %s,
                      images_synced_at = CASE WHEN %s THEN now() ELSE images_synced_at END,
                      details_synced_at = CASE WHEN %s THEN now() ELSE details_synced_at END
               WHERE id = %s""", (message, images_ok, details_ok, set_pk))


def fail_sync(set_pk: int, message: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE card_sets SET sync_status = 'error', sync_message = %s WHERE id = %s",
                     (message[:300], set_pk))


def cards_for_sync(set_pk: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT id, local_id, image_base, has_detail FROM cards WHERE set_id = %s ORDER BY number_sort NULLS LAST",
                            (set_pk,)).fetchall()


# ---- Cards --------------------------------------------------------------------

def _set_lookup(conn) -> dict[str, dict]:
    return {r["tcgdex_id"]: r for r in conn.execute(
        "SELECT id, tcgdex_id, card_count_official FROM card_sets").fetchall()}


def _resolve_set(conn, card: dict, lookup: dict[str, dict]) -> tuple[int | None, int | None]:
    """(set pk, official count) for a card, creating the set from the card's own set block if new."""
    sid = card.get("set_tcgdex_id")
    if card.get("set_info"):
        pk = upsert_set(conn, card["set_info"])
        lookup[sid] = {"id": pk, "tcgdex_id": sid, "card_count_official": card["set_info"].get("card_count_official")}
    row = lookup.get(sid)
    return (row["id"], row["card_count_official"]) if row else (None, None)


def _link_species(conn, card_id: str, dex_numbers: list[int]) -> None:
    conn.execute("DELETE FROM card_species WHERE card_id = %s", (card_id,))
    for dex in dex_numbers:
        conn.execute(
            """INSERT INTO card_species (card_id, dex_number)
               SELECT %s, dex_number FROM species WHERE dex_number = %s ON CONFLICT DO NOTHING""",
            (card_id, dex))


def upsert_resume(conn, card: dict, set_pk: int | None) -> None:
    """Store a brief card record. Never overwrites details already fetched for the card."""
    row = conn.execute(
        """INSERT INTO cards (id, set_id, local_id, name, clean_name, card_number, number_sort, image_base)
           VALUES (%(id)s, %(set_pk)s, %(local_id)s, %(name)s, %(clean_name)s, %(card_number)s,
                   %(number_sort)s, %(image_base)s)
           ON CONFLICT (id) DO UPDATE SET
               set_id = COALESCE(EXCLUDED.set_id, cards.set_id), local_id = EXCLUDED.local_id,
               name = EXCLUDED.name, clean_name = EXCLUDED.clean_name,
               card_number = COALESCE(EXCLUDED.card_number, cards.card_number),
               number_sort = EXCLUDED.number_sort,
               image_base = COALESCE(EXCLUDED.image_base, cards.image_base), updated_at = now()
           RETURNING has_detail""",
        {**card, "set_pk": set_pk}).fetchone()
    if not row["has_detail"]:   # until the full record arrives, guess the Pokemon from the name
        _link_species(conn, card["id"], species_mod.species_for_card(card["clean_name"], None))


def upsert_detail(conn, card: dict, set_pk: int | None) -> None:
    """Store a full card record and link it to the Pokemon TCGdex says it features."""
    conn.execute(
        """INSERT INTO cards (id, set_id, local_id, name, clean_name, card_number, number_sort, image_base,
                              has_detail, rarity, card_type, hp, stage, variants, data)
           VALUES (%(id)s, %(set_pk)s, %(local_id)s, %(name)s, %(clean_name)s, %(card_number)s, %(number_sort)s,
                   %(image_base)s, true, %(rarity)s, %(card_type)s, %(hp)s, %(stage)s, %(variants)s, %(data)s)
           ON CONFLICT (id) DO UPDATE SET
               set_id = COALESCE(EXCLUDED.set_id, cards.set_id), local_id = EXCLUDED.local_id,
               name = EXCLUDED.name, clean_name = EXCLUDED.clean_name, card_number = EXCLUDED.card_number,
               number_sort = EXCLUDED.number_sort, image_base = COALESCE(EXCLUDED.image_base, cards.image_base),
               has_detail = true, rarity = EXCLUDED.rarity, card_type = EXCLUDED.card_type, hp = EXCLUDED.hp,
               stage = EXCLUDED.stage, variants = EXCLUDED.variants, data = EXCLUDED.data, updated_at = now()""",
        {**card, "set_pk": set_pk, "variants": Jsonb(card["variants"]), "data": Jsonb(card["data"])})
    dex = card["dex_ids"]
    if not dex and not (card["card_type"] or "").casefold().startswith(species_mod.NON_POKEMON_TYPES):
        dex = species_mod.species_for_card(card["clean_name"], card["card_type"])   # Pokemon without a dexId
    _link_species(conn, card["id"], dex or [])


def save_resumes(cards: list[dict], set_pk: int | None = None, conn=None) -> None:
    """Store brief records. `set_pk` forces the set (a set download); otherwise it's derived from each card."""
    with use_conn(conn) as c:
        lookup = _set_lookup(c)
        for card in cards:
            pk, official = (set_pk, card.get("card_count_official")) if set_pk else _resolve_set(c, card, lookup)
            if not set_pk and official and (card.get("local_id") or "").isdigit():
                card = {**card, "card_number": f"{card['local_id']}/{official}"}
            upsert_resume(c, card, pk)


def save_detail(card: dict) -> None:
    """Store a full record. Its TCGdex prices replace the stored ones, but a card TCGdex has no price for keeps any it had."""
    with get_conn() as conn:
        pk, _ = _resolve_set(conn, card, _set_lookup(conn))
        upsert_detail(conn, card, pk)
        if card.get("prices"):
            conn.execute("UPDATE cards SET pw_id = NULL, prices = %s, prices_updated_at = now() WHERE id = %s",
                         (Jsonb(card["prices"]), card["id"]))


def save_prices(card_id: str, pw_id: str | None, prices: dict) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE cards SET pw_id = %s, prices = %s, prices_updated_at = now() WHERE id = %s",
                     (pw_id, Jsonb(prices), card_id))


def mark_price_checked(card_id: str) -> None:
    """Looked and found nothing new: remember when (any price already stored is kept)."""
    with get_conn() as conn:
        conn.execute("UPDATE cards SET prices_updated_at = now() WHERE id = %s", (card_id,))


_CARD_SELECT = """SELECT k.*, s.name AS set_name, s.set_code, s.release_date, s.series,
                         s.card_count AS set_total, s.card_count_official AS set_official
                  FROM cards k LEFT JOIN card_sets s ON s.id = k.set_id"""


def get_card(card_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(f"{_CARD_SELECT} WHERE k.id = %s", (card_id,)).fetchone()


def neighbours(card_id: str, set_pk: int | None) -> tuple[dict | None, dict | None]:
    """The cards just before and after this one in its expansion, in the expansion page's order (None at either end)."""
    if not set_pk:
        return None, None
    with get_conn() as conn:
        row = conn.execute(
            """SELECT prev_id, next_id FROM (
                   SELECT id, lag(id) OVER w AS prev_id, lead(id) OVER w AS next_id FROM cards WHERE set_id = %s
                   WINDOW w AS (ORDER BY number_sort NULLS LAST, local_id, name)) o WHERE id = %s""",
            (set_pk, card_id)).fetchone()
        if not row:
            return None, None
        ids = [i for i in (row["prev_id"], row["next_id"]) if i]
        found = {r["id"]: r for r in conn.execute(
            "SELECT id, name, card_number, local_id FROM cards WHERE id = ANY(%s)", (ids,)).fetchall()} if ids else {}
    return found.get(row["prev_id"]), found.get(row["next_id"])


def get_cards(card_ids: list[str]) -> list[dict]:
    if not card_ids:
        return []
    with get_conn() as conn:
        return conn.execute(f"{_CARD_SELECT} WHERE k.id = ANY(%s)", (card_ids,)).fetchall()


def search_local(name: str, limit: int = 60) -> list[dict]:
    """Cards already cached whose name contains `name` (no network)."""
    with get_conn() as conn:
        return conn.execute(
            f"{_CARD_SELECT} WHERE lower(k.clean_name) LIKE %s ORDER BY s.release_date DESC NULLS LAST LIMIT %s",
            (f"%{name.lower()}%", limit)).fetchall()


def species_cards(collection_id: int, dex: int) -> list[dict]:
    """Cached cards featuring a Pokemon; `qty` > 0 marks the ones this collection owns."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT k.*, s.name AS set_name, s.set_code, s.release_date,
                      COALESCE(sum(oc.quantity), 0) AS qty,
                      array_remove(array_agg(DISTINCT oc.variant), NULL) AS owned_variants
               FROM card_species cs JOIN cards k ON k.id = cs.card_id
               LEFT JOIN card_sets s ON s.id = k.set_id
               LEFT JOIN owned_cards oc ON oc.card_id = k.id AND oc.collection_id = %s
               WHERE cs.dex_number = %s
               GROUP BY k.id, s.id
               ORDER BY s.release_date NULLS LAST, k.number_sort NULLS LAST""",
            (collection_id, dex)).fetchall()


# ---- Pokedex ------------------------------------------------------------------

def load_species() -> int:
    """Fill `species` from the bundled Pokedex (safe to re-run)."""
    rows = species_mod.load()
    with get_conn() as conn:
        for dex, name, gen in rows:
            conn.execute(
                """INSERT INTO species (dex_number, name, generation) VALUES (%s, %s, %s)
                   ON CONFLICT (dex_number) DO UPDATE SET name = EXCLUDED.name, generation = EXCLUDED.generation""",
                (dex, name, gen))
    return len(rows)


# ---- Card-scan lookups ----------------------------------------------------------

def get_scan_products(product_ids: list[int]) -> dict[int, dict]:
    """Cached scan-product details, keyed by product id."""
    if not product_ids:
        return {}
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM scan_products WHERE product_id = ANY(%s)", (product_ids,)).fetchall()
    return {r["product_id"]: r for r in rows}


def save_scan_product(product: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scan_products (product_id, name, number, rarity, set_name, set_abbr, hint)
               VALUES (%(product_id)s, %(name)s, %(number)s, %(rarity)s, %(set_name)s, %(set_abbr)s, %(hint)s)
               ON CONFLICT (product_id) DO UPDATE SET name = EXCLUDED.name, number = EXCLUDED.number,
                   rarity = EXCLUDED.rarity, set_name = EXCLUDED.set_name, set_abbr = EXCLUDED.set_abbr,
                   hint = EXCLUDED.hint, fetched_at = now()""",
            product)
