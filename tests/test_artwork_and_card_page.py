"""Add from the card page, per-expansion value, and artwork from other sources or by hand."""
import io
from unittest.mock import patch

import pytest
from PIL import Image

from app import catalog_service as svc, images
from tests.conftest import CARD

BASE = "/c/ash-s-cards"


def png(size=(300, 420), color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


# ---- card page add stays on the card page
def test_adding_from_the_card_page_returns_to_the_card(client, login, stub):
    login()
    stub["role"] = "editor"
    page = client.get(BASE + "/card/sv8-057").data.decode()
    assert 'name="back" value="card"' in page
    data = {"card_id": "sv8-057", "variant": "Holofoil", "condition": "Near Mint", "quantity": "1", "back": "card"}
    resp = client.post(BASE + "/add/confirm", data=data)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/card/sv8-057")
    assert stub["added"] == [("sv8-057", "Holofoil", "Near Mint", 1)]
    bad = client.post(BASE + "/add/confirm", data={**data, "quantity": "0"})
    assert bad.headers["Location"].endswith("/card/sv8-057")                    # errors come back here too
    plain = client.post(BASE + "/add/confirm", data={k: v for k, v in data.items() if k != "back"})
    assert plain.headers["Location"].endswith("/add")                             # the Add page flow is unchanged


# ---- per-expansion value
def test_expansion_list_shows_value_only_where_cards_are_owned(client, login):
    from unittest.mock import patch as p
    from tests.conftest import SET
    login()
    with p("app.catalog_repository.list_sets", return_value=[SET, {**SET, "id": 8, "name": "Obsidian Flames", "owned": 0}]):
        html = client.get(BASE + "/sets?mine=0").data.decode()
    assert html.count("Est. value") == 1 and "Est. value $9.00" in html


# ---- artwork sources
@pytest.fixture
def ctx(app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    with app.app_context():
        yield app


CARD_NO_IMAGE = {**CARD, "image_base": None}


def test_fallback_urls_fill_in_the_card(ctx):
    ctx.config["ART_FALLBACK_URLS"] = ["https://img.example/{set_id}/{number}.png", "https://other.example/{local_id}"]
    card = {"id": "sv8-057", "local_id": "057", "image_base": None}
    assert svc.fallback_urls(card) == ["https://img.example/sv8/57.png", "https://other.example/057"]
    ctx.config["ART_FALLBACK_URLS"] = ["https://x.example/{bogus}"]
    assert svc.fallback_urls(card) == []


def test_missing_tcgdex_image_uses_the_fallback_and_stores_both_sizes(ctx):
    with patch.object(images, "download_image", return_value=png()) as dl:
        data = svc.fetch_art_for(CARD_NO_IMAGE, "low")
    assert data and data[:4] == b"RIFF" and dl.call_count == 1
    assert svc.cached_art("sv8-057", "high")
    with patch.object(images, "download_image") as dl:                 # second view: from disk, no request
        assert svc.fetch_art_for(CARD_NO_IMAGE, "low") == data
    dl.assert_not_called()


def test_tcgdex_failure_also_falls_back(ctx):
    from app.tcgdex import TCGdexError
    with patch.object(svc, "fetch_art", side_effect=TCGdexError("down")), \
         patch.object(images, "download_image", return_value=png()):
        assert svc.fetch_art_for(CARD, "low")


def test_nothing_found_is_remembered_so_pages_dont_keep_asking(ctx):
    with patch.object(images, "download_image", side_effect=images.ImageError("nope")) as dl:
        assert svc.fetch_art_for(CARD_NO_IMAGE) is None
        assert svc.fetch_art_for(CARD_NO_IMAGE) is None
    assert dl.call_count == 1 and svc.recently_missed("sv8-057")
    ctx.config["ART_RETRY_MINUTES"] = 0
    assert not svc.recently_missed("sv8-057")


def test_turning_the_fallback_off(ctx):
    ctx.config["ART_FALLBACK_URLS"] = []
    with patch.object(images, "download_image") as dl:
        assert svc.fetch_art_for(CARD_NO_IMAGE) is None
    dl.assert_not_called()


def test_download_image_only_talks_to_public_hosts(ctx):
    for url in ("http://127.0.0.1/x.png", "http://localhost/x", "file:///etc/passwd", "ftp://example.com/x",
                "http://192.168.1.5/card.png", "http://169.254.169.254/latest"):
        with pytest.raises(images.ImageError):
            images.download_image(url)


def test_download_image_follows_redirects_safely(ctx):
    class Resp:
        def __init__(self, status, headers=None, body=b""):
            self.status_code, self.headers, self.is_redirect = status, headers or {}, status in (301, 302)
            stream = io.BytesIO(body)
            self.raw = type("Raw", (), {"read": lambda _s, n, decode_content=False: stream.read(n)})()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    seq = [Resp(302, {"Location": "https://cdn.example/final.png"}), Resp(200, body=b"IMG")]
    with patch.object(images, "_public_host", return_value=True), patch.object(images.requests, "get", side_effect=seq):
        assert images.download_image("https://example.com/a") == b"IMG"
    hosts = iter([True, False])                                         # redirect to a private address is refused
    with patch.object(images, "_public_host", side_effect=lambda h: next(hosts)), \
         patch.object(images.requests, "get", return_value=Resp(302, {"Location": "http://10.0.0.1/x"})):
        with pytest.raises(images.ImageError):
            images.download_image("https://example.com/a")


# ---- manual artwork
def upload(data=None, name="art.png"):
    return {"image": (io.BytesIO(data or png()), name)}


def test_upload_artwork_replaces_the_picture(client, login, stub, app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    login()
    assert client.post(BASE + "/card/sv8-057/artwork", data=upload(), content_type="multipart/form-data").status_code == 403
    stub["role"] = "editor"
    resp = client.post(BASE + "/card/sv8-057/artwork", data=upload(), content_type="multipart/form-data")
    assert resp.status_code == 302
    with app.app_context():
        low, high = svc.cached_art("sv8-057", "low"), svc.cached_art("sv8-057", "high")
    assert Image.open(io.BytesIO(low)).width == 245 and Image.open(io.BytesIO(high)).width == 300   # never enlarged


def test_artwork_from_a_link_and_bad_input(client, login, stub, app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    login()
    stub["role"] = "editor"
    with patch.object(images, "download_image", return_value=png((900, 1260))):
        client.post(BASE + "/card/sv8-057/artwork", data={"url": "https://example.com/a.png"})
    with app.app_context():
        assert Image.open(io.BytesIO(svc.cached_art("sv8-057", "high"))).width == 600
    client.get(BASE + "/card/sv8-057")                                  # shows (and clears) the success message
    for data, kind in (({}, None), ({"url": "http://127.0.0.1/x"}, None)):
        resp = client.post(BASE + "/card/sv8-057/artwork", data=data, follow_redirects=True)
        assert b"Artwork saved" not in resp.data
    resp = client.post(BASE + "/card/sv8-057/artwork", data=upload(b"not an image"), content_type="multipart/form-data",
                       follow_redirects=True)
    assert b"couldn" in resp.data.lower()
    assert client.post(BASE + "/card/nope/artwork", data={"url": "x"}).status_code == 404


def test_reset_artwork_looks_again(client, login, stub, app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    login()
    stub["role"] = "editor"
    with app.app_context():
        svc.store_art("sv8-057", images.art_from_bytes(png()))
    with patch("app.catalog_service.fetch_art_for", return_value=None):
        html = client.post(BASE + "/card/sv8-057/artwork/reset", follow_redirects=True).data.decode()
    assert "No artwork was found" in html
    with app.app_context():
        assert svc.cached_art("sv8-057", "low") is None


def test_card_page_offers_artwork_tools_to_editors(client, login, stub):
    login()
    assert b"artwork-tools" not in client.get(BASE + "/card/sv8-057").data
    stub["role"] = "editor"
    html = client.get(BASE + "/card/sv8-057").data.decode()
    assert "Artwork wrong or missing?" in html and "Download again" in html
