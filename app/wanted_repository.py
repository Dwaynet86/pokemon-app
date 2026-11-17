"""Database access for each collection's wanted list."""
from .db import get_conn

_PRICE_FROM = """(SELECT min(e.v::numeric) FROM jsonb_each_text(k.prices) AS e(k, v)
                   WHERE e.v ~ '^[0-9]+(\\.[0-9]+)?$')"""


def toggle(collection_id: int, card_id: str) -> bool:
    """Add the card to the list, or remove it if it is already there. Returns True when it is now wanted."""
    with get_conn() as conn:
        removed = conn.execute("DELETE FROM wanted_cards WHERE collection_id = %s AND card_id = %s RETURNING 1",
                               (collection_id, card_id)).fetchone()
        if removed:
            return False
        conn.execute("INSERT INTO wanted_cards (collection_id, card_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (collection_id, card_id))
        return True


def add_missing(collection_id: int, set_pk: int) -> int:
    """Put every card of the expansion that this collection doesn't own on the wanted list. Returns how many were added."""
    with get_conn() as conn:
        rows = conn.execute(
            """INSERT INTO wanted_cards (collection_id, card_id)
               SELECT %(c)s, k.id FROM cards k
                WHERE k.set_id = %(s)s
                  AND NOT EXISTS (SELECT 1 FROM owned_cards oc WHERE oc.collection_id = %(c)s AND oc.card_id = k.id)
               ON CONFLICT DO NOTHING RETURNING 1""", {"c": collection_id, "s": set_pk}).fetchall()
    return len(rows)


def clear_set(collection_id: int, set_pk: int) -> int:
    """Take every card of the expansion off the wanted list. Returns how many were removed."""
    with get_conn() as conn:
        rows = conn.execute(
            """DELETE FROM wanted_cards w USING cards k
               WHERE w.collection_id = %s AND k.id = w.card_id AND k.set_id = %s RETURNING 1""",
            (collection_id, set_pk)).fetchall()
    return len(rows)


def wanted_ids(collection_id: int, set_pk: int | None = None) -> set[str]:
    """Ids of the wanted cards (just one expansion's when `set_pk` is given)."""
    sql, params = "SELECT w.card_id FROM wanted_cards w", [collection_id]
    if set_pk is not None:
        sql += " JOIN cards k ON k.id = w.card_id WHERE w.collection_id = %s AND k.set_id = %s"
        params.append(set_pk)
    else:
        sql += " WHERE w.collection_id = %s"
    with get_conn() as conn:
        return {r["card_id"] for r in conn.execute(sql, params).fetchall()}


def is_wanted(collection_id: int, card_id: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM wanted_cards WHERE collection_id = %s AND card_id = %s",
                            (collection_id, card_id)).fetchone() is not None


def list_wanted(collection_id: int) -> list[dict]:
    """Wanted cards with their expansion and the cheapest known market price, newest expansions first."""
    with get_conn() as conn:
        return conn.execute(
            f"""SELECT k.*, s.name AS set_name, s.set_code, s.release_date, w.added_at,
                       {_PRICE_FROM} AS price_from
                FROM wanted_cards w JOIN cards k ON k.id = w.card_id
                LEFT JOIN card_sets s ON s.id = k.set_id
                WHERE w.collection_id = %s
                ORDER BY s.release_date DESC NULLS LAST, s.name, k.number_sort NULLS LAST, k.id""",
            (collection_id,)).fetchall()


def get_public(slug: str) -> dict | None:
    """The collection with this slug, only if its owner shares the wanted list."""
    with get_conn() as conn:
        return conn.execute("SELECT id, name, slug FROM collections WHERE slug = %s AND wanted_public",
                            (slug,)).fetchone()


def is_public_wanted(collection_id: int, card_id: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM wanted_cards WHERE collection_id = %s AND card_id = %s",
                            (collection_id, card_id)).fetchone() is not None


def set_public(collection_id: int, public: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE collections SET wanted_public = %s WHERE id = %s", (public, collection_id))
