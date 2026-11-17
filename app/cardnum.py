"""Collector-number helpers shared by the card clients and the matcher."""
import re


def number_sort(card_number: str | None) -> int | None:
    match = re.search(r"(\d+)", (card_number or "").split("/")[0])
    return int(match.group(1)) if match else None


def normalize_number(card_number: str | None) -> str:
    """'025/185' -> '25', 'SWSH039' -> 'SWSH39', 'TG05/TG30' -> 'TG5' (for comparing)."""
    head = (card_number or "").split("/")[0].strip().upper()
    return re.sub(r"(?<!\d)0+(?=\d)", "", head)


def number_total(card_number: str | None) -> str:
    """'025/185' -> '185' ('' when the number has no denominator)."""
    parts = (card_number or "").split("/")
    return re.sub(r"(?<!\d)0+(?=\d)", "", parts[1].strip().upper()) if len(parts) > 1 else ""


def display_number(local_id: str | None, official: int | None) -> str | None:
    """'25' + 185 -> '25/185' (how it's printed); other numbers (SWSH039, TG05) stay as they are."""
    if not local_id:
        return None
    return f"{local_id}/{official}" if official and local_id.isdigit() else local_id
