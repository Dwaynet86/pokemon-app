"""Identify a photographed card: scan it, then match the result to the TCGdex catalog.

    photo --crop--> straightened card --scan API--> product ids + scores
          --product lookup--> name, number, set --search TCGdex--> our card records --rank--> candidates

The scan API recognises the *picture*; it doesn't know our catalog. So each scan hit is turned
into a name / number / set, matched against TCGdex cards with the same scoring used for manual
search (identify.rank), and nudged by the scan score. The user always confirms the result, and
every stored detail comes from the TCGdex record, never from the scan.
"""
from dataclasses import dataclass, field

from PIL import Image

from . import cardcrop, identify
from .cardnum import normalize_number
from .tcgtracking import ScanError

MAX_PRODUCTS = 3        # scan hits whose details are looked up
PER_HIT = 3             # candidates kept per hit


@dataclass
class PhotoResult:
    candidates: list[identify.Candidate] = field(default_factory=list)
    reading: identify.Reading | None = None    # name / number / set of the best scan hit
    top_score: float | None = None             # that hit's match percentage
    crop_found: bool = True                    # False: the card's edges weren't found in the photo
    error: str | None = None                   # scanning failed outright
    note: str | None = None                    # trouble while matching (e.g. TCGdex unreachable)

    @property
    def query(self) -> str:
        return self.reading.name if self.reading else ""


def _best(hits) -> float:
    return max((h.score for h in hits), default=0.0)


def scan_photo(scanner, crop: cardcrop.Crop, min_score: float):
    """Scan the straightened card; if it doesn't match well, retry upside down, then the whole photo.

    Returns hits, best first. Retries are only spent when the first result is weak, and a failure
    in a retry never discards a usable first result.
    """
    attempts = [crop.image, crop.image.rotate(180)]
    if crop.found:
        attempts.append(crop.whole)          # the outline may have been a box inside the card
    hits = scanner.scan(cardcrop.encode_jpeg(attempts[0]))
    for image in attempts[1:]:
        if _best(hits) >= min_score:
            break
        try:
            more = scanner.scan(cardcrop.encode_jpeg(image))
        except (ScanError, cardcrop.CropError):
            continue
        if _best(more) > _best(hits):
            hits = more
    return sorted(hits, key=lambda h: -h.score)


def identify_photo(path, scanner, search, local_search, min_score: float = 50) -> PhotoResult:
    """Identify the card photographed in the image file at `path`.

    `search(name, number)` returns TCGdex card rows (may raise); `local_search(name)` is the
    cache-only fallback.
    """
    try:
        img = Image.open(path)
        img.load()
        crop = cardcrop.crop_card(img)
    except (OSError, ValueError):
        return PhotoResult(error="That photo couldn't be read.")
    result = PhotoResult(crop_found=crop.found)
    try:
        hits = scan_photo(scanner, crop, min_score)
    except (ScanError, cardcrop.CropError) as exc:
        result.error = str(exc)
        return result
    if not hits:
        return result

    pool: dict[str, identify.Candidate] = {}
    searched: dict[tuple, list[dict]] = {}
    looked_up = 0
    for hit in hits[:MAX_PRODUCTS]:
        try:
            product = scanner.product(hit.product_id)
        except ScanError:
            product = None
        if not product or not product["base_name"]:
            continue
        looked_up += 1
        reading = identify.Reading(name=product["base_name"], number=product["number"],
                                   set_code=product["set_abbr"], set_name=product["set_name"])
        if result.reading is None:
            result.reading, result.top_score = reading, hit.score
        key = (reading.name.casefold(), normalize_number(reading.number))
        if key not in searched:
            try:
                searched[key] = search(reading.name, reading.number)
            except Exception as exc:  # TCGdex down, etc.: fall back to what's cached
                result.note = str(exc)
                searched[key] = local_search(reading.name)
        # a weaker hit than the best one counts for less, so it can't tie the top match
        bonus = max(0, min(10, round(hit.score / 10))) - round(max(0.0, hits[0].score - hit.score) / 2)
        for cand in identify.rank(reading, searched[key], limit=PER_HIT):
            cand.score += bonus
            cand.reasons.append(f"photo {round(hit.score)}%")
            known = pool.get(cand.card["id"])
            if known is None or cand.score > known.score:
                pool[cand.card["id"]] = cand
    if looked_up == 0:
        result.note = result.note or "The scan found a match, but its card details couldn't be looked up."
    result.candidates = sorted(pool.values(), key=lambda c: -c.score)[:6]
    return result
