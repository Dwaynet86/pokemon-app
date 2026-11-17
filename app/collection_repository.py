"""Database access for collections and their members."""
import re

from .db import get_conn, use_conn

ROLES = ("owner", "editor", "viewer")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug or "collection"


def list_for_user(user_id: int) -> list[dict]:
    """Collections the user belongs to, with their role, card counts and market value."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT c.*, m.role,
                      (SELECT COALESCE(sum(o.quantity), 0) FROM owned_cards o
                        WHERE o.collection_id = c.id) AS card_count,
                      (SELECT count(DISTINCT o.card_id) FROM owned_cards o
                        WHERE o.collection_id = c.id) AS unique_count,
                      (SELECT COALESCE(sum(o.quantity * NULLIF(k.prices ->> o.variant, '')::numeric), 0)
                         FROM owned_cards o JOIN cards k ON k.id = o.card_id
                        WHERE o.collection_id = c.id) AS total_value
               FROM collections c JOIN collection_members m ON m.collection_id = c.id
               WHERE m.user_id = %s ORDER BY lower(c.name)""",
            (user_id,),
        ).fetchall()


def get_for_user(slug: str, user_id: int) -> dict | None:
    """The collection plus the user's role, or None if missing / not a member."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT c.*, m.role FROM collections c
               JOIN collection_members m ON m.collection_id = c.id
               WHERE c.slug = %s AND m.user_id = %s""",
            (slug, user_id),
        ).fetchone()


def set_master_set(collection_id: int, on: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE collections SET master_set = %s WHERE id = %s", (on, collection_id))


def create(name: str, owner_id: int, conn=None) -> str:
    """Create a collection owned by `owner_id`. Returns its slug.

    Pass `conn` to run inside the caller's transaction (e.g. when creating a user).
    """
    base = slugify(name)
    with use_conn(conn) as c:
        slug, n = base, 1
        while c.execute("SELECT 1 FROM collections WHERE slug = %s", (slug,)).fetchone():
            n += 1
            slug = f"{base}-{n}"
        row = c.execute(
            "INSERT INTO collections (name, slug) VALUES (%s, %s) RETURNING id", (name, slug)
        ).fetchone()
        cid = row["id"] if isinstance(row, dict) else row[0]
        c.execute(
            "INSERT INTO collection_members (collection_id, user_id, role) VALUES (%s, %s, 'owner')",
            (cid, owner_id),
        )
    return slug


def personal_name(username: str) -> str:
    """Name of the collection every new user starts with."""
    return f"{username}'s Cards"


# ---- Members ---------------------------------------------------------------

def list_members(collection_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT u.id, u.username, u.is_active, m.role
               FROM collection_members m JOIN users u ON u.id = m.user_id
               WHERE m.collection_id = %s
               ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END,
                        lower(u.username)""",
            (collection_id,),
        ).fetchall()


def get_member_role(collection_id: int, user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT role FROM collection_members WHERE collection_id = %s AND user_id = %s",
            (collection_id, user_id),
        ).fetchone()
    return row["role"] if row else None


def count_owners(collection_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM collection_members WHERE collection_id = %s AND role = 'owner'",
            (collection_id,),
        ).fetchone()["n"]


def add_member(collection_id: int, user_id: int, role: str) -> bool:
    """Add a member. Returns False if the user is already a member."""
    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO collection_members (collection_id, user_id, role)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING user_id""",
            (collection_id, user_id, role),
        ).fetchone()
    return row is not None


def set_role(collection_id: int, user_id: int, role: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE collection_members SET role = %s WHERE collection_id = %s AND user_id = %s",
            (role, collection_id, user_id),
        )


def remove_member(collection_id: int, user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM collection_members WHERE collection_id = %s AND user_id = %s",
            (collection_id, user_id),
        )
