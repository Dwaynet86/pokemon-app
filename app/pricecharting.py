"""PriceCharting collection import support.

PriceCharting's import needs its own product id for every row (its "id" column), and there is no way to derive one from a
TCGdex card. So the user loads a PriceCharting CSV that has `id`, `console-name` and `product-name` columns: the price guide
download, or a collection export. Those rows are kept in `pricecharting_products`, and each card we export is matched to
a product by name and expansion. Cards that can't be matched are listed so their ids can be filled in by hand.
"""
import csv
import io
import re
import unicodedata

from .db import get_conn
from .exporter import PRICECHARTING_TAGS, _ascii, _pc_number

# Expansions in another language are separate products on PriceCharting; never match an English card to one by similarity.
_FOREIGN = re.compile(r"\b(japanese|chinese|korean|german|french|spanish|italian|portuguese|dutch|indonesian|thai)\b", re.I)


def key(text: str) -> str:
    """Lower-case, accent-free and punctuation-free, so 'Scarlet & Violet' and 'scarlet and violet' compare equal."""
    text = unicodedata.normalize("NFKD", (text or "").replace("&", " and ")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9#]+", " ", text).split())


# ---- reading a PriceCharting CSV -----------------------------------------------------------

class ProductFileError(ValueError):
    pass


def parse_products(stream) -> tuple[list[tuple[int, str, str]], int]:
    """(id, console-name, product-name) for every Pokemon row of a PriceCharting CSV, and how many rows were skipped.

    Accepts a binary file. Rows with no id (an unmatched-cards file that hasn't been filled in yet) and rows that aren't
    Pokemon (the price guide covers every category) are skipped.
    """
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="")
    reader = csv.DictReader(text)
    names = {(n or "").strip().lower() for n in (reader.fieldnames or [])}
    missing = {"id", "console-name", "product-name"} - names
    if missing:
        raise ProductFileError("This doesn't look like a PriceCharting file: it has no " +
                               ", ".join(f"'{m}'" for m in sorted(missing)) + " column.")
    products, skipped = [], 0
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        console = row.get("console-name", "")
        if not row.get("id", "").isdigit() or not console.lower().startswith("pokemon") or not row.get("product-name"):
            skipped += 1
            continue
        products.append((int(row["id"]), console, row["product-name"]))
    return products, skipped


# ---- storage -------------------------------------------------------------------------------

def count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT count(*) AS n FROM pricecharting_products").fetchone()["n"]


def save_products(products: list[tuple[int, str, str]]) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(products), 1000):
            cur.executemany(
                """INSERT INTO pricecharting_products (id, console_name, product_name) VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET console_name = EXCLUDED.console_name, product_name = EXCLUDED.product_name""",
                products[i:i + 1000])


def all_products() -> list[tuple[int, str, str]]:
    with get_conn() as conn:
        return [(r["id"], r["console_name"], r["product_name"]) for r in
                conn.execute("SELECT id, console_name, product_name FROM pricecharting_products").fetchall()]


# ---- matching our cards to products --------------------------------------------------------

def product_names(row: dict) -> list[str]:
    """The product names this owned entry could have on PriceCharting, best guess first."""
    name, number, variant = _ascii(row.get("name")), _pc_number(row), row.get("variant") or ""

    def tagged(tag):
        return f"{name} [{tag}] #{number}"
    plain = f"{name} #{number}"
    if variant in PRICECHARTING_TAGS:
        return [tagged(PRICECHARTING_TAGS[variant])]
    if variant == "Holofoil":
        return [plain, tagged("Holo")]
    if variant == "Normal":
        return [plain, tagged("Non-Holo")]
    return [plain]


def console_name(row: dict) -> str:
    set_name = _ascii(row.get("set_name"))
    return set_name if set_name.lower().startswith("pokemon") else f"Pokemon {set_name}".strip()


class Index:
    def __init__(self, products):
        self.exact, self.by_product = {}, {}
        for pid, console, product in products:
            entry = (pid, console, product)
            self.exact[(key(console), key(product))] = entry
            self.by_product.setdefault(key(product), []).append(entry)

    def find(self, row: dict):
        """The (id, console-name, product-name) for an owned entry, or None.

        First by exact expansion and name; failing that by name alone, among the English expansions whose name contains
        (or is contained in) ours, when that leaves exactly one product.
        """
        ckey = key(console_name(row))
        names = [key(n) for n in product_names(row)]
        for pkey in names:
            if hit := self.exact.get((ckey, pkey)):
                return hit
        set_key = key(row.get("set_name"))
        for pkey in names:
            near = [e for e in self.by_product.get(pkey, [])
                    if not _FOREIGN.search(e[1]) and set_key and (set_key in key(e[1]) or key(e[1]).removeprefix("pokemon ") in set_key)]
            if len({e[0] for e in near}) == 1:
                return near[0]
        return None


def match_all(rows: list[dict], products) -> tuple[list[tuple[dict, tuple]], list[dict]]:
    """Split owned rows into [(row, product)] that matched and [row] that didn't."""
    index = Index(products)
    matched, unmatched = [], []
    for row in rows:
        hit = index.find(row)
        (matched.append((row, hit)) if hit else unmatched.append(row))
    return matched, unmatched


def to_unmatched_csv(rows: list[dict]) -> bytes:
    """Entries with no PriceCharting match. Fill in the `id` column (the 'PriceCharting ID' on the item's page on
    pricecharting.com) and load this file back in: product-name and console-name are exactly what we look up by."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["id", "product-name", "console-name", "quantity", "variant", "card_id"])
    for row in rows:
        writer.writerow(["", product_names(row)[0], console_name(row), row.get("quantity") or 1, row.get("variant") or "",
                         row.get("card_id") or ""])
    return buf.getvalue().encode("utf-8")
