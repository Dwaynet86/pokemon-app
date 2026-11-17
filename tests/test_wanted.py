"""Wanted list: marking cards, the members' page, the public page (no login, no links) and sharing."""
import re
from unittest.mock import MagicMock, patch

BASE = "/c/ash-s-cards"
FETCH = {"X-Requested-With": "fetch"}


def test_star_toggles_wanted_for_editors_only(client, login, stub):
    login()
    assert client.post(BASE + "/cards/sv8-057/wanted", headers=FETCH).status_code == 403
    stub["role"] = "editor"
    data = client.post(BASE + "/cards/sv8-057/wanted", headers=FETCH).get_json()
    assert data["wanted"] is True and data["name"] == "Pikachu ex" and stub["wanted"] == ["sv8-057"]
    assert client.post(BASE + "/cards/sv8-057/wanted", headers=FETCH).get_json()["wanted"] is False
    assert stub["wanted"] == []
    assert client.post(BASE + "/cards/nope/wanted", headers=FETCH).status_code == 404


def test_form_fallback_redirects_back(client, login, stub):
    login()
    stub["role"] = "editor"
    resp = client.post(BASE + "/cards/sv8-057/wanted")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/card/sv8-057")
    resp = client.post(BASE + "/cards/sv8-057/wanted", data={"back": "wanted"})
    assert resp.headers["Location"].endswith("/wanted")


def test_expansion_page_shows_wanted_state(client, login, stub):
    login()
    stub["role"] = "editor"
    stub["wanted"] = ["sv8-058"]
    html = client.get(BASE + "/sets/7").data.decode()
    assert 'want-btn on' in html and 'aria-pressed="true"' in html
    stub["role"] = "viewer"
    assert "want-badge" in client.get(BASE + "/sets/7").data.decode()          # viewers see a star, no button


def test_card_page_links_back_to_gallery_and_expansion_and_offers_wanted(client, login, stub):
    login()
    html = client.get(BASE + "/card/sv8-057").data.decode()
    assert '← Gallery' in html and '/c/ash-s-cards/sets/7">← Surging Sparks' in html
    assert "Add to wanted list" not in html                                     # viewers can't
    stub["role"] = "editor"
    stub["wanted"] = []
    from unittest.mock import patch as p
    from tests.conftest import CARD
    with p("app.owned_repository.copies_of", return_value=[]):
        assert "Add to wanted list" in client.get(BASE + "/card/sv8-057").data.decode()
        stub["wanted"] = ["sv8-057"]
        assert "On your wanted list" in client.get(BASE + "/card/sv8-057").data.decode()


def test_members_wanted_page(client, login, stub):
    login()
    html = client.get(BASE + "/wanted").data.decode()
    assert "Nothing on the wanted list yet" in html and "Wanted</a>" in html
    stub["wanted"] = ["sv8-057"]
    html = client.get(BASE + "/wanted").data.decode()
    assert "Pikachu ex" in html and "from $4.50" in html and "/wanted/ash-s-cards" in html
    assert "Make private" not in html                                            # owners only
    stub["role"] = "owner"
    assert "Make private" in client.get(BASE + "/wanted").data.decode()


def test_only_owners_change_sharing(client, login, stub):
    login()
    stub["role"] = "editor"
    assert client.post(BASE + "/wanted/sharing", data={"public": "0"}).status_code == 403
    stub["role"] = "owner"
    client.post(BASE + "/wanted/sharing", data={"public": "0"})
    assert stub["public"] is False


def test_public_page_needs_no_login_and_has_no_links(client, stub):
    stub["wanted"] = ["sv8-057"]
    resp = client.get("/wanted/ash-s-cards")                                      # not logged in
    html = resp.data.decode()
    assert resp.status_code == 200 and "Pikachu ex" in html and "57/191" in html and "from $4.50" in html
    assert "<a " not in html and "href=" not in re.sub(r'<link[^>]*>', "", html)  # nothing to click into the app
    assert "/wanted/ash-s-cards/art/sv8-057" in html and 'name="csrf-token"' not in html
    assert "noindex" in html


def test_public_art_only_for_wanted_cards_of_public_lists(client, stub):
    stub["wanted"] = ["sv8-057"]
    with patch("app.routes.wanted_public.art_response", return_value=MagicMock(status_code=200)) as art:
        assert client.get("/wanted/ash-s-cards/art/sv8-057").status_code == 200
        assert art.called
        assert client.get("/wanted/ash-s-cards/art/sv8-058").status_code == 404      # not on the list
    stub["public"] = False
    assert client.get("/wanted/ash-s-cards/art/sv8-057").status_code == 404
    assert client.get("/wanted/ash-s-cards").status_code == 404                       # private: looks like it doesn't exist
    assert client.get("/wanted/nobody").status_code == 404


def test_signed_in_member_goes_to_the_full_page_and_others_get_the_normal_layout(client, login, stub):
    login()
    resp = client.get("/wanted/ash-s-cards")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/c/ash-s-cards/wanted")
    stub["role"] = None                                                              # signed in, but not a member
    html = client.get("/wanted/ash-s-cards").data.decode()
    assert "Pikachu" in html or "wanted cards" in html
    assert 'class="topbar"' in html and "Log out" in html


def test_other_pages_still_require_login(client):
    for path in ("/art/sv8-057", "/c/ash-s-cards/wanted", "/c/ash-s-cards/sets/7"):
        assert client.get(path).status_code == 302


def test_adding_a_copy_clears_it_from_the_wanted_list():
    from app import owned_repository as owned

    class Conn:
        def __init__(self): self.sql = []
        def execute(self, sql, params=None):
            self.sql.append(" ".join(sql.split()))
            return MagicMock(fetchone=lambda: {"quantity": 1, "inserted": True})
    conn = Conn()
    owned.add_copy(10, "sv8-057", "Holofoil", "Near Mint", 1, None, conn=conn)
    assert any(q.startswith("DELETE FROM wanted_cards") for q in conn.sql)
