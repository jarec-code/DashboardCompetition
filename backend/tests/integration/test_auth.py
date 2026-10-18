from tests.conftest import PASSWORD, login, make_user


def test_login_me_logout(client):
    make_user("admin@example.com")
    token = login(client, "Admin@Example.com ")  # e-mail is normalized
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "admin@example.com"
    assert r.json()["csrf_token"] == token

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_unknown_email_and_inactive_user(client):
    make_user("admin@example.com")
    make_user("off@example.com", active=False)
    for email, password in (("admin@example.com", "zle-heslo"), ("nikto@example.com", PASSWORD),
                            ("off@example.com", PASSWORD)):
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 401
        assert r.json()["detail"] == "Nesprávny e-mail alebo heslo."


def test_me_requires_login(client):
    assert client.get("/api/auth/me").status_code == 401


def test_logout_requires_csrf(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/auth/logout").status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_login_rate_limit(client):
    make_user("admin@example.com")
    for _ in range(10):
        client.post("/api/auth/login", json={"email": "admin@example.com", "password": "zle-heslo"})
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert r.status_code == 429


def test_deactivated_user_loses_session(client, db):
    user = make_user("admin@example.com")
    login(client, "admin@example.com")
    db.get(type(user), user.id).is_active = False
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_session_cookie_flags(client):
    make_user("admin@example.com")
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
