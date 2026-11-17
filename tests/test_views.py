"""Every page renders for the right role, and access rules hold."""
import io

import pytest
from PIL import Image

BASE = "/c/ash-s-cards"


@pytest.mark.parametrize("path", ["/", "/sets", "/sets/7", "/pokemon", "/pokemon/25", "/card/sv8-057"])
def test_pages_render_for_viewer(client, login, path):
    login()
    resp = client.get(BASE + path)
    assert resp.status_code == 200
    assert b"Pikachu" in resp.data or b"Surging Sparks" in resp.data


def test_login_required(client):
    assert client.get(BASE + "/").status_code == 302
    assert client.get("/art/sv8-057").status_code == 302


def test_non_member_gets_404(client, login, stub):
    login()
    stub["role"] = None
    assert client.get(BASE + "/").status_code == 404


def test_viewer_cannot_add(client, login):
    login()
    assert client.get(BASE + "/add").status_code == 403
    assert client.post(BASE + "/add/confirm", data={"card_id": "sv8-057"}).status_code == 403
    assert b"Add card" not in client.get(BASE + "/").data


def test_editor_sees_add(client, login, stub):
    login()
    stub["role"] = "editor"
    assert b"Add card" in client.get(BASE + "/").data
    assert client.get(BASE + "/add").status_code == 200


def test_gallery_shows_quantity_and_variant(client, login):
    login()
    html = client.get(BASE + "/").data.decode()
    assert "×2" in html and "Holofoil" in html and "Near Mint" in html


def test_set_view_marks_owned_and_missing(client, login):
    login()
    html = client.get(BASE + "/sets/7").data.decode()
    assert "tile owned" in html and "tile missing" in html
    assert "1</strong> of 2 collected" in html or "of 2 collected" in html


def test_pokemon_view_marks_owned_and_missing(client, login):
    login()
    html = client.get(BASE + "/pokemon").data.decode()
    assert "dex-tile owned" in html and "dex-tile missing" in html


def test_confirm_adds_copy_and_starts_expansion_download_and_price(client, login, stub):
    login()
    stub["role"] = "editor"
    resp = client.post(BASE + "/add/confirm", data={
        "card_id": "sv8-057", "variant": "Holofoil", "condition": "Near Mint", "quantity": "3"})
    assert resp.status_code == 302
    assert stub["added"] == [("sv8-057", "Holofoil", "Near Mint", 3)]
    assert stub["sync"] == [(7, False)] and stub["priced"] == ["sv8-057"]


def test_set_status_and_download(client, login, stub):
    login()
    assert client.get(BASE + "/sets/7/status").get_json()["status"] == "idle"
    assert client.post(BASE + "/sets/7/download").status_code == 403       # viewers can't start downloads
    stub["role"] = "editor"
    assert client.post(BASE + "/sets/7/download", data={"force": "1"}).status_code == 302
    assert stub["sync"] == [(7, True)]


def test_running_download_shows_progress_banner(client, login, stub):
    from unittest.mock import patch
    from tests.conftest import SET
    login()
    with patch("app.catalog_repository.get_set", return_value={**SET, "sync_status": "running"}):
        assert b"data-sync-url" in client.get(BASE + "/sets/7").data


def test_manual_search_lists_candidates(client, login, stub):
    login()
    stub["role"] = "editor"
    html = client.get(BASE + "/add/search?q=Pikachu ex 57").data.decode()
    assert "Pikachu ex" in html and "57/191" in html and 'name="card_id"' in html


@pytest.mark.parametrize("data", [
    {"card_id": "nope", "variant": "Normal", "condition": "Near Mint", "quantity": "1"},
    {"card_id": "sv8-057", "variant": "", "condition": "Near Mint", "quantity": "1"},
    {"card_id": "sv8-057", "variant": "Normal", "condition": "Pristine", "quantity": "1"},
    {"card_id": "sv8-057", "variant": "Normal", "condition": "Near Mint", "quantity": "0"},
    {"card_id": "sv8-057", "variant": "Normal", "condition": "Near Mint", "quantity": "1000"},
])
def test_confirm_rejects_bad_input(client, login, stub, data):
    login()
    stub["role"] = "editor"
    client.post(BASE + "/add/confirm", data=data)
    assert stub["added"] == []


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 56), "red").save(buf, "PNG")
    buf.seek(0)
    return buf


def _identify(client):
    return client.post(BASE + "/add/identify", data={"photo": (_png(), "card.png")},
                       content_type="multipart/form-data")


def test_identify_lists_candidates_with_scan_summary(client, login, stub):
    login()
    stub["role"] = "editor"
    html = _identify(client).data.decode()
    assert "Scan matched" in html and "Pikachu ex" in html and "91% match" in html
    assert 'name="card_id"' in html and "57/191" in html and "photo 91%" in html
    assert 'data-default="Holofoil"' in html          # this card has no Normal finish
    assert "edges weren" not in html                  # crop was found: no warning


def test_identify_warns_when_card_edges_not_found(client, login, stub):
    from dataclasses import replace
    login()
    stub["role"] = "editor"
    stub["scan"] = replace(stub["scan"], crop_found=False)
    assert "edges weren" in _identify(client).data.decode()


def test_identify_scan_failure_keeps_photo_and_offers_search(client, login, stub):
    from app.photoid import PhotoResult
    login()
    stub["role"] = "editor"
    stub["scan"] = PhotoResult(error="Couldn't reach the scan service.")
    html = _identify(client).data.decode()
    assert "Couldn" in html and "search by name" in html and "Your photo" in html
    assert 'name="card_id"' not in html


def test_identify_no_match(client, login, stub):
    from app.photoid import PhotoResult
    login()
    stub["role"] = "editor"
    stub["scan"] = PhotoResult()
    html = _identify(client).data.decode()
    assert "No match found" in html and "Try searching by name" in html


def test_add_page_needs_no_api_key_and_explains_the_scan(client, login, stub):
    login()
    stub["role"] = "editor"
    html = client.get(BASE + "/add").data.decode()
    assert "ANTHROPIC" not in html and "TCG Tracking" in html


def test_pending_photo_is_private_to_uploader(client, login, stub, app):
    from app import images
    login()
    stub["role"] = "editor"
    client.post(BASE + "/add/identify", data={"photo": (_png(), "card.png")},
                content_type="multipart/form-data")
    with app.test_request_context():
        name = next((app.config["UPLOAD_DIR"] / "pending").glob("*.webp")).name
        mine = images.make_token(1, name)
        theirs = images.make_token(2, name)
    assert client.get(f"{BASE}/add/photo/{mine}").status_code == 200
    assert client.get(f"{BASE}/add/photo/{theirs}").status_code == 404
    assert client.get(f"{BASE}/add/photo/garbage").status_code == 404


def test_art_serves_cache_downloads_once_and_falls_back(client, login):
    from unittest.mock import patch
    from app.tcgdex import TCGdexError
    login()
    assert client.get("/art/unknown").status_code == 404
    assert client.get("/art/sv8-057?size=huge").status_code == 400
    with patch("app.catalog_service.cached_art", return_value=None), \
         patch("app.catalog_service.fetch_art", return_value=b"RIFFxxxxWEBP") as fetch:
        resp = client.get("/art/sv8-057")
        assert resp.mimetype == "image/webp" and resp.data == b"RIFFxxxxWEBP"
        fetch.assert_called_once_with("sv8-057", "https://assets.tcgdex.net/en/sv/sv8/57", "low")
        assert client.get("/art/sv8-057?cache=only").mimetype == "image/svg+xml"   # never hits the network
        fetch.side_effect = TCGdexError("down")
        assert client.get("/art/sv8-057").mimetype == "image/svg+xml"                # placeholder, not a 500


def test_own_photos_are_not_served_or_shown(client, login):
    login()
    assert client.get(BASE + "/media/" + "a" * 32 + ".webp").status_code == 404
    assert client.get(BASE + "/thumb/" + "a" * 32 + ".webp").status_code == 404
    assert b"Your photos" not in client.get(BASE + "/card/sv8-057").data


def test_expansions_default_to_my_expansions(client, login):
    from unittest.mock import patch
    from tests.conftest import SET
    login()
    with patch("app.catalog_repository.list_sets", return_value=[SET]) as listing:
        html = client.get(BASE + "/sets").data.decode()
        assert listing.call_args.args[3] is True                       # mine_only on a fresh visit
        assert 'name="mine" value="1" checked' in html and 'name="mine" value="0"' in html
        client.get(BASE + "/sets?mine=0&mine=1")                       # ticked: hidden 0 then checkbox 1
        assert listing.call_args.args[3] is True
        html = client.get(BASE + "/sets?mine=0").data.decode()         # unticked: only the hidden 0
        assert listing.call_args.args[3] is False and "checked" not in html.split('name="mine" value="1"')[1][:20]


def test_expansions_fall_back_to_all_until_something_is_collected(client, login):
    from unittest.mock import patch
    from tests.conftest import SET
    login()
    calls = []

    def listing(cid, q, series, mine, master=False):
        calls.append(mine)
        return [] if mine else [SET]

    with patch("app.catalog_repository.list_sets", side_effect=listing):
        html = client.get(BASE + "/sets").data.decode()
        assert calls == [True, False] and "haven't added any cards yet" in html and "Surging Sparks" in html
        calls.clear()
        html = client.get(BASE + "/sets?mine=1").data.decode()          # explicit choice: respected, no fallback
        assert calls == [True] and "No expansions yet" in html and "Browse all expansions" in html


def test_confirming_deletes_the_photo_and_stores_nothing(client, login, stub, app):
    from app import images
    login()
    stub["role"] = "editor"
    _identify(client)
    pending = list((app.config["UPLOAD_DIR"] / "pending").glob("*.webp"))
    assert len(pending) == 1
    with app.test_request_context():
        token = images.make_token(1, pending[0].name)
    client.post(BASE + "/add/confirm", data={"card_id": "sv8-057", "variant": "Holofoil", "condition": "Near Mint",
                                             "quantity": "1", "token": token})
    assert stub["added"] and not list(app.config["UPLOAD_DIR"].rglob("*.webp"))


def test_discarding_deletes_the_photo(client, login, stub, app):
    from app import images
    login()
    stub["role"] = "editor"
    _identify(client)
    (path,) = (app.config["UPLOAD_DIR"] / "pending").glob("*.webp")
    with app.test_request_context():
        token = images.make_token(1, path.name)
    client.post(BASE + "/add/cancel", data={"token": token})
    assert not path.exists()


def test_grey_cards_open_the_card_and_only_the_buttons_add(client, login, stub):
    login()
    viewer = client.get(BASE + "/sets/7").data.decode()
    assert "data-quick-add" not in viewer and "data-want" not in viewer             # viewers get no buttons
    stub["role"] = "editor"
    html = client.get(BASE + "/sets/7").data.decode()
    assert html.count("data-quick-add") == 1 and html.count("data-want=") == 1      # only the grey card, not the owned one
    assert "/cards/sv8-058/quick-add" in html and "/cards/sv8-058/wanted" in html and "data-collected" in html
    grey = html.split('class="tile missing"')[1].split("</div>")[0]
    assert 'class="card-frame" href="/c/ash-s-cards/card/sv8-058"' in grey          # the picture is a link, not a button
    assert "<button" in grey and 'class="card-frame"' in grey and "quick-btn" not in html


def test_quick_add_adds_one_default_copy(client, login, stub):
    login()
    assert client.post(BASE + "/cards/sv8-057/quick-add").status_code == 403
    stub["role"] = "editor"
    resp = client.post(BASE + "/cards/sv8-057/quick-add")
    assert resp.status_code == 200
    # Pikachu ex has no Normal finish in the fixture, so the default is Holofoil
    assert stub["added"] == [("sv8-057", "Holofoil", "Near Mint", 1)]
    assert resp.get_json()["ok"] and resp.get_json()["variant"] == "Holofoil" and resp.get_json()["total"] == 2
    assert stub["sync"] == [(7, False)] and stub["priced"] == ["sv8-057"]
    assert client.post(BASE + "/cards/nope/quick-add").status_code == 404


def test_card_without_artwork_gets_a_named_placeholder(client, login):
    from unittest.mock import patch
    from tests.conftest import CARD
    login()
    card = {**CARD, "image_base": None, "name": "Pikachu <ex> & friends"}
    with patch("app.catalog_repository.get_card", return_value=card), \
         patch("app.catalog_service.cached_art", return_value=None), \
         patch("app.catalog_service.fetch_art_for", return_value=None):
        resp = client.get("/art/sv8-057")
    assert resp.mimetype == "image/svg+xml" and b"Pikachu &lt;ex&gt;" in resp.data and b"57/191" in resp.data


def test_base_page_has_logo_and_background_ball(client, login):
    login()
    html = client.get(BASE + "/").data.decode()
    assert "img/logo.png" in html and 'class="brand-logo"' in html and "onerror" in html
    for path in ("/static/img/logo.png", "/static/img/pokeball.svg"):
        assert client.get(path).status_code == 200
    assert "pokeball.svg" in client.get("/static/css/style.css").data.decode()


def test_gallery_row_shows_finish_badge(client, login):
    login()
    html = client.get(BASE + "/").data.decode()
    assert 'class="finish has"' in html and 'title="Holofoil: you own this finish"' in html


def test_set_tile_shows_owned_and_unowned_finishes(client, login):
    login()
    html = client.get(BASE + "/sets/7").data.decode()
    assert "finish-legend" in html
    assert "Reverse Holofoil: you own this finish" in html and "Holofoil: not owned yet" in html
