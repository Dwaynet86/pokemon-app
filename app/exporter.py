"""Build downloadable exports of a collection: CSV and JSON.

The JSON/ZIP format ("version": 1) is what importer.py reads back. Entries refer to cards by
their TCGdex id, so a backup restores onto any install; card details are re-fetched if needed.
"""
import csv
import io
import json
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

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
# A plain list for PriceCharting's collection import: card name, card number and expansion are enough for it to find the card.
PRICECHARTING_FIELDS = ["Card name", "Card number", "Expansion"]


def _ascii(text: str) -> str:
    """Flabebe, not Flabébé: PriceCharting names are plain ASCII."""
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().strip()


def _pc_number(row: dict) -> str:
    """The card number as PriceCharting shows it: 057/191 -> 57. Falls back to the end of the card id (sv8-057)."""
    raw = str(row.get("card_number") or "").split("/")[0].strip() or str(row.get("card_id") or "").rsplit("-", 1)[-1]
    return raw.lstrip("0") or "0" if raw.isdigit() else raw


def to_pricecharting_csv(rows: list[dict]) -> bytes:
    """Card name, card number and expansion: one row per owned entry (card + finish + condition).

    Plain UTF-8 with no byte-order mark. Quantity, finish, condition and price are not part of this file.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(PRICECHARTING_FIELDS)
    for row in rows:
        writer.writerow([_cell(_ascii(row.get("name"))), _pc_number(row), _cell(_ascii(row.get("set_name")))])
    return buf.getvalue().encode("utf-8")
