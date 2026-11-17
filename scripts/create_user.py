"""Create a user (and their personal collection) from the command line.

    python scripts/create_user.py USERNAME --admin    # first account: do this once
    python scripts/create_user.py USERNAME

Every user starts with their own collection, "<username>'s Cards". With --admin the user
can also manage accounts (create users, reset passwords, deactivate).
"""
import argparse
import getpass
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import collection_repository as coll_repo  # noqa: E402
from app.config import Config  # noqa: E402
from app.user_forms import password_error, username_error  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("username")
    parser.add_argument("--admin", action="store_true", help="grant account-management rights")
    args = parser.parse_args()

    if err := username_error(args.username):
        sys.exit(err)
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords don't match.")
    if err := password_error(password, Config.MIN_PASSWORD_LENGTH):
        sys.exit(err)

    with psycopg.connect(Config.DATABASE_URL, row_factory=dict_row) as conn:
        row = conn.execute(
            """INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s)
               ON CONFLICT (lower(username)) DO NOTHING RETURNING id""",
            (args.username, generate_password_hash(password), args.admin),
        ).fetchone()
        if row is None:
            sys.exit("That username already exists.")
        slug = coll_repo.create(coll_repo.personal_name(args.username), row["id"], conn=conn)
    print(f"Created {'admin ' if args.admin else ''}user '{args.username}' with collection /c/{slug}/.")


if __name__ == "__main__":
    main()
