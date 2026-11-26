"""Shared fixtures: an app with the database layer stubbed out."""
from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, identify
from app.photoid import PhotoResult

USER = {"id": 1, "username": "ash", "is_admin": False, "is_active": True,
        "password_hash": generate_password_hash("correct horse")}

CARD = {"id": "sv8-057", "set_id": 7, "local_id": "57", "name": "Pikachu ex", "clean_name": "Pikachu ex",
        "card_number": "57/191", "rarity": "Double Rare", "card_type": "Pokemon", "hp": "200",
        "stage": "Basic", "variants": ["Holofoil", "Reverse Holofoil"], "has_detail": True,
        "image_base": "https://assets.tcgdex.net/en/sv/sv8/57", "prices_updated_at": None,
        "prices": {"Holofoil": 4.5, "Reverse Holofoil": None}, "set_name": "Surging Sparks",
        "set_code": "SSP", "release_date": date(2024, 11, 8), "set_total": 252}

OWNED = {**CARD, "id": 11, "card_id": "sv8-057", "variant": "Holofoil", "condition": "Near Mint",
         "quantity": 2, "notes": None, "unit_price": Decimal("4.50"),
         "set_pk": 7}

SET = {"id": 7, "tcgdex_id": "sv8", "set_code": "SSP", "name": "Surging Sparks", "series": "Scarlet & Violet",
       "release_date": date(2024, 11, 8), "card_count": 252, "card_count_official": 191,
       "logo_url": None, "symbol_url": None, "cards_fetched_at": date(2025, 1, 1),
       "images_synced_at": None, "details_synced_at": None, "sync_status": "idle", "sync_done": 0,
       "sync_total": 0, "sync_message": None, "owned": 1, "total": 252, "value": Decimal("9.00")}


OVERVIEW = {
    "head": {"copies": 3, "unique_cards": 2, "sets": 1, "value": Decimal("9.00"), "priced": 1, "completed": 0,
             "species": 1, "species_total": 1025},
    "top_cards": [{"id": "sv8-057", "name": "Pikachu ex", "card_number": "57/191", "rarity": "Double Rare",
                   "set_name": "Surging Sparks", "copies": 2, "value": Decimal("9.00")}],
    "closest": [{"id": 7, "name": "Surging Sparks", "owned": 180, "total": 191, "missing": 11, "pct": 94, "value": Decimal("9")}],
    "top_sets": [{"id": 7, "name": "Surging Sparks", "owned": 1, "total": 252, "pct": 0, "value": Decimal("9.00")}],
    "recent": [{"id": "sv8-057", "name": "Pikachu ex", "card_number": "57/191", "set_name": "Surging Sparks"}],
    "generations": [{"n": 1, "name": "Kanto", "caught": 25, "total": 151, "pct": 17}],
    "wanted": {"cards": 2, "cost": Decimal("5.50")},
}


def collection(role: str) -> dict:
    return {"id": 10, "name": "Ash's Cards", "slug": "ash-s-cards", "role": role, "wanted_public": True,
            "card_count": 2, "unique_count": 1, "total_value": Decimal("9.00")}


@pytest.fixture
def app():
    with patch("app.db.ConnectionPool"):
        return create_app({"TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False,
                           "POKEWALLET_API_KEY": "",
                           "BACKGROUND_JOBS": False})


@pytest.fixture
def stub(tmp_path, app):
    """Patch repository functions; returns a dict to set the user's role."""
    app.config["UPLOAD_DIR"] = tmp_path
    stack = ExitStack()
    state = {"overview": OVERVIEW, "role": "viewer", "user": dict(USER), "added": [], "wanted": [], "public": True,
             "scan": PhotoResult(
                 candidates=[identify.Candidate(CARD, 97, ["name", "number", "photo 91%"])],
                 reading=identify.Reading(name="Pikachu ex", number="57", set_name="Surging Sparks"),
                 top_score=91.0)}

    def add_copy(cid, card_id, variant, condition, qty, notes):
        state["added"].append((card_id, variant, condition, qty))
        return {"quantity": qty, "inserted": True}

    targets = {
        "user_repository.get_by_id": lambda i: state["user"],
        "user_repository.get_by_username": lambda n: state["user"] if n == "ash" else None,
        "collection_repository.get_for_user":
            lambda slug, uid: collection(state["role"]) if state["role"] and slug == "ash-s-cards" else None,
        "collection_repository.list_for_user": lambda uid: [collection(state["role"] or "viewer")],
        "owned_repository.list_owned": lambda *a, **k: ([OWNED], 1),
        "owned_repository.filter_options": lambda cid: {"sets": [{"id": 7, "name": "Surging Sparks", "n": 1}],
                                                       "rarities": ["Double Rare"], "variants": ["Holofoil"]},
        "owned_repository.stats": lambda cid: {"copies": 2, "unique_cards": 1, "sets": 1, "species": 1,
                                              "value": Decimal("9.00")},
        "pricecharting.count": lambda: len(state.get("pc_products", [])),
        "pricecharting.all_products": lambda: list(state.get("pc_products", [])),
        "pricecharting.save_products": lambda products: state.__setitem__("pc_products", list(products)),
        "wanted_repository.wanted_ids": lambda cid, set_pk=None: set(state["wanted"]),
        "wanted_repository.is_wanted": lambda cid, card_id: card_id in state["wanted"],
        "wanted_repository.toggle": lambda cid, card_id: (state["wanted"].remove(card_id) or False) if card_id in state["wanted"]
            else (state["wanted"].append(card_id) or True),
        "wanted_repository.list_wanted": lambda cid: [{**CARD, "price_from": 4.5}] if state["wanted"] else [],
        "wanted_repository.get_public": lambda slug: ({"id": 10, "name": "Ash's Cards", "slug": slug}
                                                      if slug == "ash-s-cards" and state["public"] else None),
        "wanted_repository.is_public_wanted": lambda cid, card_id: card_id in state["wanted"],
        "wanted_repository.add_missing": lambda cid, pk: state.setdefault("bulk_wanted", []).append(pk) or 3,
        "wanted_repository.clear_set": lambda cid, pk: state.setdefault("cleared", []).append(pk) or 2,
        "collection_repository.set_master_set": lambda cid, on: state.setdefault("master", []).append(on),
        "wanted_repository.set_public": lambda cid, flag: state.__setitem__("public", flag),
        "overview_repository.overview": lambda cid, **k: state["overview"],
        "owned_repository.owned_card_ids": lambda cid: ["sv8-057"],
        "owned_repository.price_overview": lambda cid: {"cards": 4, "priced": 3, "from_tcgdex": 2, "from_pokewallet": 1,
                                                       "oldest": None, "newest": None},
        "catalog_service.bulk_status": lambda cid: state.get("bulk"),
        "catalog_service.start_bulk_price_refresh": lambda cid, ids: state.setdefault("bulk_calls", []).append((cid, ids)) or "started",
        "owned_repository.all_owned": lambda cid: [OWNED],
        "owned_repository.copies_of": lambda cid, card_id: [OWNED],
        "owned_repository.get_copy": lambda cid, oid: OWNED if oid == 11 else None,
        "owned_repository.add_copy": add_copy,
        "owned_repository.species_progress": lambda *a, **k: [
            {"dex_number": 25, "name": "Pikachu", "generation": 1, "cards": 1, "copies": 2},
            {"dex_number": 26, "name": "Raichu", "generation": 1, "cards": 0, "copies": 0}],
        "owned_repository.get_species": lambda dex: {"dex_number": 25, "name": "Pikachu", "generation": 1},
        "catalog_repository.neighbours": lambda cid, pk: ({"id": "sv8-056", "name": "Pichu", "card_number": "56/191", "local_id": "56"}, None) if pk else (None, None),
        "catalog_repository.get_card": lambda cid: CARD if cid == "sv8-057" else None,
                "catalog_repository.list_sets": lambda *a, **k: [SET],
        "catalog_repository.series_list": lambda: ["Scarlet & Violet"],
        "catalog_repository.get_set": lambda pk: SET if pk == 7 else None,
        "catalog_repository.set_cards": lambda cid, pk: [{**CARD, "qty": 2, "owned_variants": ["Reverse Holofoil"]},
                                                         {**CARD, "id": "sv8-058", "name": "Raichu", "qty": 0}],
        "catalog_repository.species_cards": lambda cid, dex: [{**CARD, "qty": 2}],
        "catalog_repository.search_local": lambda name, limit=60: [CARD],
        "photoid.identify_photo": lambda *a, **k: state["scan"],
        "catalog_service.ensure_sets": lambda force=False: 0,
        "catalog_service.ensure_set_cards": lambda pk, force=False: SET,
        "catalog_service.ensure_detail": lambda cid: CARD,
        "catalog_service.start_set_sync": lambda pk, force=False: state.setdefault("sync", []).append((pk, force)) or "started",
        "catalog_service.refresh_price_later": lambda cid: state.setdefault("priced", []).append(cid),
        "catalog_service.search_cards": lambda name, number="", detail_for=6: [CARD],
    }
    for name, fn in targets.items():
        stack.enter_context(patch(f"app.{name}", side_effect=fn))
    yield state
    stack.close()


@pytest.fixture
def client(app, stub):
    return app.test_client()


@pytest.fixture
def login(client):
    def _login():
        with client.session_transaction() as s:
            s["_user_id"] = "1"
    return _login
