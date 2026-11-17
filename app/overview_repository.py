"""Numbers for the Overview page. Everything comes from data already stored: no API requests."""
from . import species as species_mod
from .catalog_repository import OWNED_FINISHES, TOTAL_FINISHES
from .db import get_conn

MIN_CARDS_FOR_COMPLETION = 3      # a set with one or two cards isn't "nearly complete", it's barely started
_PRICE = "NULLIF(k.prices ->> oc.variant, '')::numeric"


def overview(collection_id: int, limit: int = 5, recent_limit: int = 12, master: bool = False) -> dict:
    """Headline figures, most valuable cards, expansions near completion, and recent additions (5 per ranked list)."""
    with get_conn() as conn:
        head = conn.execute(
            f"""SELECT COALESCE(sum(oc.quantity), 0) AS copies, count(DISTINCT oc.card_id) AS unique_cards,
                       count(DISTINCT k.set_id) AS sets,
                       COALESCE(sum(oc.quantity * {_PRICE}), 0) AS value,
                       count(DISTINCT oc.card_id) FILTER (WHERE k.prices <> '{{}}'::jsonb) AS priced
                FROM owned_cards oc JOIN cards k ON k.id = oc.card_id WHERE oc.collection_id = %s""",
            (collection_id,)).fetchone()
        top_cards = conn.execute(
            f"""SELECT k.id, k.name, k.card_number, k.rarity, s.name AS set_name, sum(oc.quantity) AS copies,
                       sum(oc.quantity * {_PRICE}) AS value
                FROM owned_cards oc JOIN cards k ON k.id = oc.card_id LEFT JOIN card_sets s ON s.id = k.set_id
                WHERE oc.collection_id = %s GROUP BY k.id, s.name
                HAVING sum(oc.quantity * {_PRICE}) > 0
                ORDER BY value DESC, k.name LIMIT %s""", (collection_id, limit)).fetchall()
        sets = conn.execute(
            f"""SELECT s.id, s.name, s.set_code, s.logo_url, s.release_date,
                       {"COALESCE(t.finishes, s.card_count)" if master else "s.card_count"} AS total,
                       o.owned, o.value
                FROM card_sets s
                {"LEFT JOIN " + TOTAL_FINISHES + " t ON t.set_id = s.id" if master else ""}
                JOIN (SELECT k.set_id, {OWNED_FINISHES if master else "count(DISTINCT k.id)"} AS owned,
                             COALESCE(sum(oc.quantity * COALESCE({_PRICE}, 0)), 0) AS value
                        FROM owned_cards oc JOIN cards k ON k.id = oc.card_id
                       WHERE oc.collection_id = %s GROUP BY k.set_id) o ON o.set_id = s.id""",
            (collection_id,)).fetchall()
        recent = conn.execute(
            """SELECT k.id, k.name, k.card_number, s.name AS set_name, max(oc.created_at) AS added
               FROM owned_cards oc JOIN cards k ON k.id = oc.card_id LEFT JOIN card_sets s ON s.id = k.set_id
               WHERE oc.collection_id = %s GROUP BY k.id, s.name ORDER BY added DESC LIMIT %s""",
            (collection_id, recent_limit)).fetchall()
        dex = conn.execute(
            """SELECT sp.generation, count(*) AS total, count(o.dex_number) AS caught
               FROM species sp LEFT JOIN (SELECT DISTINCT cs.dex_number FROM owned_cards oc
                      JOIN card_species cs ON cs.card_id = oc.card_id WHERE oc.collection_id = %s) o
                    ON o.dex_number = sp.dex_number
               GROUP BY sp.generation ORDER BY sp.generation""", (collection_id,)).fetchall()
        wanted = conn.execute(
            """SELECT count(*) AS cards,
                      COALESCE(sum((SELECT min(e.v::numeric) FROM jsonb_each_text(k.prices) AS e(k, v)
                                     WHERE e.v ~ '^[0-9]+(\\.[0-9]+)?$')), 0) AS cost
               FROM wanted_cards w JOIN cards k ON k.id = w.card_id WHERE w.collection_id = %s""",
            (collection_id,)).fetchone()
    return shape(head, top_cards, sets, recent, dex, wanted)


def shape(head: dict, top_cards: list, sets: list, recent: list, dex: list, wanted: dict) -> dict:
    """Derive the ranked panels from the raw rows (pure, so it is easy to test)."""
    for s in sets:
        s["missing"] = max((s["total"] or 0) - s["owned"], 0)
        s["pct"] = round(s["owned"] * 100 / s["total"]) if s["total"] else 0
    candidates = [s for s in sets if s["total"] and s["owned"] < s["total"] and s["owned"] >= MIN_CARDS_FOR_COMPLETION]
    closest = sorted(candidates, key=lambda s: (-s["owned"] / s["total"], s["missing"], s["name"]))[:5]
    names = species_mod.GENERATION_NAMES
    gens = [{"n": g["generation"], "name": names.get(g["generation"], ""), "caught": g["caught"], "total": g["total"],
             "pct": round(g["caught"] * 100 / g["total"]) if g["total"] else 0} for g in dex]
    return {
        "head": {**head, "completed": sum(1 for s in sets if s["total"] and s["owned"] >= s["total"]),
                 "species": sum(g["caught"] for g in gens), "species_total": sum(g["total"] for g in gens)},
        "top_cards": top_cards,
        "closest": closest,
        "top_sets": sorted((s for s in sets if s["value"]), key=lambda s: -s["value"])[:5],
        "recent": recent,
        "generations": gens,
        "wanted": wanted,
    }
