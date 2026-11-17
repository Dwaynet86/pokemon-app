"""Finish pills, master-set counting, the finish filter and the per-expansion wanted buttons."""
from app import finishes

BASE = "/c/ash-s-cards"
FETCH = {"X-Requested-With": "fetch"}


def card(variants, owned, qty=None):
    return {"variants": variants, "owned_variants": owned, "qty": qty if qty is not None else len(owned)}


# ---- pure rules
def test_progress_counts_a_card_once_or_every_finish():
    c = card(["Normal", "Holofoil", "Reverse Holofoil"], ["Holofoil"])
    assert finishes.progress(c, master=False) == (1, 1)
    assert finishes.progress(c, master=True) == (1, 3)
    assert finishes.progress(card(["Normal"], []), master=True) == (0, 1)


def test_cards_with_unknown_finishes_count_once_even_in_master_mode():
    assert finishes.progress(card([], ["Normal"]), master=True) == (1, 1)
    assert finishes.progress(card([], []), master=True) == (0, 1)


def test_finish_owned_outside_the_published_list_is_not_counted():
    assert finishes.progress(card(["Holofoil"], ["Normal"]), master=True) == (0, 1)


def test_totals_and_options_and_lacks():
    cards = [card(["Normal", "Reverse Holofoil"], ["Normal"]), card(["Holofoil"], []), card(["Reverse Holofoil", "Weird"], [])]
    assert finishes.totals(cards, False) == (1, 3) and finishes.totals(cards, True) == (1, 5)
    assert finishes.options(cards) == ["Normal", "Holofoil", "Reverse Holofoil", "Weird"]
    assert finishes.lacks(cards[0], "Reverse Holofoil") and not finishes.lacks(cards[0], "Normal")
    assert not finishes.lacks(cards[1], "Normal")                  # the card doesn't come in that finish


# ---- master-set switch
def test_master_set_is_editor_only_and_redirects_safely(client, login, stub):
    login()
    assert client.post(BASE + "/master-set", data={"master": ["0", "1"]}).status_code == 403
    stub["role"] = "editor"
    resp = client.post(BASE + "/master-set", data={"master": ["0", "1"], "back": "/c/ash-s-cards/sets/7"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/c/ash-s-cards/sets/7") and stub["master"] == [True]
    resp = client.post(BASE + "/master-set", data={"master": "0", "back": "//evil.example"})
    assert "evil.example" not in resp.headers["Location"] and stub["master"] == [True, False]


def test_expansion_page_counts_finishes_in_master_mode(client, login, stub, monkeypatch):
    login()
    html = client.get(BASE + "/sets/7").data.decode()
    assert "1</strong> of 2" in html and "finishes" not in html.split("collected")[0][-40:]
    from app.routes import cards as routes
    monkeypatch.setattr(routes, "_master", lambda: True)
    html = client.get(BASE + "/sets/7").data.decode()
    assert "of 4 finishes collected" in html and 'data-master="1"' in html        # 2 cards x (Holofoil, Reverse Holofoil)


# ---- finish filter
def test_finish_filter_lists_cards_missing_that_finish(client, login):
    login()
    html = client.get(BASE + "/sets/7?lacking=Holofoil").data.decode()
    assert "Missing Reverse Holofoil" in html                                      # option present
    assert "Pikachu ex" in html and "Raichu" in html                                # Raichu owns nothing; Pikachu ex lacks Holofoil
    html = client.get(BASE + "/sets/7?lacking=Reverse+Holofoil").data.decode()
    assert "Raichu" in html and 'title="Pikachu ex"' not in html                    # Pikachu ex already owns the reverse holo
    assert "Pikachu ex" not in html.split('<ul class="grid">')[1]
    assert "lacking=Reverse" in html                                               # tabs keep the filter
    assert "Raichu" in client.get(BASE + "/sets/7?lacking=bogus").data.decode()   # unknown finish is ignored


# ---- clickable pills and quick-add with a finish
def test_pills_are_buttons_only_for_editors(client, login, stub):
    login()
    assert "data-add-finish" not in client.get(BASE + "/sets/7").data.decode()
    stub["role"] = "editor"
    html = client.get(BASE + "/sets/7").data.decode()
    assert 'data-add-finish="/c/ash-s-cards/cards/sv8-057/quick-add" data-variant="Holofoil"' in html
    assert 'data-variant="Reverse Holofoil"' not in html.split("sv8-058")[0]       # that one is owned


def test_quick_add_takes_a_specific_finish(client, login, stub):
    login()
    stub["role"] = "editor"
    data = client.post(BASE + "/cards/sv8-057/quick-add?variant=Reverse+Holofoil", headers=FETCH).get_json()
    assert data["variant"] == "Reverse Holofoil" and stub["added"][-1][:3] == ("sv8-057", "Reverse Holofoil", "Near Mint")
    assert client.post(BASE + "/cards/sv8-057/quick-add?variant=Nonsense", headers=FETCH).status_code == 400


# ---- bulk wanted
def test_add_all_missing_to_wanted(client, login, stub):
    login()
    assert client.post(BASE + "/sets/7/wanted-missing").status_code == 403
    stub["role"] = "editor"
    resp = client.post(BASE + "/sets/7/wanted-missing", data={"show": "missing"})
    assert resp.status_code == 302 and "show=missing" in resp.headers["Location"] and stub["bulk_wanted"] == [7]
    with client.session_transaction() as s:
        assert any("Added 3 missing cards" in m for _, m in s["_flashes"])
    client.post(BASE + "/sets/7/wanted-missing", data={"action": "clear"})
    assert stub["cleared"] == [7]
    assert client.post(BASE + "/sets/99/wanted-missing").status_code == 404


def test_expansion_page_offers_the_bulk_button_to_editors(client, login, stub):
    login()
    assert "missing card" not in client.get(BASE + "/sets/7").data.decode().split("Missing</a>")[0]
    stub["role"] = "editor"
    html = client.get(BASE + "/sets/7").data.decode()
    assert "Add all 1 missing card to the wanted list" in html
    stub["wanted"] = ["sv8-058"]
    html = client.get(BASE + "/sets/7").data.decode()
    assert "All 1 missing cards are on your wanted list" in html and "Clear this expansion" in html


def test_navigation_says_pokedex(client, login):
    login()
    html = client.get(BASE + "/").data.decode()
    assert ">PokeDex</a>" in html and ">Pokémon</a>" not in html


def test_card_page_links_to_previous_and_next_card(client, login):
    login()
    html = client.get(BASE + "/card/sv8-057").data.decode()
    assert 'rel="prev"' in html and "/card/sv8-056" in html and "Pichu" in html
    assert 'rel="next"' not in html                                  # last card in the expansion: no next link


def test_mobile_hooks_are_present(client, login, stub):
    """Phone layout relies on these: members/maintenance in the account menu, the prev/next bar, an icon-only theme button."""
    login()
    stub["role"] = "owner"
    html = client.get(BASE + "/card/sv8-057").data.decode()
    assert 'class="menu-mobile"' in html and 'class="card-nav"' in html and 'class="theme-word"' in html
    css = open("app/static/css/style.css").read()
    assert "repeat(2, minmax(0, 1fr))" in css and ".card-nav { position: fixed" in css
