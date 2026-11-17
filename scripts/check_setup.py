"""Check the card services and their response shapes WITHOUT touching the database.

    python scripts/check_setup.py                 # TCGdex, TCG Tracking, and PokeWallet prices if a key is set
    python scripts/check_setup.py card-photo.jpg  # also scans a photo of a card end to end

Run this first on a new install. It prints what the services actually return, so any
field-name difference from their documentation shows up here rather than in the app.
With a photo it also saves scan-crop.jpg: the exact image that was sent to the scanner.
"""
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import cardcrop, identify, photoid, pokewallet as pw, tcgdex as tc, tcgtracking as tt  # noqa: E402
from app.cardnum import normalize_number  # noqa: E402
from app.config import Config  # noqa: E402


def main() -> None:
    client = tc.TCGdex(Config.TCGDEX_BASE_URL, Config.TCGDEX_LANG, Config.TCGDEX_TIMEOUT)

    print("1. TCGdex: expansions")
    sets = client.list_sets()
    print(f"   {len(sets)} sets. first: {sets[0] if sets else None}")
    if not sets:
        sys.exit("   no sets returned; the response shape differs from the docs")
    swsh = next((s for s in sets if s["tcgdex_id"] == "swsh3"), sets[-1])

    print(f"2. TCGdex: one expansion ({swsh['name']}) with all its cards in a single request")
    info, cards = client.get_set(swsh["tcgdex_id"])
    print(f"   set code {info and info['set_code']!r}, series {info and info['series']!r}, {len(cards)} cards")
    if not (info and info["set_code"]):
        print("   NOTE: no set code (abbreviation) found; photo matching will rely on the set name and number")
    print(f"   first card: {cards[0]['name']} {cards[0]['card_number']}  image: {cards[0]['image_base']}")

    print("3. TCGdex: a full card record")
    detail = client.get_card(cards[len(cards) // 2]["id"])
    print("  ", json.dumps({k: v for k, v in detail.items() if k not in ("set_info",)}, default=str)[:400])
    if detail["dex_ids"] is None or not detail["variants"]:
        print("   WARNING: dexId or variants missing; check the full card JSON")

    print("3b. TCGdex: prices (the main price source)")
    for card in (detail, client.get_card(cards[0]["id"])):
        raw = client._json(f"/cards/{card['id']}").get("pricing")
        print(f"   {card['name']} {card['card_number']}: parsed {card['prices']}")
        print(f"      raw pricing: {json.dumps(raw)[:300] if raw else 'none published'}")
    if not any(c["prices"] for c in (detail,)):
        print("   NOTE: no TCGplayer prices parsed for that card. If the raw block above has prices, the key names differ;"
              " send me the output and I'll add them. Cards without a price fall back to PokeWallet.")

    print("4. TCGdex: artwork (static host, not the API)")
    if cards[0]["image_base"]:
        data = client.download(f"{cards[0]['image_base']}/low.webp")
        print(f"   {len(data)} bytes, valid WebP")

    print("5. TCGdex: search by name")
    found = client.search_cards("pikachu")
    print(f"   {len(found)} cards named like 'pikachu'")

    if Config.POKEWALLET_API_KEY:
        print("6. PokeWallet: price lookup for that card")
        wallet = pw.PokeWallet(Config.POKEWALLET_API_KEY, Config.POKEWALLET_BASE_URL)
        reading = identify.Reading(name=detail["name"], number=detail["card_number"],
                                   set_code=(detail["set_info"] or {}).get("set_code") or "",
                                   set_name=(detail["set_info"] or {}).get("name") or "")
        for query in (f"{detail['name']} {normalize_number(detail['card_number'])}", detail["name"]):
            ranked = identify.rank(reading, wallet.search(query)["cards"], 3)
            print(f"   query {query!r}: " + ", ".join(
                f"{c.score} {c.card['name']} {c.card['card_number']} {c.card['prices']}" for c in ranked))
        print(f"   requests left this hour: {pw.rate.hour_remaining} of {pw.rate.hour_limit}")
    else:
        print("6. PokeWallet: skipped (no POKEWALLET_API_KEY; prices will be off)")

    scanner = tt.TCGTracking(Config.TCGTRACKING_BASE_URL, Config.TCGTRACKING_GAME_ID, Config.TCGTRACKING_TIMEOUT)

    print("7. TCG Tracking: games (the photo scanner)")
    try:
        games = scanner.categories()
        pokemon = [g for g in games if "pokemon" in json.dumps(g).lower() or "pokémon" in json.dumps(g).lower()]
        print(f"   {len(games)} games. Pokemon entries: {json.dumps(pokemon)[:300]}")
        print(f"   configured game id: {Config.TCGTRACKING_GAME_ID} (Pokemon should be 3; set TCGTRACKING_GAME_ID if not)")
    except tt.ScanError as exc:
        print(f"   FAILED: {exc}")

    if len(sys.argv) > 1:
        scan_photo_check(Path(" ".join(sys.argv[1:]).strip().strip("\"'")).expanduser(), scanner, client)


def scan_photo_check(path: Path, scanner, tcgdex) -> None:
    print(f"8. Scanning {path.name}")
    if not path.is_file():
        sys.exit(f"   can't find a photo at: {path.resolve()}\n"
                 f"   (looked relative to {Path.cwd()}). Give the full path, e.g. "
                 f"python scripts/check_setup.py \"C:\\Users\\you\\Pictures\\card.jpg\"")
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()      # phone HEIC photos
    except ImportError:
        pass
    try:
        photo = Image.open(path)
        photo.load()
    except Exception as exc:
        sys.exit(f"   found the file but couldn't open it as an image: {exc}")
    crop = cardcrop.crop_card(photo)
    jpeg = cardcrop.encode_jpeg(crop.image)
    Path("scan-crop.jpg").write_bytes(jpeg)
    print(f"   card outline {'found' if crop.found else 'NOT found (whole photo used)'}; "
          f"{len(jpeg) // 1024} KB sent; saved as scan-crop.jpg (open it: it should show one upright card)")

    hits = photoid.scan_photo(scanner, crop, Config.SCAN_MIN_SCORE)
    print(f"   scan hits (best first): {[(h.product_id, round(h.score)) for h in hits]}")
    if not hits:
        print("   no hits: try a flatter, better lit photo on a plain background")
        return
    for hit in hits[:3]:
        raw = scanner._json(scanner._request("GET", f"/products/{hit.product_id}"))
        product = tt.normalize_product(raw, hit.product_id)
        print(f"   product {hit.product_id} ({round(hit.score)}%) raw: {json.dumps(raw)[:500]}")
        print(f"      parsed: {product}")
        if not product or not product["base_name"]:
            print("      WARNING: couldn't read a name from that response; adjust normalize_product in app/tcgtracking.py")

    def search(name, number):
        wanted = normalize_number(number)
        found = tcgdex.search_cards(name)
        return [c for c in found if not wanted or normalize_number(c["local_id"]) == wanted] or found

    result = photoid.identify_photo(path, scanner, search, lambda name: [], Config.SCAN_MIN_SCORE)
    print("9. Matched to TCGdex cards" + (f" (note: {result.note})" if result.note else ""))
    if result.error:
        print(f"   FAILED: {result.error}")
    for cand in result.candidates:
        c = cand.card
        print(f"   {cand.score:3d}  {c['id']:<14} {c['name']} {c.get('card_number') or ''}  ({', '.join(cand.reasons)})")
    if not result.candidates:
        print("   no TCGdex card matched; compare the parsed product above with the card's name and number")


if __name__ == "__main__":
    main()
