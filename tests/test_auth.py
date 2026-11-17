def test_login_flow(client, stub):
    assert client.get("/login").status_code == 200
    bad = client.post("/login", data={"username": "ash", "password": "wrong"})
    assert bad.status_code == 401
    ok = client.post("/login", data={"username": "ash", "password": "correct horse"})
    assert ok.status_code == 302


def test_open_redirect_blocked(client, stub):
    resp = client.post("/login?next=//evil.com", data={"username": "ash", "password": "correct horse"})
    assert "evil.com" not in resp.headers["Location"]


def test_admin_only(client, login):
    login()
    assert client.get("/admin/users").status_code == 403
