"""Build downloadable exports of a collection: CSV and JSON.

The JSON/ZIP format ("version": 1) is what importer.py reads back. Entries refer to cards by
their TCGdex id, so a backup restores onto any install; card details are re-fetched if needed.
"""
import csv
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

FORMAT = "pokemon-collection"
VERSION = 1

# What a backup stores per owned entry. name/set/number/rarity are informational (and make the
# file readable); the importer only needs card_id, variant, condition and quantity.
ENTRY_FIELDS = ["card_id", "name", "card_number", "set_name", "set_code", "rarity", "variant", "condition",
                "quantity", "acquired_on", "notes", "created_at"]
CSV_FIELDS = ["card_id", "name", "card_number", "set_name", "set_code", "rarity", "variant", "condition",
              "quantity", "market_price", "total_value", "acquired_on", "notes", "created_at"]


def _cell(value) -> str:
    """Format a value for CSV. Neutralises spreadsheet formula injection."""
    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return "'" + text if text and text[0] in "=+-@\t\r" else text


def _value(row: dict, field: str):
    if field == "market_price":
        return row.get("unit_price")
    if field == "total_value":
        return row["unit_price"] * row["quantity"] if row.get("unit_price") is not None else None
    return row.get(field)


def to_csv(rows: list[dict]) -> bytes:
    """One row per owned entry. UTF-8 with BOM so Excel reads accents correctly."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_FIELDS)
    for row in rows:
        writer.writerow([_cell(_value(row, f)) for f in CSV_FIELDS])
    return buf.getvalue().encode("utf-8-sig")


def _json_default(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)  # dates, Decimal


def to_json(rows: list[dict], collection_name: str = "") -> str:
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "collection": collection_name,
        "cards": [{f: _value(row, f) for f in ENTRY_FIELDS} for row in rows],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default)


# ---- PriceCharting import -------------------------------------------------------------------
# https://www.pricecharting.com/ collection import. The header and column order match the file PriceCharting exports itself.
PRICECHARTING_FIELDS = ["id", "product-name", "console-name", "price-in-pennies", "include-string", "condition-string",
                        "condition-id", "sku", "notes", "cost-basis-in-pennies", "quantity", "date-entered",
                        "date-purchased", "grading-company", "grading-cert-id", "folder"]
# PriceCharting's sample export lists every raw (ungraded) card as Ungraded / "Normal wear" / 1, whatever its condition.
# If you ever want to map our conditions (Near Mint, Lightly Played, ...) to theirs, change it here.
PRICECHARTING_CONDITION = ("Ungraded", "Normal wear", 1)
# A finish PriceCharting lists as its own product gets a bracket tag in the name; holo and normal cards are the plain name.
PRICECHARTING_TAGS = {"Reverse Holofoil": "Reverse Holo", "1st Edition": "1st Edition", "1st Edition Holofoil": "1st Edition"}


def _ascii(text: str) -> str:
    """Flabebe, not Flabébé: PriceCharting names are plain ASCII."""
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().strip()


def _pc_number(row: dict) -> str:
    """The card number as PriceCharting shows it: 057/191 -> 57. Falls back to the end of the card id (sv8-057)."""
    raw = str(row.get("card_number") or "").split("/")[0].strip() or str(row.get("card_id") or "").rsplit("-", 1)[-1]
    return raw.lstrip("0") or "0" if raw.isdigit() else raw


def _pc_date(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "date"):                       # a datetime
        value = value.date()
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


def _pennies(price) -> int:
    if price is None:
        return 0
    return int((Decimal(str(price)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_pricecharting_csv(matches: list[tuple[dict, tuple]]) -> bytes:
    """PriceCharting collection-import file. `matches` is [(owned row, (product id, console-name, product-name))]:
    PriceCharting's import requires its own product id on every row (see pricecharting.py for how we find them).

    One row per owned entry (card + finish + condition, with its quantity). Plain UTF-8, no byte-order mark. Prices are our
    market price for the finish (0 when there isn't one yet). Condition is always Ungraded (id 1), the import's default.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(PRICECHARTING_FIELDS)
    include, condition, condition_id = PRICECHARTING_CONDITION
    for row, (pc_id, console, product) in matches:
        notes = _cell(row.get("notes"))[:300] if row.get("notes") else ""             # PriceCharting allows 300 characters
        writer.writerow([pc_id, product, console, _pennies(row.get("unit_price")), include, condition, condition_id, "", notes,
                         0, row.get("quantity") or 1, _pc_date(row.get("created_at")), _pc_date(row.get("acquired_on")),
                         "", "", ""])
    return buf.getvalue().encode("utf-8")
