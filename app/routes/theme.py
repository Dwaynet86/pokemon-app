"""Save the signed-in user's page theme (colours). Called by static/js/theme.js."""
from flask import Blueprint, abort, request
from flask_login import current_user

from .. import theme, user_repository as users

bp = Blueprint("theme", __name__)


@bp.post("/theme")
def save():
    try:
        state = theme.normalize(request.get_json(silent=True), strict=True)
    except ValueError:
        abort(400)
    users.set_theme(current_user.id, theme.dumps(state))
    return "", 204
