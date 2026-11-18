"""Create the first administrator from environment variables, once.

Used by the Docker entrypoint. Set FIRST_ADMIN_USER and FIRST_ADMIN_PASSWORD (e.g. in docker-compose) and the account is
created on the first start, while no users exist yet. After that the variables are ignored: change the password in the app,
and remove the password from the compose file.
"""
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import collection_repository as coll_repo  # noqa: E402
from app.config import Config  # noqa: E402
from app.user_forms import password_error, username_error  # noqa: E402


def bootstrap(username: str, password: str) -> str:
    """Create the admin if there are no users yet. Returns a one-line message about what happened."""
    if not username or not password:
        return "No FIRST_ADMIN_USER / FIRST_ADMIN_PASSWORD set; create an account with scripts/create_user.py --admin."
    if err := username_error(username) or password_error(password, Config.MIN_PASSWORD_LENGTH):
        return f"Not creating the first admin: {err}"
    with psycopg.connect(Config.DATABASE_URL, row_factory=dict_row) as conn:
        if conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]:
            return "Users already exist; FIRST_ADMIN_* ignored."
        row = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, TRUE) RETURNING id",
            (username, generate_password_hash(password))).fetchone()
        slug = coll_repo.create(coll_repo.personal_name(username), row["id"], conn=conn)
    return f"Created admin user '{username}' with collection /c/{slug}/. Sign in, then remove FIRST_ADMIN_PASSWORD from your config."


if __name__ == "__main__":
    print(bootstrap(os.environ.get("FIRST_ADMIN_USER", "").strip(), os.environ.get("FIRST_ADMIN_PASSWORD", "")), flush=True)
