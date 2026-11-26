"""Maintenance page: exports, backup format, import validation and all-or-nothing behaviour."""
import contextlib
import io
import json
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage

from app import exporter, images, importer, pricecharting
from tests.conftest import OWNED

BASE = "/c/ash-s-cards/maintenance"


def entry(**kw):
    return {"card_id": "sv8-057", "name": "Pikachu ex", "variant": "Holofoil", "condition": "Near Mint",
            "quantity": 2, **kw}


def upload(payload, name="backup.json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return FileStorage(stream=io.BytesIO(body), filename=name)


def make_zip(entries, photos=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cards.json", json.dumps({"format": "pokemon-collection", "version": 1, "cards": entries}))
        for name, data in (photos or {}).items():
            zf.writestr(f"photos/{name}", data)
    return upload(buf.getvalue(), "backup.zip")


# ---- export ----------------------------------------------------------------------------

def test_csv_has_bom_prices_and_neutralises_formulas():
    row = {**OWNED, "name": "=HYPERLINK(\"x\")", "notes": "-1+1"}
    raw = exporter.to_csv([row])
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "'=HYPERLINK" in text and "'-1+1" in text
    header, line = text.strip().splitlines()[:2]
    assert header.split(",")[:3] == ["card_id", "name", "card_number"]
    assert "4.50,9.00" in line                       # market price, total (price x quantity)


def test_json_export_reads_back_through_the_importer():
    row = {**OWNED, "acquired_on": date(2025, 1, 2), "notes": "gift", "photo_filename": "a" * 32 + ".webp"}   # legacy field: never exported
    data = json.loads(exporter.to_json([row], "Ash's Cards"))
    assert data["format"] == "pokemon-collection" and data["version"] == 1 and data["collection"] == "Ash's Cards"
    bundle = importer.load_bundle(upload(data))
    (e,) = bundle.entries
    assert (e.card_id, e.variant, e.condition, e.quantity, e.notes) == ("sv8-057", "Holofoil", "Near Mint", 2, "gift")
    assert e.acquired_on == date(2025, 1, 2)
    assert "photo" not in data["cards"][0] and "photo_filename" not in json.dumps(data)


# ---- validation ------------------------------------------------------------------------

@pytest.mark.parametrize("bad, fragment", [
    (entry(condition="Pristine"), "condition"),
    (entry(quantity=0), "quantity"), (entry(quantity=1000), "quantity"), (entry(quantity="many"), "quantity"),
    (entry(card_id="../etc"), "card_id"), (entry(card_id=""), "card_id"),
    (entry(variant=""), "variant"), (entry(acquired_on="yesterday"), "valid date"), ("nope", "isn't valid"),
])
def test_bad_entries_are_reported_and_block_the_import(bad, fragment):
    with pytest.raises(importer.BackupError) as exc:
        importer.load_bundle(upload({"version": 1, "cards": [entry(), bad]}))
    assert fragment in " ".join(exc.value.problems) and "Entry 2" in " ".join(exc.value.problems)


@pytest.mark.parametrize("file, fragment", [
    (upload(b"{}", "x.txt"), ".zip backup"), (upload(b"not json", "x.json"), "couldn't be read"),
    (upload({"cards": "x"}), "list of cards"), (upload({"version": 9, "cards": []}), "newer version"),
    (upload(b"junk", "x.zip"), "valid ZIP"),
])
def test_unusable_files(file, fragment):
    with pytest.raises(importer.BackupError) as exc:
        importer.load_bundle(file)
    assert fragment in exc.value.problems[0]


def test_zip_without_cards_json_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("other.json", "{}")
    with pytest.raises(importer.BackupError, match="no cards.json"):
        importer.load_bundle(upload(buf.getvalue(), "x.zip"))


def test_condition_is_case_insensitive_and_duplicates_in_a_file_are_summed():
    bundle = importer.load_bundle(upload({"cards": [
        entry(condition="near mint", quantity=2), entry(variant="holofoil", quantity=3, notes="n"),
        entry(variant="Normal", quantity=1)]}))
    assert [(e.variant, e.condition, e.quantity) for e in bundle.entries] == \
           [("Holofoil", "Near Mint", 5), ("Normal", "Near Mint", 1)]


# ---- restore ---------------------------------------------------------------------------

@contextlib.contextmanager
def fake_db(existing=(), failures=None, fetched=0, add_error=None):
    """Patch everything restore() touches; returns the list of add_copy calls."""
    calls = []

    def add_copy(cid, card_id, variant, condition, qty, notes, conn, acquired, created):
        if add_error:
            raise add_error
        calls.append((card_id, variant, condition, qty))
        merged = (card_id, variant, condition) in existing
        return {"inserted": not merged}

    with patch("app.importer.get_conn", contextlib.nullcontext), \
         patch("app.importer.service.ensure_cards", return_value=(failures or {}, fetched)), \
         patch("app.importer.owned.copy_exists", side_effect=lambda cid, c, v, k, conn: (c, v, k) in existing), \
         patch("app.importer.owned.add_copy", side_effect=add_copy):
        yield calls


def test_restore_skips_existing_by_default(app):
    payload = {"cards": [entry(), entry(variant="Normal"), entry(card_id="sv8-058")]}
    with app.app_context(), fake_db(existing={("sv8-057", "Holofoil", "Near Mint")}) as calls:
        r = importer.restore(10, upload(payload), skip_duplicates=True)
    assert (r.created, r.skipped, r.merged, r.copies) == (2, 1, 0, 4)
    assert [c[:3] for c in calls] == [("sv8-057", "Normal", "Near Mint"), ("sv8-058", "Holofoil", "Near Mint")]


def test_restore_can_add_quantities_to_existing_rows(app):
    with app.app_context(), fake_db(existing={("sv8-057", "Holofoil", "Near Mint")}) as calls:
        r = importer.restore(10, upload({"cards": [entry(quantity=3)]}), skip_duplicates=False)
    assert (r.created, r.merged, r.skipped, r.copies) == (0, 1, 0, 3) and len(calls) == 1


def test_unknown_cards_block_everything(app):
    payload = {"cards": [entry(), entry(card_id="zzz-1")]}
    with app.app_context(), fake_db(failures={"zzz-1": "TCGdex has no record of that."}) as calls:
        with pytest.raises(importer.BackupError) as exc:
            importer.restore(10, upload(payload))
    assert "zzz-1" in exc.value.problems[0] and calls == []


def test_old_backup_zip_with_photos_restores_without_them(app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    name = "c" * 32 + ".webp"
    with app.app_context(), fake_db() as calls:
        r = importer.restore(10, make_zip([entry(photo=name)], {name: b"RIFFxxxxWEBP"}))
    assert r.created == 1 and len(calls) == 1
    assert not list(tmp_path.rglob("*.webp"))                      # the photo in the ZIP is ignored


PC_PRODUCTS = [(14450985, "Pokemon 30th Celebration", "Vulpix #9"),
               (111, "Pokemon Surging Sparks", "Pikachu ex #57"),
               (112, "Pokemon Surging Sparks", "Pikachu ex [Reverse Holo] #57"),
               (113, "Pokemon Japanese Surging Sparks", "Raichu #58"),
               (114, "Pokemon Scarlet & Violet 151", "Flabebe #81")]


def pc_rows(*rows, products=PC_PRODUCTS):
    import csv as _csv
    matched, _ = pricecharting.match_all(list(rows), products)
    return list(_csv.DictReader(io.StringIO(exporter.to_pricecharting_csv(matched).decode())))


def test_pricecharting_header_matches_their_export():
    raw = exporter.to_pricecharting_csv([])
    assert not raw.startswith(b"\xef\xbb\xbf")                        # no BOM: the first header must read literally
    assert raw.decode().strip() == ("id,product-name,console-name,price-in-pennies,include-string,condition-string,condition-id,"
                                    "sku,notes,cost-basis-in-pennies,quantity,date-entered,date-purchased,grading-company,"
                                    "grading-cert-id,folder")


def test_pricecharting_row_matches_their_sample_with_the_product_id():
    # Their sample: "14450985, Vulpix #9, Pokemon 30th Celebration, 114, Ungraded, Normal wear, 1, ..., 0, 1, 2026-11-26"
    row = {**OWNED, "name": "Vulpix", "card_number": "009/030", "set_name": "30th Celebration", "variant": "Normal",
           "unit_price": Decimal("1.14"), "quantity": 1, "notes": None, "acquired_on": None,
           "created_at": datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)}
    (r,) = pc_rows(row)
    assert r["id"] == "14450985"                                           # the mandatory column
    assert (r["product-name"], r["console-name"], r["price-in-pennies"]) == ("Vulpix #9", "Pokemon 30th Celebration", "114")
    assert (r["include-string"], r["condition-string"], r["condition-id"]) == ("Ungraded", "Normal wear", "1")
    assert (r["cost-basis-in-pennies"], r["quantity"], r["date-entered"], r["date-purchased"]) == ("0", "1", "2026-11-26", "")


def test_pricecharting_matching_finish_prices_notes_and_fallbacks():
    base = {**OWNED, "card_number": "57/191", "set_name": "Surging Sparks", "notes": "x" * 400, "acquired_on": date(2025, 1, 2)}
    rows = pc_rows(
        {**base, "variant": "Holofoil", "unit_price": Decimal("4.5"), "quantity": 2},
        {**base, "variant": "Reverse Holofoil", "unit_price": Decimal("0.995")},
        {**base, "name": "Flabébé", "card_number": None, "card_id": "sv3pt5-081", "set_name": "151", "variant": "Normal"},
        {**base, "name": "Raichu", "card_number": "58/191", "variant": "Normal"})       # only a Japanese Raichu exists: no match
    assert len(rows) == 3                                                               # ...so that row is left out, not guessed
    holo, rev, flabebe = rows
    assert holo["id"] == "111" and holo["price-in-pennies"] == "450" and holo["quantity"] == "2"
    assert len(holo["notes"]) == 300 and holo["date-purchased"] == "2025-01-02"          # PriceCharting's notes limit
    assert rev["id"] == "112" and rev["product-name"] == "Pikachu ex [Reverse Holo] #57" and rev["price-in-pennies"] == "100"
    assert flabebe["id"] == "114" and flabebe["console-name"] == "Pokemon Scarlet & Violet 151"   # near-match on the set name


def test_pricecharting_unmatched_are_listed_and_a_filled_file_loads_back():
    raichu = {**OWNED, "name": "Raichu", "card_number": "58/191", "set_name": "Surging Sparks", "variant": "Normal"}
    matched, unmatched = pricecharting.match_all([raichu, {**OWNED, "name": "Vulpix", "card_number": "9", "set_name": "30th Celebration",
                                                           "variant": "Normal"}], PC_PRODUCTS)
    assert [m[1][0] for m in matched] == [14450985] and unmatched == [raichu]          # the Japanese one is never used
    lines = pricecharting.to_unmatched_csv(unmatched).decode().splitlines()
    assert lines[0] == "id,product-name,console-name,quantity,variant,card_id"
    assert lines[1].startswith(",Raichu #58,Pokemon Surging Sparks,")
    filled = ("\n".join([lines[0], "9999" + lines[1]])).encode()                      # user types the id in front of the comma
    products, skipped = pricecharting.parse_products(io.BytesIO(filled))
    assert products == [(9999, "Pokemon Surging Sparks", "Raichu #58")] and skipped == 0
    assert pricecharting.match_all([raichu], products)[0][0][1][0] == 9999


def test_loading_a_price_guide_keeps_only_pokemon_rows_with_ids_and_rejects_other_files():
    guide = (b"id,console-name,product-name,loose-price\n1,Pokemon Surging Sparks,Pikachu ex #57,$4\n"
             b"2,Nintendo 64,Mario Kart,$30\n,Pokemon Base Set,Charmander #46,$1\n3,Pokemon Base Set,Charmander #46,$2\n")
    products, skipped = pricecharting.parse_products(io.BytesIO(b"\xef\xbb\xbf" + guide))
    assert products == [(1, "Pokemon Surging Sparks", "Pikachu ex #57"), (3, "Pokemon Base Set", "Charmander #46")] and skipped == 2
    with pytest.raises(pricecharting.ProductFileError, match="'id'"):
        pricecharting.parse_products(io.BytesIO(b"name,price\nx,1\n"))


# ---- routes ----------------------------------------------------------------------------

def test_maintenance_is_owner_only(client, login, stub):
    login()
    for role in ("viewer", "editor"):
        stub["role"] = role
        for path in ("/", "/export.csv", "/export.json", "/export-pricecharting.csv", "/export-pricecharting-unmatched.csv"):
            assert client.get(BASE + path).status_code == 403
        assert client.post(BASE + "/import").status_code == 403
        assert b"Maintenance" not in client.get("/c/ash-s-cards/").data
    stub["role"] = None
    assert client.get(BASE + "/").status_code == 404


def test_owner_sees_page_and_nav_link(client, login, stub):
    login()
    stub["role"] = "owner"
    html = client.get(BASE + "/").data.decode()
    assert "Backup (JSON)" in html and "photo" not in html.lower() and "Artifact" not in html and "Est. value" in html
    assert 'href="/c/ash-s-cards/maintenance/"' in client.get("/c/ash-s-cards/").data.decode()


def test_exports_download_with_the_right_types(client, login, stub, app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    login()
    stub["role"] = "owner"
    csv_resp = client.get(BASE + "/export.csv")
    assert csv_resp.mimetype == "text/csv" and "ash-s-cards-" in csv_resp.headers["Content-Disposition"]
    assert b"sv8-057" in csv_resp.data
    assert json.loads(client.get(BASE + "/export.json").data)["cards"][0]["card_id"] == "sv8-057"
    assert client.get(BASE + "/export-pricecharting.csv").status_code == 302              # no product list loaded yet
    stub["pc_products"] = [(111, "Pokemon Surging Sparks", "Pikachu ex #57")]
    pc = client.get(BASE + "/export-pricecharting.csv")
    assert pc.mimetype == "text/csv" and "ash-s-cards-pricecharting-" in pc.headers["Content-Disposition"]
    first, row = pc.data.decode().splitlines()[:2]
    assert first.startswith("id,product-name,console-name,price-in-pennies") and row.startswith("111,Pikachu ex #57,Pokemon Surging Sparks,450")
    assert client.get(BASE + "/export.zip").status_code == 404


def test_import_route_reports_success_and_failure(client, login, stub):
    login()
    stub["role"] = "owner"
    assert b"Choose a backup file" in client.post(BASE + "/import", follow_redirects=True).data
    bad = client.post(BASE + "/import", data={"backup": (io.BytesIO(b"nope"), "x.json")},
                      content_type="multipart/form-data", follow_redirects=True)
    assert b"nothing was changed" in bad.data
    with patch("app.routes.maintenance.importer.restore",
               return_value=importer.Result(created=2, merged=1, skipped=1, copies=5, cards_fetched=3)) as restore:
        ok = client.post(BASE + "/import", data={"backup": (io.BytesIO(b"{}"), "x.json"), "skip_duplicates": "1"},
                         content_type="multipart/form-data", follow_redirects=True)
    text = ok.data.decode()
    assert "Imported 5 cards" in text and "2 new entries" in text and "skipped 1" in text and "3 cards on TCGdex" in text
    assert restore.call_args.kwargs["skip_duplicates"] is True


def test_only_the_import_may_exceed_the_normal_upload_limit(client, login, stub, app):
    login()
    stub["role"] = "owner"
    big = app.config["MAX_UPLOAD_BYTES"] + 10
    assert client.post("/c/ash-s-cards/add/identify", data=b"x",
                       environ_overrides={"CONTENT_LENGTH": str(big)}).status_code == 413
    with patch("app.routes.maintenance.importer.restore", return_value=importer.Result()):
        resp = client.post(BASE + "/import", data={"backup": (io.BytesIO(b"{}"), "x.json")},
                           content_type="multipart/form-data")
    assert resp.status_code == 302


def test_price_section_and_refresh_all_button(client, login, stub):
    login()
    stub["role"] = "owner"
    html = client.get(BASE + "/").data.decode()
    assert "Refresh all prices" in html and "3 of 4" in html and "TCGdex" in html
    resp = client.post(BASE + "/refresh-prices")
    assert resp.status_code == 302 and stub["bulk_calls"] == [(10, ["sv8-057"])]
    stub["role"] = "editor"
    assert client.post(BASE + "/refresh-prices").status_code == 403


def test_price_section_reports_the_last_run(client, login, stub):
    login()
    stub["role"] = "owner"
    stub["bulk"] = {"status": "done", "done": 4, "total": 4, "priced": 3, "unpriced": 1, "fallback": 1, "failed": 0,
                    "note": "Only the first 15 cards were tried on PokeWallet."}
    html = client.get(BASE + "/").data.decode()
    assert "Last run: 3 priced" in html and "1 via PokeWallet" in html and "Only the first 15" in html
    stub["bulk"]["status"] = "running"
    assert "Refreshing now: 4 of 4" in client.get(BASE + "/").data.decode()


def test_pricecharting_product_list_upload_and_status(client, login, stub):
    login()
    stub["role"] = "owner"
    assert b"Load a PriceCharting product list below first" in client.get(BASE + "/").data
    resp = client.post(BASE + "/pricecharting-products", data={"products": (io.BytesIO(
        b"id,console-name,product-name\n111,Pokemon Surging Sparks,Pikachu ex #57\n5,Game Boy,Tetris\n"), "guide.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Loaded 1 PriceCharting Pokemon products (skipped 1 other rows)" in resp.data
    assert stub["pc_products"] == [(111, "Pokemon Surging Sparks", "Pikachu ex #57")]
    assert b"1 of 1 entries match a PriceCharting product" in client.get(BASE + "/").data
    bad = client.post(BASE + "/pricecharting-products", data={"products": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")},
                      content_type="multipart/form-data", follow_redirects=True)
    assert b"look like a PriceCharting file" in bad.data
    assert client.post(BASE + "/pricecharting-products").status_code == 302                  # no file chosen
    unmatched = client.get(BASE + "/export-pricecharting-unmatched.csv")
    assert unmatched.data.decode().splitlines() == ["id,product-name,console-name,quantity,variant,card_id"]   # all matched
