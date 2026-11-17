"""Official card artwork: served from disk, downloaded once from TCGdex's static image host."""
from flask import Blueprint, Response, abort, request

from .. import catalog_repository as catalog, catalog_service as service

bp = Blueprint("art", __name__)

def placeholder(card: dict) -> str:
    """Stand-in for a card with no artwork: its name and number, so it can still be recognised."""
    from textwrap import wrap
    from xml.sax.saxutils import escape
    lines = wrap(card.get("name") or "", 16)[:3]
    text = "".join(f'<tspan x="125" dy="{0 if i == 0 else 1.2}em">{escape(line)}</tspan>' for i, line in enumerate(lines))
    number = escape(card.get("card_number") or card.get("local_id") or "")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 250 350">'
        '<rect width="250" height="350" rx="14" fill="#8a96a3" opacity=".25"/>'
        '<rect x="14" y="14" width="222" height="322" rx="8" fill="none" stroke="#8a96a3" stroke-width="3" opacity=".5"/>'
        f'<text y="150" text-anchor="middle" font-family="system-ui, sans-serif" font-size="22" font-weight="600" fill="#5b6672">{text}</text>'
        f'<text x="125" y="300" text-anchor="middle" font-family="system-ui, sans-serif" font-size="18" fill="#5b6672">{number}</text>'
        '</svg>'
    )


def art_response(card: dict, size: str, cache_only: bool = False) -> Response:
    """The card's artwork (stored, else TCGdex, else the fallback sources) or a named placeholder."""
    data = service.cached_art(card["id"], size)
    if data is None and not cache_only:
        data = service.fetch_art_for(card, size)
    if data:
        return Response(data, mimetype="image/webp", headers={"Cache-Control": "private, max-age=31536000"})
    return Response(placeholder(card), mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})


@bp.get("/art/<card_id>")
def card_art(card_id: str):
    """`?size=high` for the large image (card page); `?cache=only` never goes to the network."""
    card = catalog.get_card(card_id)
    if card is None:
        abort(404)
    size = request.args.get("size", "low")
    if size not in service.SIZES:
        abort(400)
    return art_response(card, size, request.args.get("cache") == "only")
