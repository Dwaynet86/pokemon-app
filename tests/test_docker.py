"""The container's health check, proxy setting and packaging files."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import create_app

ROOT = Path(__file__).resolve().parent.parent


def test_healthz_needs_no_login(client):
    assert client.get("/healthz").status_code in (200, 503)         # never a redirect to the login page


def test_healthz_ok_and_unhealthy(client):
    conn = MagicMock()
    with patch("app.db.get_conn") as gc:
        gc.return_value.__enter__.return_value = conn
        assert client.get("/healthz").get_json() == {"status": "ok"}
        conn.execute.side_effect = RuntimeError("down")
        resp = client.get("/healthz")
    assert resp.status_code == 503 and resp.get_json()["status"] == "database unavailable"


def test_trust_proxy_uses_forwarded_headers():
    with patch("app.db.ConnectionPool"):
        app = create_app({"TESTING": True, "SECRET_KEY": "x", "TRUST_PROXY": True, "BACKGROUND_JOBS": False})

    from app import auth
    auth.PUBLIC_ENDPOINTS.add("who")

    @app.get("/_who")
    def who():
        from flask import request
        return request.url_root

    resp = app.test_client().get("/_who", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "cards.example.com"})
    auth.PUBLIC_ENDPOINTS.discard("who")
    assert resp.get_data(as_text=True) == "https://cards.example.com/"


def test_packaging_files_exist_and_agree():
    assert (ROOT / "docker/entrypoint.sh").is_file()      # (the Dockerfile sets the executable bit itself: git on Windows drops it)
    compose = (ROOT / "docker-compose.truenas.yml").read_text()
    assert "pull_policy: always" in compose and "/data/uploads" in compose and "8000" in compose
    assert "init_db.py" in (ROOT / "docker/entrypoint.sh").read_text()
    assert "568" in (ROOT / "Dockerfile").read_text()
    assert "ghcr.io" in (ROOT / ".github/workflows/docker.yml").read_text()


def test_bootstrap_admin_creates_only_the_first_user():
    from scripts import bootstrap_admin as ba

    def run(users, name="admin", pw="longenough1"):
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [{"n": users}, {"id": 5}]
        with patch.object(ba.psycopg, "connect") as connect, patch.object(ba.coll_repo, "create", return_value="admin-s-cards") as create:
            connect.return_value.__enter__.return_value = conn
            return ba.bootstrap(name, pw), create

    msg, create = run(0)
    assert msg.startswith("Created admin user 'admin'") and create.called
    msg, create = run(2)
    assert "already exist" in msg and not create.called
    assert "No FIRST_ADMIN_USER" in run(0, "", "")[0]
    msg, create = run(0, pw="short")
    assert msg.startswith("Not creating") and not create.called
