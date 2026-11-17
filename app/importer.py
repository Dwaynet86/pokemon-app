"""Restore owned cards into a collection from an export (ZIP backup or cards JSON).

Safety:
  * the whole file is validated before anything is written;
  * every card in the file must exist on TCGdex (unknown ones are fetched and cached first);
    if any can't be found, nothing is imported;
  * database changes happen in one transaction (all or nothing);
  * ZIP entries are only ever read by exact name, never extracted to disk.
"""
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime

from . import catalog_service as service, owned_repository as owned
from .db import get_conn

MAX_ENTRIES = 20_000
MAX_JSON_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 50_000
CARD_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
CONDITIONS = {c.lower(): c for c in owned.CONDITIONS}


class BackupError(ValueError):
    """The file can't be imported. `problems` is a list of user-facing messages."""

    def __init__(self, *problems: str):
        super().__init__(problems[0] if problems else "Import failed.")
        self.problems = list(problems)


@dataclass
class Entry:
    card_id: str
    variant: str
    condition: str
    quantity: int
    notes: str | None = None
    acquired_on: date | None = None
    created_at: datetime | None = None


@dataclass
class Bundle:
    entries: list[Entry]
    zf: zipfile.ZipFile | None = None


@dataclass
class Result:
    created: int = 0        # new rows
    merged: int = 0         # added to rows already in the collection
    skipped: int = 0        # left alone because they were already there
    copies: int = 0         # total copies added
    cards_fetched: int = 0  # cards looked up on TCGdex because they weren't cached yet


# ---- Reading ---------------------------------------------------------------

def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _read_payload(upload) -> tuple[object, zipfile.ZipFile | None]:
    filename = (upload.filename or "").lower()
    zf = None
    try:
        if filename.endswith(".zip"):
            zf = zipfile.ZipFile(upload.stream)
            if len(zf.infolist()) > MAX_ZIP_ENTRIES:
                raise BackupError("This ZIP has too many files to be a collection backup.")
            try:
                info = zf.getinfo("cards.json")
            except KeyError:
                raise BackupError("This ZIP has no cards.json, so it isn't a collection backup.") from None
            if info.file_size > MAX_JSON_BYTES:
                raise BackupError("The data file inside the ZIP is too large.")
            raw = zf.read(info)
        elif filename.endswith(".json"):
            raw = upload.stream.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise BackupError("That JSON file is too large.")
        else:
            raise BackupError("Choose a .zip backup or a .json export.")
        return json.loads(raw), zf
    except zipfile.BadZipFile:
        raise BackupError("That file isn't a valid ZIP.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BackupError("The data in that file couldn't be read.") from None
    except BackupError:
        if zf:
            zf.close()
        raise


def _parse_date(value, label: str, problems: list[str]) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        problems.append(f"{label}: “{text}” isn't a valid date.")
        return None


def _parse_timestamp(value) -> datetime | None:
    text = _text(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def _entry_from_raw(raw, index: int, problems: list[str]) -> Entry | None:
    if not isinstance(raw, dict):
        problems.append(f"Entry {index} isn't valid.")
        return None
    card_id = _text(raw.get("card_id"))
    label = f"Entry {index} ({_text(raw.get('name')) or card_id or 'no card'})"
    before = len(problems)
    if not CARD_ID_RE.match(card_id):
        problems.append(f"{label}: missing or invalid card_id.")
    variant = _text(raw.get("variant"))
    if not variant or len(variant) > 60:
        problems.append(f"{label}: variant must be 1-60 characters.")
    condition = CONDITIONS.get(_text(raw.get("condition")).lower())
    if condition is None:
        problems.append(f"{label}: condition “{_text(raw.get('condition'))}” isn't one of "
                        f"{', '.join(owned.CONDITIONS)}.")
    try:
        quantity = int(float(_text(raw.get("quantity")) or 1))
    except ValueError:
        quantity = 0
    if not 1 <= quantity <= 999:
        problems.append(f"{label}: quantity must be between 1 and 999.")
    acquired = _parse_date(raw.get("acquired_on"), label, problems)
    if len(problems) > before:
        return None
    return Entry(card_id, variant, condition, quantity, _text(raw.get("notes"))[:500] or None, acquired,
                 _parse_timestamp(raw.get("created_at")))


def _merge_duplicates(entries: list[Entry]) -> list[Entry]:
    """Same card + variant + condition twice in one file counts as one entry with the summed quantity."""
    merged: dict[tuple, Entry] = {}
    for e in entries:
        key = (e.card_id, e.variant.lower(), e.condition)
        if key in merged:
            first = merged[key]
            first.quantity = min(999, first.quantity + e.quantity)
            first.notes = first.notes or e.notes
        else:
            merged[key] = e
    return list(merged.values())


def load_bundle(upload) -> Bundle:
    """Read and fully validate an uploaded backup. Raises BackupError."""
    payload, zf = _read_payload(upload)
    problems: list[str] = []

    if isinstance(payload, dict) and isinstance(payload.get("cards"), list):
        version = payload.get("version", 1)
        if not isinstance(version, int) or version > 1:
            raise BackupError("This backup was made by a newer version of the app.")
        raw_entries = payload["cards"]
    elif isinstance(payload, list):
        raw_entries = payload
    else:
        raise BackupError("That file doesn't contain a list of cards.")
    if len(raw_entries) > MAX_ENTRIES:
        raise BackupError(f"Too many entries to import at once (limit {MAX_ENTRIES:,}).")

    entries = [x for i, raw in enumerate(raw_entries, 1) if (x := _entry_from_raw(raw, i, problems))]
    if problems:
        if zf:
            zf.close()
        raise BackupError(*problems)
    return Bundle(entries=_merge_duplicates(entries), zf=zf)


# ---- Writing ---------------------------------------------------------------

def restore(collection_id: int, upload, skip_duplicates: bool = True) -> Result:
    """Import a backup into a collection. Raises BackupError if the file is unusable."""
    bundle = load_bundle(upload)
    result = Result()
    try:
        ids = list(dict.fromkeys(e.card_id for e in bundle.entries))
        failures, result.cards_fetched = service.ensure_cards(ids)
        if failures:
            shown = [f"Card {cid}: {why}" for cid, why in list(failures.items())[:5]]
            more = len(failures) - len(shown)
            raise BackupError(*shown, *([f"and {more} more cards couldn't be found."] if more > 0 else []))

        with get_conn() as conn:
            for e in bundle.entries:
                if skip_duplicates and owned.copy_exists(collection_id, e.card_id, e.variant, e.condition, conn):
                    result.skipped += 1
                    continue
                row = owned.add_copy(collection_id, e.card_id, e.variant, e.condition, e.quantity,
                                     e.notes, conn, e.acquired_on, e.created_at)
                result.copies += e.quantity
                if row["inserted"]:
                    result.created += 1
                else:
                    result.merged += 1
    finally:
        if bundle.zf:
            bundle.zf.close()
    return result
