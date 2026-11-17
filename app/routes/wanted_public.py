"""The public wanted list: viewable by anyone, no login. Read-only, and only for collections that share it.

Signed-out visitors get a bare page with no links to the rest of the app. A signed-in member of the collection
is sent to the ordinary Wanted page instead; any other signed-in user sees it inside the normal layout.
"""
from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user

from .. import catalog_repository as catalog, collection_repository as coll_repo, wanted_repository as wanted
from .art import art_response

bp = Blueprint("wanted_public", __name__)


@bp.get("/wanted/<slug>")
def view(slug: str):
    row = wanted.get_public(slug)
    if row is None:
        abort(404)
    if current_user.is_authenticated and coll_repo.get_for_user(slug, current_user.id):
        return redirect(url_for("cards.wanted_list", slug=slug))
    cards = wanted.list_wanted(row["id"])
    return render_template(
        "wanted_public.html", owner_collection=row, cards=cards,
        layout="base.html" if current_user.is_authenticated else "base_public.html",
        total=sum(c["price_from"] or 0 for c in cards))


@bp.get("/wanted/<slug>/art/<card_id>")
def art(slug: str, card_id: str):
    """Artwork for a card on a shared wanted list (and nothing else, so strangers can't make the server fetch images)."""
    row = wanted.get_public(slug)
    card = catalog.get_card(card_id) if row else None
    if card is None or not wanted.is_public_wanted(row["id"], card_id):
        abort(404)
    return art_response(card, "low")
