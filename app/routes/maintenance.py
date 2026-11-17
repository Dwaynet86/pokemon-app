"""Maintenance page for a collection: overview and data tools (export, import).

Owners only. To add a tool: add a route here and a <section class="panel">
in templates/maintenance.html.
"""
from datetime import date

from flask import (Blueprint, Response, current_app, flash, g, redirect,
                   render_template, request, send_file, url_for)

from .. import catalog_service as service, exporter, images, importer, owned_repository as owned
from ..permissions import collection_access
from ..tcgdex import TCGdexError

bp = Blueprint("maintenance", __name__)


def _filename(ext: str) -> str:
    return f"{g.collection['slug']}-{date.today().isoformat()}.{ext}"


def _download(body: bytes | str, mimetype: str, ext: str) -> Response:
    return Response(
        body, mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{_filename(ext)}"'},
    )


@bp.get("/")
@collection_access("owner")
def index():
    cid = g.collection["id"]
    return render_template(
        "maintenance.html", stats=owned.stats(cid), art_bytes=images.catalog_usage(),
        price_info=owned.price_overview(cid), price_run=service.bulk_status(cid),
        fallback_on=service.prices_configured(), fallback_max=current_app.config["PRICE_FALLBACK_MAX"],
    )


@bp.post("/refresh-prices")
@collection_access("owner")
def refresh_prices():
    cid = g.collection["id"]
    ids = owned.owned_card_ids(cid)
    if not ids:
        flash("There are no cards to price yet.", "error")
    else:
        status = service.start_bulk_price_refresh(cid, ids)
        flash({"started": f"Refreshing prices for {len(ids)} card{'' if len(ids) == 1 else 's'} in the background. "
                          "Reload this page in a minute to see the result.",
               "running": "A price refresh is already running. Reload this page to see how it's going.",
               "off": "Background jobs are turned off on this server."}[status], "success" if status == "started" else "error")
    return redirect(url_for("maintenance.index"))


@bp.get("/export.csv")
@collection_access("owner")
def export_csv():
    return _download(exporter.to_csv(owned.all_owned(g.collection["id"])), "text/csv; charset=utf-8", "csv")


@bp.get("/export.json")
@collection_access("owner")
def export_json():
    body = exporter.to_json(owned.all_owned(g.collection["id"]), g.collection["name"])
    return _download(body, "application/json", "json")


@bp.post("/import")
@collection_access("owner")
def import_backup():
    upload = request.files.get("backup")
    if not upload or not upload.filename:
        flash("Choose a backup file to import.", "error")
        return redirect(url_for("maintenance.index"))
    try:
        result = importer.restore(
            g.collection["id"], upload, skip_duplicates=bool(request.form.get("skip_duplicates"))
        )
    except importer.BackupError as exc:
        shown = exc.problems[:5]
        more = len(exc.problems) - len(shown)
        flash("Import failed, nothing was changed. " + " ".join(shown)
              + (f" (and {more} more problems)" if more > 0 else ""), "error")
        return redirect(url_for("maintenance.index"))
    except TCGdexError as exc:
        flash(f"Import failed, nothing was changed. {exc}", "error")
        return redirect(url_for("maintenance.index"))

    parts = [f"Imported {result.copies} card{'' if result.copies == 1 else 's'} "
             f"({result.created} new entr{'y' if result.created == 1 else 'ies'}"
             + (f", {result.merged} added to existing entries" if result.merged else "") + ")"]
    if result.skipped:
        parts.append(f"skipped {result.skipped} already in the collection")
    if result.cards_fetched:
        parts.append(f"looked up {result.cards_fetched} card{'' if result.cards_fetched == 1 else 's'} on TCGdex")
    flash(", ".join(parts) + ".", "success")
    return redirect(url_for("maintenance.index"))
