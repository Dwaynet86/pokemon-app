"""Overview page: the ranked panels, the landing redirects, and the empty state."""
from decimal import Decimal

from app import overview_repository as ov

BASE = "/c/ash-s-cards"


def row(sid, name, owned, total, value=0):
    return {"id": sid, "name": name, "owned": owned, "total": total, "value": Decimal(value)}


def test_closest_to_complete_ranks_by_percentage_then_cards_missing():
    sets = [row(1, "Big", 180, 200), row(2, "Small", 9, 10), row(3, "Tiny start", 2, 2 + 1),
            row(4, "Done", 50, 50), row(5, "Barely", 1, 1), row(6, "Half", 50, 100), row(7, "Unknown size", 10, 0)]
    out = ov.shape({"copies": 1, "unique_cards": 1, "sets": 7, "value": 0, "priced": 0}, [], sets, [], [], {})
    assert [s["name"] for s in out["closest"]] == ["Small", "Big", "Half"]          # complete, <3 cards and no-total sets excluded
    many = [row(i, f"S{i}", 90 + i, 100) for i in range(8)]
    assert len(ov.shape({}, [], many, [], [], {})["closest"]) == 5                    # five, like the other panels
    assert out["closest"][0]["missing"] == 1 and out["closest"][1]["pct"] == 90
    assert out["head"]["completed"] == 2                                              # Done and Barely (1 of 1)


def test_most_valuable_expansions_skip_unpriced_and_cap_at_five():
    sets = [row(i, f"S{i}", 5, 10, i) for i in range(0, 8)]
    out = ov.shape({}, [], sets, [], [], {})
    assert [s["name"] for s in out["top_sets"]] == ["S7", "S6", "S5", "S4", "S3"]    # S0 has no value


def test_generation_progress_and_totals():
    dex = [{"generation": 1, "total": 151, "caught": 100}, {"generation": 2, "total": 100, "caught": 0}]
    out = ov.shape({}, [], [], [], dex, {})
    assert out["generations"][0] == {"n": 1, "name": "Kanto", "caught": 100, "total": 151, "pct": 66}
    assert out["head"]["species"] == 100 and out["head"]["species_total"] == 251


def test_overview_page_shows_every_panel(client, login):
    login()
    html = client.get(BASE + "/overview").data.decode()
    for text in ("Estimated value", "$9.00", "1 of 2 cards have a price", "Most valuable cards", "Pikachu ex", "×2",
                 "Expansions near completion", "11 cards to go", "180/191", "Most valuable expansions", "Pokémon by generation",
                 "Gen 1", "25/151", "Recently added", "Wanted list", "2 cards", "$5.50"):
        assert text in html, text
    assert 'aria-current="page">Overview' in html
    assert "show=missing" in html                                                    # links straight to the missing cards


def test_overview_empty_collection(client, login, stub):
    login()
    stub["overview"] = {**stub["overview"], "head": {**stub["overview"]["head"], "unique_cards": 0}}
    assert "Nothing in this collection yet" in client.get(BASE + "/overview").data.decode()
    assert "Add your first card" not in client.get(BASE + "/overview").data.decode()      # viewers can't add
    stub["role"] = "editor"
    assert "Add your first card" in client.get(BASE + "/overview").data.decode()


def test_everything_lands_on_the_overview(client, login):
    login()
    resp = client.get("/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/c/ash-s-cards/overview")
    html = client.get("/collections").data.decode()
    assert "/c/ash-s-cards/overview" in html
    assert "Overview</a>" in client.get(BASE + "/").data.decode()                           # tab on the other pages too


def test_overview_requires_membership(client, login, stub):
    assert client.get(BASE + "/overview").status_code == 302
    login()
    stub["role"] = None
    assert client.get(BASE + "/overview").status_code == 404
