"""Everything inside a collection: gallery, expansion view, Pokemon view, adding cards."""
from math import ceil

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user

from .. import (catalog_repository as catalog, catalog_service as service, collection_repository as collection_repo,
                finishes, identify, images,
                owned_repository as owned, photoid, species as species_mod, wanted_repository as wanted,
                overview_repository as overview_repo)
from ..permissions import collection_access
from ..pokewallet import PokeWalletError
from ..tcgdex import TCGdexError
from ..tcgtracking import TCGTracking

bp = Blueprint("cards", __name__)


def _cid() -> int:
    return g.collection["id"]


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---- Gallery: every card you own ---------------------------------------------------

@bp.get("/")
@collection_access("viewer")
def index():
    per_page = current_app.config["CARDS_PER_PAGE"]
    args = {"q": request.args.get("q", "").strip(), "set_pk": _int(request.args.get("set")),
            "rarity": request.args.get("rarity", ""), "variant": request.args.get("variant", ""),
            "sort": request.args.get("sort", "added")}
    page = max(_int(request.args.get("page"), 1), 1)
    rows, total = owned.list_owned(_cid(), page=page, per_page=per_page, **args)
    filtered = any(args[k] for k in ("q", "set_pk", "rarity", "variant"))
    return render_template(
        "cards/index.html", rows=rows, total=total, page=page, pages=max(ceil(total / per_page), 1),
        search=args["q"], set_pk=args["set_pk"], rarity=args["rarity"], variant=args["variant"],
        sort=args["sort"], filtered=filtered, options=owned.filter_options(_cid()),
        stats=owned.stats(_cid()),
    )


# ---- Expansion view ----------------------------------------------------------------

def _master() -> bool:
    return bool(g.collection.get("master_set"))


def _safe_back(default: str) -> str:
    """A same-site path to return to after a form post (never another site)."""
    back = request.form.get("back", "")
    return back if back.startswith("/") and not back.startswith("//") else default


@bp.post("/master-set")
@collection_access("editor")
def master_set():
    """Count every finish of a card (normal / holo / reverse holo) toward completion, or just the card."""
    on = (request.form.getlist("master") or ["0"])[-1] == "1"
    collection_repo.set_master_set(_cid(), on)
    flash("Master set on: every finish of a card counts toward completion." if on
          else "Master set off: a card counts once, whatever its finish.", "success")
    return redirect(_safe_back(url_for("cards.sets")))


@bp.get("/overview")
@collection_access("viewer")
def overview():
    return render_template("cards/overview.html", ov=overview_repo.overview(_cid(), master=_master()), master=_master())


@bp.get("/sets")
@collection_access("viewer")
def sets():
    try:
        service.ensure_sets()
    except TCGdexError as exc:
        flash(str(exc), "error")
    q, series = request.args.get("q", "").strip(), request.args.get("series", "")
    # "Only expansions I collect" is on by default. The form sends mine=0 (hidden field) then
    # mine=1 (checkbox, when ticked), so the last value is the choice; no value means a fresh visit.
    choice = request.args.getlist("mine")
    mine = choice[-1] != "0" if choice else True
    rows = catalog.list_sets(_cid(), q, series, mine, _master())
    first_visit_fallback = False
    if mine and not choice and not q and not series and not rows:
        # Nothing collected yet: show every expansion rather than an empty page.
        mine, first_visit_fallback = False, True
        rows = catalog.list_sets(_cid(), q, series, False, _master())
    return render_template(
        "cards/sets.html", sets=rows, search=q, series=series, mine=mine,
        series_options=catalog.series_list(), nothing_collected=first_visit_fallback,
    )


@bp.post("/sets/refresh")
@collection_access("editor")
def refresh_sets():
    try:
        service.ensure_sets(force=True)
        flash("Expansion list updated.", "success")
    except TCGdexError as exc:
        flash(str(exc), "error")
    return redirect(url_for("cards.sets"))


@bp.get("/sets/<int:set_pk>")
@collection_access("viewer")
def set_detail(set_pk: int):
    row = catalog.get_set(set_pk)
    if row is None:
        abort(404)
    if not row["cards_fetched_at"]:      # one request for the whole card list, then cached for good
        try:
            row = service.ensure_set_cards(set_pk)
        except TCGdexError as exc:
            flash(str(exc), "error")
    cards = catalog.set_cards(_cid(), set_pk)
    master = _master()
    have, total = finishes.totals(cards, master)
    show = request.args.get("show", "all")
    options = finishes.options(cards)
    lacking = request.args.get("lacking", "")
    if lacking not in options:
        lacking = ""
    visible = [c for c in cards if (show == "all" or (show == "have") == bool(c["qty"]))
               and (not lacking or finishes.lacks(c, lacking))]
    wanted_ids = wanted.wanted_ids(_cid(), set_pk)
    missing_ids = {c["id"] for c in cards if not c["qty"]}
    return render_template("cards/set_detail.html", set=row, cards=visible, have=have, total=total, show=show,
                           master=master, lacking=lacking, finish_options=options, wanted_ids=wanted_ids,
                           missing_count=len(missing_ids), unwanted_missing=len(missing_ids - wanted_ids),
                           wanted_here=len(wanted_ids))


@bp.get("/sets/<int:set_pk>/status")
@collection_access("viewer")
def set_status(set_pk: int):
    """Progress of an expansion download, polled by the expansion page."""
    row = catalog.get_set(set_pk)
    if row is None:
        abort(404)
    return jsonify(status=row["sync_status"], done=row["sync_done"], total=row["sync_total"],
                   message=row["sync_message"])


@bp.post("/sets/<int:set_pk>/download")
@collection_access("editor")
def download_set(set_pk: int):
    """Download the whole expansion (cards, details, artwork) so it's stored locally."""
    if catalog.get_set(set_pk) is None:
        abort(404)
    force = request.form.get("force") == "1"
    state = service.start_set_sync(set_pk, force=force)
    flash({"started": "Downloading the expansion in the background.",
           "running": "That expansion is already downloading.",
           "done": "That expansion is already stored locally.",
           "off": "Background downloads are turned off."}[state], "success" if state == "started" else "error")
    return redirect(url_for("cards.set_detail", set_pk=set_pk))


# ---- Pokemon view -------------------------------------------------------------------

@bp.get("/pokemon")
@collection_access("viewer")
def pokemon():
    gen = _int(request.args.get("gen"))
    q, show = request.args.get("q", "").strip(), request.args.get("show", "all")
    rows = owned.species_progress(_cid(), gen, q)
    caught = sum(1 for r in rows if r["cards"])
    visible = [r for r in rows if show == "all" or (show == "have") == bool(r["cards"])]
    return render_template(
        "cards/pokemon.html", species=visible, caught=caught, total=len(rows), gen=gen, search=q,
        show=show, generations=species_mod.GENERATION_NAMES,
    )


@bp.get("/pokemon/<int:dex>")
@collection_access("viewer")
def pokemon_detail(dex: int):
    sp = owned.get_species(dex)
    if sp is None:
        abort(404)
    return render_template("cards/pokemon_detail.html", sp=sp, cards=catalog.species_cards(_cid(), dex))


# ---- One card, and your copies of it --------------------------------------------------

@bp.get("/card/<card_id>")
@collection_access("viewer")
def card(card_id: str):
    row = catalog.get_card(card_id)
    if row is None:
        abort(404)
    if not row["has_detail"]:             # rarity, HP, variants: one request, then cached
        try:
            row = service.ensure_detail(card_id) or row
        except TCGdexError as exc:
            flash(str(exc), "error")
    prev_card, next_card = catalog.neighbours(card_id, row.get("set_id"))
    return render_template(
        "cards/card.html", card=row, prev_card=prev_card, next_card=next_card, wanted=wanted.is_wanted(_cid(), card_id),
        art_missing=service.cached_art(card_id, "low") is None and (service.recently_missed(card_id) or not row["image_base"]), copies=owned.copies_of(_cid(), card_id),
        variants=owned.variants_for(row), conditions=owned.CONDITIONS,
    )


@bp.post("/card/<card_id>/artwork")
@collection_access("editor")
def set_artwork(card_id: str):
    """Use your own picture for a card's artwork: an uploaded file or a link. Shared by every collection."""
    if catalog.get_card(card_id) is None:
        abort(404)
    upload, url = request.files.get("image"), request.form.get("url", "").strip()
    try:
        if upload and upload.filename:
            sizes = images.art_from_upload(upload)
        elif url:
            sizes = images.art_from_bytes(images.download_image(url))
        else:
            raise images.ImageError("Choose an image file or paste a link to one.")
    except images.ImageError as exc:
        flash(str(exc), "error")
    else:
        service.store_art(card_id, sizes)
        flash("Artwork saved.", "success")
    return redirect(url_for("cards.card", card_id=card_id))


@bp.post("/card/<card_id>/artwork/reset")
@collection_access("editor")
def reset_artwork(card_id: str):
    """Forget the stored picture and look for it again (TCGdex, then the alternate sources)."""
    card_row = catalog.get_card(card_id)
    if card_row is None:
        abort(404)
    service.clear_art(card_id)
    found = service.fetch_art_for(card_row, "low") is not None
    flash("Artwork downloaded again." if found else "No artwork was found from any source. You can upload your own.",
          "success" if found else "error")
    return redirect(url_for("cards.card", card_id=card_id))


@bp.post("/card/<card_id>/price")
@collection_access("editor")
def refresh_price(card_id: str):
    if catalog.get_card(card_id) is None:
        abort(404)
    try:
        source = service.refresh_price(card_id)
        flash(f"Price updated from {source}." if source else "No price is published for this card.", "success")
    except (TCGdexError, PokeWalletError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("cards.card", card_id=card_id))


def _copy_form():
    """Validated (variant, condition, quantity, notes) from the request, or an error message."""
    variant = request.form.get("variant", "").strip()
    condition = request.form.get("condition", "")
    quantity = _int(request.form.get("quantity"), 0)
    notes = request.form.get("notes", "").strip()[:500] or None
    if not variant or len(variant) > 60:
        return None, "Choose a variant."
    if condition not in owned.CONDITIONS:
        return None, "Choose a condition."
    if not 1 <= quantity <= 999:
        return None, "Quantity must be between 1 and 999."
    return (variant, condition, quantity, notes), None


@bp.post("/copies/<int:owned_id>")
@collection_access("editor")
def update_copy(owned_id: int):
    row = owned.get_copy(_cid(), owned_id)
    if row is None:
        abort(404)
    values, error = _copy_form()
    if error:
        flash(error, "error")
    else:
        owned.update_copy(_cid(), owned_id, *values)
        flash("Saved.", "success")
    return redirect(url_for("cards.card", card_id=row["card_id"]))


@bp.post("/copies/<int:owned_id>/delete")
@collection_access("editor")
def delete_copy(owned_id: int):
    row = owned.get_copy(_cid(), owned_id)
    if row is None:
        abort(404)
    owned.delete_copy(_cid(), owned_id)
    flash(f"Removed {row['name']} ({row['variant']}, {row['condition']}).", "success")
    return redirect(url_for("cards.card", card_id=row["card_id"]) if owned.copies_of(_cid(), row["card_id"])
                    else url_for("cards.index"))


# ---- Adding cards: photo -> identify -> confirm ----------------------------------------

@bp.get("/add")
@collection_access("editor")
def add():
    return render_template("cards/add.html", prices=service.prices_configured())


def _confirm_page(candidates, scan=None, token=None, info=None, query=""):
    return render_template(
        "cards/add_confirm.html", candidates=candidates, scan=scan, token=token,
        info=info or {}, query=query, conditions=owned.CONDITIONS,
        confident=identify.is_confident(candidates),
        default_variant=owned.default_variant(candidates[0].card) if candidates else "Normal",
        variants_by_card={c.card["id"]: owned.variants_for(c.card) for c in candidates},
    )


def _scanner() -> TCGTracking:
    cfg = current_app.config
    return TCGTracking(cfg["TCGTRACKING_BASE_URL"], cfg["TCGTRACKING_GAME_ID"], cfg["TCGTRACKING_TIMEOUT"])


@bp.post("/add/identify")
@collection_access("editor")
def add_identify():
    upload = request.files.get("photo")
    if not upload or not upload.filename:
        flash("Choose or take a photo first.", "error")
        return redirect(url_for("cards.add"))
    try:
        name = images.store_pending(upload)
    except images.ImageError as exc:
        flash(str(exc), "error")
        return redirect(url_for("cards.add"))
    token = images.make_token(current_user.id, name)

    result = photoid.identify_photo(
        images.pending_path(name), _scanner(), service.search_cards, catalog.search_local,
        min_score=current_app.config["SCAN_MIN_SCORE"])
    if result.error:
        flash(result.error + " You can still search by name.", "error")
    elif result.note:
        flash(result.note, "error")
    return _confirm_page(result.candidates, result, token, {}, result.query)


@bp.get("/add/search")
@collection_access("editor")
def add_search():
    """Find a card by name (and optionally number) instead of, or after, a photo."""
    query = request.args.get("q", "").strip()
    token = request.args.get("token") or None
    if not query:
        return redirect(url_for("cards.add"))
    name, number = service.split_query(query)
    info, cards = {"api_error": None}, []
    try:
        cards = service.search_cards(name, number)
    except TCGdexError as exc:
        info["api_error"] = str(exc)
        flash(str(exc), "error")
    if not cards:
        cards = catalog.search_local(name)
    ranked = identify.rank(identify.Reading(name=name, number=number), cards, limit=40)
    return _confirm_page(ranked, None, token, info, query)


@bp.get("/add/photo/<token>")
@collection_access("editor")
def add_photo(token: str):
    name = images.read_token(token, current_user.id)
    path = images.pending_path(name) if name else None
    if path is None:
        abort(404)
    resp = send_from_directory(path.parent, path.name)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.post("/add/confirm")
@collection_access("editor")
def add_confirm():
    token = request.form.get("token") or None
    card_row = catalog.get_card(request.form.get("card_id", ""))
    values, error = _copy_form()
    if card_row is None:
        error = "Choose which card this is."
    stay = request.form.get("back") == "card" and card_row is not None      # added from the card's own page
    if error:
        flash(error, "error")
        return redirect(url_for("cards.card", card_id=card_row["id"]) if stay else url_for("cards.add"))
    variant, condition, quantity, notes = values

    pending = images.read_token(token, current_user.id)
    if pending:
        images.discard_pending(pending)      # identified: the original photo isn't kept
    try:
        card_row = service.ensure_detail(card_row["id"]) or card_row   # full record: rarity, HP, Pokedex numbers
    except TCGdexError:
        pass
    row = owned.add_copy(_cid(), card_row["id"], variant, condition, quantity, notes)
    note = ""
    if card_row["set_id"] and service.start_set_sync(card_row["set_id"]) == "started":
        note = f" Downloading the rest of {card_row['set_name']} in the background."
    service.refresh_price_later(card_row["id"])
    flash(f"Added {card_row['name']} ({variant}, {condition}). You now have {row['quantity']}.{note}", "success")
    return redirect(url_for("cards.card", card_id=card_row["id"]) if stay else url_for("cards.add"))


@bp.get("/wanted")
@collection_access("viewer")
def wanted_list():
    cards = wanted.list_wanted(_cid())
    return render_template("cards/wanted.html", cards=cards, total=sum(c["price_from"] or 0 for c in cards),
                           public_url=url_for("wanted_public.view", slug=g.collection["slug"], _external=True))


@bp.post("/cards/<card_id>/wanted")
@collection_access("editor")
def toggle_wanted(card_id: str):
    """Add a card to the wanted list, or take it off. JSON for the expansion page buttons, a redirect for forms."""
    card_row = catalog.get_card(card_id)
    if card_row is None:
        abort(404)
    now_wanted = wanted.toggle(_cid(), card_id)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, wanted=now_wanted, name=card_row["name"])
    flash(f"{card_row['name']} {'added to' if now_wanted else 'removed from'} the wanted list.", "success")
    back = request.form.get("back")
    return redirect(url_for("cards.wanted_list") if back == "wanted"
                    else url_for("cards.card", card_id=card_id))


@bp.post("/sets/<int:set_pk>/wanted-missing")
@collection_access("editor")
def wanted_missing(set_pk: int):
    """Put every card you don't own from this expansion on the wanted list (or, with action=clear, take them all off)."""
    row = catalog.get_set(set_pk)
    if row is None:
        abort(404)
    if request.form.get("action") == "clear":
        n = wanted.clear_set(_cid(), set_pk)
        flash(f"Took {n} card{'' if n == 1 else 's'} from {row['name']} off the wanted list." if n
              else "None of this expansion's cards were on the wanted list.", "success")
    else:
        n = wanted.add_missing(_cid(), set_pk)
        flash(f"Added {n} missing card{'' if n == 1 else 's'} from {row['name']} to the wanted list." if n
              else "Every missing card from this expansion is already on the wanted list.", "success")
    return redirect(url_for("cards.set_detail", set_pk=set_pk, show=request.form.get("show") or None,
                            lacking=request.form.get("lacking") or None))


@bp.post("/wanted/sharing")
@collection_access("owner")
def wanted_sharing():
    public = request.form.get("public") == "1"
    wanted.set_public(_cid(), public)
    flash("The wanted list is now public: anyone with the link can see it." if public
          else "The wanted list is private again.", "success")
    return redirect(url_for("cards.wanted_list"))


@bp.post("/cards/<card_id>/quick-add")
@collection_access("editor")
def quick_add(card_id: str):
    """One-click add from the expansion view: one copy, the default finish, Near Mint."""
    card_row = catalog.get_card(card_id)
    if card_row is None:
        abort(404)
    try:
        card_row = service.ensure_detail(card_id) or card_row      # finishes (cached after the first time)
    except TCGdexError:
        pass
    variant, condition = owned.default_variant(card_row), owned.CONDITIONS[0]
    asked = request.values.get("variant")                 # a finish pill on an owned tile asks for a specific one
    if asked:
        if asked not in owned.variants_for(card_row):
            abort(400)
        variant = asked
    row = owned.add_copy(_cid(), card_id, variant, condition, 1, None)
    if card_row["set_id"]:
        service.start_set_sync(card_row["set_id"])
    service.refresh_price_later(card_id)
    total = sum(c["quantity"] for c in owned.copies_of(_cid(), card_id))
    return jsonify(ok=True, name=card_row["name"], variant=variant, condition=condition, variants=card_row["variants"] or [],
                   quantity=row["quantity"], total=total)


@bp.post("/add/cancel")
@collection_access("editor")
def add_cancel():
    name = images.read_token(request.form.get("token"), current_user.id)
    if name:
        images.discard_pending(name)
    return redirect(url_for("cards.add"))
