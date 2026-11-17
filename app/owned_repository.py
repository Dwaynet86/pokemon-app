"""What a collection owns: one row per (card, variant, condition) with a quantity."""
from .db import get_conn, use_conn

CONDITIONS = ["Near Mint", "Lightly Played", "Moderately Played", "Heavily Played", "Damaged"]
DEFAULT_VARIANTS = ["Normal", "Holofoil", "Reverse Holofoil", "1st Edition", "1st Edition Holofoil", "Promo"]

SORTS = {
    "added": "oc.created_at DESC, oc.id DESC",
    "oldest": "oc.created_at, oc.id",
    "name": "lower(k.clean_name), s.release_date NULLS LAST, k.number_sort NULLS LAST",
    "set": "s.release_date DESC NULLS LAST, s.name, k.number_sort NULLS LAST",
    "value": "(NULLIF(k.prices ->> oc.variant, '')::numeric * oc.quantity) DESC NULLS LAST, lower(k.clean_name)",
}

_SELECT = """
    SELECT oc.*, k.name, k.clean_name, k.card_number, k.rarity, k.card_type, k.variants, k.prices,
           s.name AS set_name, s.set_code, s.id AS set_pk,
           NULLIF(k.prices ->> oc.variant, '')::numeric AS unit_price
    FROM owned_cards oc JOIN cards k ON k.id = oc.card_id LEFT JOIN card_sets s ON s.id = k.set_id"""


def variants_for(card: dict) -> list[str]:
    """Finishes to offer: the card's published ones (else a default list), plus any finish that has a price."""
    variants = list(card.get("variants") or []) or list(DEFAULT_VARIANTS)
    return variants + [v for v in (card.get("prices") or {}) if v not in variants]


def default_variant(card: dict) -> str:
    """The finish to preselect for a card: Normal if it exists, else Holofoil, else its first one."""
    options = variants_for(card)
    return next((v for v in ("Normal", "Holofoil") if v in options), options[0])


def add_copy(collection_id: int, card_id: str, variant: str, condition: str, quantity: int,
             notes: str | None = None, conn=None,
             acquired_on=None, created_at=None) -> dict:
    """Add copies. Same card + variant + condition raises the quantity of the existing row (max 999).

    Pass `conn` to run inside the caller's transaction (backup import).
    """
    with use_conn(conn) as c:
        row = c.execute(
            """INSERT INTO owned_cards (collection_id, card_id, variant, condition, quantity,
                                        notes, acquired_on, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
               ON CONFLICT (collection_id, card_id, variant, condition) DO UPDATE SET
                   quantity = LEAST(999, owned_cards.quantity + EXCLUDED.quantity),
                   notes = COALESCE(owned_cards.notes, EXCLUDED.notes),
                   acquired_on = COALESCE(owned_cards.acquired_on, EXCLUDED.acquired_on),
                   updated_at = now()
               RETURNING *, (xmax = 0) AS inserted""",
            (collection_id, card_id, variant, condition, quantity, notes, acquired_on, created_at),
        ).fetchone()
        # you have it now: it's no longer "wanted"
        c.execute("DELETE FROM wanted_cards WHERE collection_id = %s AND card_id = %s", (collection_id, card_id))
        return row


def copy_exists(collection_id: int, card_id: str, variant: str, condition: str, conn=None) -> bool:
    with use_conn(conn) as c:
        return c.execute(
            """SELECT 1 FROM owned_cards WHERE collection_id = %s AND card_id = %s
               AND variant = %s AND condition = %s""",
            (collection_id, card_id, variant, condition)).fetchone() is not None


def all_owned(collection_id: int) -> list[dict]:
    """Every owned row with its card details, in a stable order (for exports)."""
    with get_conn() as conn:
        return conn.execute(
            f"{_SELECT} WHERE oc.collection_id = %s "
            "ORDER BY s.release_date NULLS LAST, s.name, k.number_sort NULLS LAST, k.id, oc.variant, oc.condition",
            (collection_id,)).fetchall()


def list_owned(collection_id: int, q: str = "", set_pk: int | None = None, rarity: str = "",
               variant: str = "", sort: str = "added", page: int = 1, per_page: int = 48):
    """Gallery rows plus the total count for the current filters."""
    where, params = ["oc.collection_id = %s"], [collection_id]
    if q:
        where.append("(k.name ILIKE %s OR k.card_number ILIKE %s OR s.name ILIKE %s OR s.set_code ILIKE %s)")
        params += [f"%{q}%"] * 4
    if set_pk:
        where.append("k.set_id = %s")
        params.append(set_pk)
    if rarity:
        where.append("k.rarity = %s")
        params.append(rarity)
    if variant:
        where.append("oc.variant = %s")
        params.append(variant)
    clause = " AND ".join(where)
    order = SORTS.get(sort, SORTS["added"])
    with get_conn() as conn:
        total = conn.execute(
            f"""SELECT count(*) AS n FROM owned_cards oc JOIN cards k ON k.id = oc.card_id
                LEFT JOIN card_sets s ON s.id = k.set_id WHERE {clause}""", params).fetchone()["n"]
        rows = conn.execute(
            f"{_SELECT} WHERE {clause} ORDER BY {order} LIMIT %s OFFSET %s",
            params + [per_page, (max(page, 1) - 1) * per_page]).fetchall()
    return rows, total


def filter_options(collection_id: int) -> dict:
    """Sets, rarities and variants present in the collection, for the gallery filters."""
    with get_conn() as conn:
        sets = conn.execute(
            """SELECT s.id, s.name, count(*) AS n FROM owned_cards oc JOIN cards k ON k.id = oc.card_id
               JOIN card_sets s ON s.id = k.set_id WHERE oc.collection_id = %s
               GROUP BY s.id ORDER BY max(s.release_date) DESC NULLS LAST, s.name""", (collection_id,)).fetchall()
        rarities = conn.execute(
            """SELECT k.rarity AS name FROM owned_cards oc JOIN cards k ON k.id = oc.card_id
               WHERE oc.collection_id = %s AND k.rarity IS NOT NULL GROUP BY k.rarity ORDER BY k.rarity""",
            (collection_id,)).fetchall()
        variants = conn.execute(
            "SELECT DISTINCT variant AS name FROM owned_cards WHERE collection_id = %s ORDER BY variant",
            (collection_id,)).fetchall()
    return {"sets": sets, "rarities": [r["name"] for r in rarities], "variants": [v["name"] for v in variants]}


def get_copy(collection_id: int, owned_id: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute(f"{_SELECT} WHERE oc.collection_id = %s AND oc.id = %s",
                            (collection_id, owned_id)).fetchone()


def copies_of(collection_id: int, card_id: str) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            f"{_SELECT} WHERE oc.collection_id = %s AND oc.card_id = %s ORDER BY oc.variant, oc.condition",
            (collection_id, card_id)).fetchall()


def update_copy(collection_id: int, owned_id: int, variant: str, condition: str,
                quantity: int, notes: str | None) -> int | None:
    """Edit a row. If the new variant + condition matches another row the two are merged.

    Returns the id of the surviving row, or None if the row doesn't exist.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM owned_cards WHERE id = %s AND collection_id = %s FOR UPDATE",
                           (owned_id, collection_id)).fetchone()
        if row is None:
            return None
        other = conn.execute(
            """SELECT id FROM owned_cards WHERE collection_id = %s AND card_id = %s
               AND variant = %s AND condition = %s AND id <> %s FOR UPDATE""",
            (collection_id, row["card_id"], variant, condition, owned_id)).fetchone()
        if other:
            conn.execute(
                """UPDATE owned_cards SET quantity = quantity + %s, updated_at = now() WHERE id = %s""",
                (quantity, other["id"]))
            conn.execute("DELETE FROM owned_cards WHERE id = %s", (owned_id,))
            return other["id"]
        conn.execute(
            """UPDATE owned_cards SET variant = %s, condition = %s, quantity = %s, notes = %s,
                      updated_at = now() WHERE id = %s""",
            (variant, condition, quantity, notes, owned_id))
        return owned_id


def delete_copy(collection_id: int, owned_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM owned_cards WHERE id = %s AND collection_id = %s", (owned_id, collection_id))


# ---- Pokemon view ---------------------------------------------------------------

def species_progress(collection_id: int, generation: int | None = None, q: str = "") -> list[dict]:
    """Every Pokemon, with how many different cards / total copies the collection has of it."""
    where, params = [], [collection_id]
    if generation:
        where.append("sp.generation = %s")
        params.append(generation)
    if q:
        where.append("sp.name ILIKE %s")
        params.append(f"%{q}%")
    with get_conn() as conn:
        return conn.execute(
            f"""SELECT sp.dex_number, sp.name, sp.generation,
                       COALESCE(o.cards, 0) AS cards, COALESCE(o.copies, 0) AS copies
                FROM species sp
                LEFT JOIN (SELECT cs.dex_number, count(DISTINCT oc.card_id) AS cards, sum(oc.quantity) AS copies
                             FROM owned_cards oc JOIN card_species cs ON cs.card_id = oc.card_id
                            WHERE oc.collection_id = %s GROUP BY cs.dex_number) o ON o.dex_number = sp.dex_number
                {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY sp.dex_number""", params).fetchall()


def get_species(dex: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM species WHERE dex_number = %s", (dex,)).fetchone()


def stats(collection_id: int) -> dict:
    with get_conn() as conn:
        return conn.execute(
            """SELECT COALESCE(sum(oc.quantity), 0) AS copies,
                      count(DISTINCT oc.card_id) AS unique_cards,
                      count(DISTINCT k.set_id) AS sets,
                      (SELECT count(DISTINCT cs.dex_number) FROM owned_cards o2
                         JOIN card_species cs ON cs.card_id = o2.card_id
                        WHERE o2.collection_id = %(c)s) AS species,
                      COALESCE(sum(oc.quantity * NULLIF(k.prices ->> oc.variant, '')::numeric), 0) AS value
               FROM owned_cards oc JOIN cards k ON k.id = oc.card_id WHERE oc.collection_id = %(c)s""",
            {"c": collection_id}).fetchone()


def owned_card_ids(collection_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT card_id FROM owned_cards WHERE collection_id = %s", (collection_id,)).fetchall()
    return [r["card_id"] for r in rows]


def price_overview(collection_id: int) -> dict:
    """How well the collection is priced: unique cards, how many have a price (and from where), oldest check."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT count(*) AS cards,
                      count(*) FILTER (WHERE k.prices <> '{}'::jsonb) AS priced,
                      count(*) FILTER (WHERE k.prices <> '{}'::jsonb AND k.pw_id IS NULL) AS from_tcgdex,
                      count(*) FILTER (WHERE k.prices <> '{}'::jsonb AND k.pw_id IS NOT NULL) AS from_pokewallet,
                      min(k.prices_updated_at) AS oldest, max(k.prices_updated_at) AS newest
               FROM (SELECT DISTINCT card_id FROM owned_cards WHERE collection_id = %s) o
               JOIN cards k ON k.id = o.card_id""", (collection_id,)).fetchone()
