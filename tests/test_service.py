"""Catalog service: query parsing, search flow, expansion download state machine."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import catalog_service as svc, tcgdex as t


@pytest.mark.parametrize("text, expected", [
    ("Charizard ex 125/197", ("Charizard ex", "125")), ("Pikachu #25", ("Pikachu", "25")),
    ("pikachu", ("pikachu", "")), ("Pikachu V SWSH039", ("Pikachu V", "SWSH039")),
    ("25", ("25", "")), ("Mr. Mime", ("Mr. Mime", "")),
])
def test_split_query(text, expected):
    assert svc.split_query(text) == expected


def resume(cid, local, name):
    return t.normalize_resume({"id": cid, "localId": local, "name": name, "image": None})


def test_search_filters_by_number_and_fetches_details_for_top_candidates(app):
    found = [resume("a-1", "1", "Pikachu"), resume("b-25", "25", "Pikachu"), resume("c-025", "025", "Pikachu V")]
    client = MagicMock(search_cards=MagicMock(return_value=found))
    rows = [{"id": "b-25", "clean_name": "Pikachu", "card_number": "25", "set_code": "B", "set_name": "B", "hp": None},
            {"id": "c-025", "clean_name": "Pikachu V", "card_number": "025", "set_code": "C", "set_name": "C", "hp": None}]
    with app.app_context(), patch.object(svc, "tcgdex", return_value=client), \
         patch.object(svc, "ensure_sets"), patch.object(svc.catalog, "save_resumes") as save, \
         patch.object(svc.catalog, "get_cards", return_value=rows), \
         patch.object(svc, "ensure_detail") as detail:
        out = svc.search_cards("Pikachu", "25")
    assert out == rows
    saved = [c["id"] for c in save.call_args.args[0]]
    assert saved == ["b-25", "c-025"]                    # only cards with number 25 (leading zeros ignored)
    assert [c.args[0] for c in detail.call_args_list] == ["b-25", "c-025"]   # best match first


def test_sync_states(app):
    base = {"images_synced_at": None, "details_synced_at": None, "sync_status": "idle"}
    with app.app_context():
        assert svc.start_set_sync(1) == "off"                        # background jobs disabled in tests
        app.config["BACKGROUND_JOBS"] = True
        with patch.object(svc.catalog, "get_set", return_value={**base, "images_synced_at": 1, "details_synced_at": 1}):
            assert svc.start_set_sync(1) == "done"                   # nothing to do
        with patch.object(svc.catalog, "get_set", return_value=base), \
             patch.object(svc.catalog, "claim_sync", return_value=False):
            assert svc.start_set_sync(1) == "running"
        with patch.object(svc.catalog, "get_set", return_value=base), \
             patch.object(svc.catalog, "claim_sync", return_value=True), \
             patch.object(svc.threading, "Thread") as thread:
            assert svc.start_set_sync(1) == "started" and thread.return_value.start.called


def test_sync_job_downloads_missing_images_and_details_then_marks_done(app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    cards = [{"id": f"s-{i}", "image_base": f"https://x/{i}", "has_detail": i == 1} for i in (1, 2, 3)]
    client = MagicMock()
    client.download.return_value = b"RIFF\0\0\0\0WEBP"
    client.get_card.return_value = {"id": "x"}
    with patch.object(svc, "tcgdex", return_value=client), patch.object(svc, "ensure_sets"), \
         patch.object(svc, "ensure_set_cards"), patch.object(svc.catalog, "cards_for_sync", return_value=cards), \
         patch.object(svc.catalog, "update_progress") as progress, patch.object(svc.catalog, "save_detail") as save, \
         patch.object(svc.catalog, "finish_sync") as finish, patch.object(svc.catalog, "fail_sync") as fail:
        svc._run_sync(app, 5, False)
    assert client.download.call_count == 3 and client.get_card.call_count == 2    # card 1 already had details
    assert save.call_count == 2 and progress.call_args.args == (5, 5, 5)
    assert finish.call_args.args == (5, True, True, None) and not fail.called
    with app.app_context():
        assert svc.cached_art("s-2", "low") == b"RIFF\0\0\0\0WEBP"


def test_sync_job_reports_partial_failures_and_total_failure(app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    cards = [{"id": "s-1", "image_base": "https://x/1", "has_detail": True},
             {"id": "s-2", "image_base": "https://x/2", "has_detail": True}]
    client = MagicMock()
    client.download.side_effect = [b"RIFF\0\0\0\0WEBP", t.TCGdexError("boom")]
    with patch.object(svc, "tcgdex", return_value=client), patch.object(svc, "ensure_sets"), \
         patch.object(svc, "ensure_set_cards"), patch.object(svc.catalog, "cards_for_sync", return_value=cards), \
         patch.object(svc.catalog, "update_progress"), patch.object(svc.catalog, "finish_sync") as finish:
        svc._run_sync(app, 5, False)
    _, images_ok, details_ok, message = finish.call_args.args
    assert images_ok is False and details_ok is True and "1 images" in message
    with patch.object(svc, "ensure_sets", side_effect=t.TCGdexError("TCGdex is down")), \
         patch.object(svc.catalog, "fail_sync") as fail:
        svc._run_sync(app, 5, False)
    assert fail.call_args.args == (5, "TCGdex is down")


ROW = {"id": "sv8-57", "clean_name": "Pikachu ex", "card_number": "57/191", "set_code": "SSP", "set_name": "Surging Sparks"}


def pokewallet_client():
    right = {"id": "pk_right", "name": "Pikachu ex", "clean_name": "Pikachu ex", "card_number": "057/191", "hp": None,
             "set_code": "SSP", "set_name": "Surging Sparks", "prices": {"Holofoil": 4.5}}
    wrong = {**right, "id": "pk_wrong", "card_number": "100/191", "prices": {"Holofoil": 99}}
    return MagicMock(search=MagicMock(return_value={"cards": [wrong, right]}))


def test_refresh_price_uses_tcgdex_first_and_never_asks_pokewallet(app):
    app.config["POKEWALLET_API_KEY"] = "key"
    detail = {"id": "sv8-57", "prices": {"Holofoil": 4.5}}
    with app.app_context(), patch.object(svc, "tcgdex", return_value=MagicMock(get_card=MagicMock(return_value=detail))), \
         patch.object(svc, "pokewallet") as wallet, patch.object(svc.catalog, "get_card", return_value=ROW), \
         patch.object(svc.catalog, "save_detail") as save:
        assert svc.refresh_price("sv8-57") == "TCGdex"
    save.assert_called_once_with(detail)
    wallet.assert_not_called()


def test_refresh_price_falls_back_to_pokewallet_matching_by_number_and_set(app):
    app.config["POKEWALLET_API_KEY"] = "key"
    detail = {"id": "sv8-57", "prices": {}}
    with app.app_context(), patch.object(svc, "tcgdex", return_value=MagicMock(get_card=MagicMock(return_value=detail))), \
         patch.object(svc, "pokewallet", return_value=pokewallet_client()), \
         patch.object(svc.catalog, "get_card", return_value=ROW), patch.object(svc.catalog, "save_detail"), \
         patch.object(svc.catalog, "save_prices") as save:
        assert svc.refresh_price("sv8-57") == "PokeWallet"
    assert save.call_args.args == ("sv8-57", "pk_right", {"Holofoil": 4.5})


def test_no_price_anywhere_keeps_what_was_stored_and_notes_the_check(app):
    app.config["POKEWALLET_API_KEY"] = ""
    with app.app_context(), patch.object(svc, "tcgdex", return_value=MagicMock(get_card=MagicMock(return_value={"id": "x", "prices": {}}))), \
         patch.object(svc.catalog, "get_card", return_value=ROW), patch.object(svc.catalog, "save_detail"), \
         patch.object(svc.catalog, "save_prices") as save, patch.object(svc.catalog, "mark_price_checked") as mark:
        assert svc.refresh_price("sv8-57") is None
    save.assert_not_called()                      # nothing overwritten
    mark.assert_called_once_with("sv8-57")


def test_refresh_without_fallback_skips_pokewallet(app):
    app.config["POKEWALLET_API_KEY"] = "key"
    with app.app_context(), patch.object(svc, "tcgdex", return_value=MagicMock(get_card=MagicMock(return_value={"id": "x", "prices": {}}))), \
         patch.object(svc, "pokewallet") as wallet, patch.object(svc.catalog, "get_card", return_value=ROW), \
         patch.object(svc.catalog, "save_detail"), patch.object(svc.catalog, "mark_price_checked"):
        assert svc.refresh_price("sv8-57", fallback=False) is None
    wallet.assert_not_called()


def test_price_staleness():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    assert svc.price_is_stale({"prices_updated_at": None}, 24)
    assert svc.price_is_stale({"prices_updated_at": now - timedelta(hours=30)}, 24)
    assert not svc.price_is_stale({"prices_updated_at": now - timedelta(hours=2)}, 24)
    assert not svc.price_is_stale({"prices_updated_at": (now - timedelta(hours=2)).replace(tzinfo=None)}, 24)


def run_bulk(app, ids, results, fallback_prices=None, key="key", fallback_max=2):
    """Run the bulk refresh synchronously by making the thread run inline."""
    import threading
    app.config.update(BACKGROUND_JOBS=True, POKEWALLET_API_KEY=key, PRICE_FALLBACK_MAX=fallback_max, SYNC_WORKERS=1)

    class Inline:
        def __init__(self, target, **kw): self.target = target
        def start(self): self.target()

    def fake_refresh(card_id, fallback=True):
        return "TCGdex" if results[card_id] else None

    with app.app_context(), patch.object(svc, "threading", SimpleNamespace(Thread=Inline, Lock=threading.Lock)), patch.object(svc, "refresh_price", fake_refresh), \
         patch.object(svc.catalog, "get_card", side_effect=lambda i: {**ROW, "id": i}), \
         patch.object(svc, "_pokewallet_price", side_effect=lambda i, row: bool(fallback_prices and fallback_prices.get(i))) as fb:
        status = svc.start_bulk_price_refresh(99, ids)
    return status, svc.bulk_status(99), fb


def test_bulk_refresh_counts_and_limits_the_pokewallet_fallback(app):
    ids = ["a", "b", "c", "d", "e"]
    status, state, fb = run_bulk(app, ids, {"a": True, "b": False, "c": False, "d": False, "e": True}, {"b": True})
    assert status == "started" and state["status"] == "done"
    assert (state["total"], state["done"], state["priced"], state["fallback"], state["unpriced"]) == (5, 5, 3, 1, 2)
    assert fb.call_count == 2 and "Only the first 2" in state["note"]            # 3 unpriced cards, limit 2


def test_bulk_refresh_without_pokewallet_key(app):
    status, state, fb = run_bulk(app, ["a", "b"], {"a": True, "b": False}, key="")
    assert state["priced"] == 1 and state["unpriced"] == 1 and fb.call_count == 0 and state["note"] is None


def test_bulk_refresh_refuses_a_second_run_and_respects_the_jobs_switch(app):
    app.config["BACKGROUND_JOBS"] = False
    with app.app_context():
        assert svc.start_bulk_price_refresh(5, ["a"]) == "off"
    app.config["BACKGROUND_JOBS"] = True
    svc._bulk[6] = {"status": "running"}
    with app.app_context():
        assert svc.start_bulk_price_refresh(6, ["a"]) == "running"
    svc._bulk.pop(6)
