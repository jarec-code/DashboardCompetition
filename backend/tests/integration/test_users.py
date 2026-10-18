from tests.conftest import login, make_user

NEW_USER = {"email": "novy@example.com", "password": "dlhe-heslo-456", "role": "viewer"}


def test_viewer_cannot_manage_users(client):
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/users", json=NEW_USER).status_code == 403


def test_admin_creates_user_and_new_user_can_log_in(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    r = client.post("/api/users", json=NEW_USER)
    assert r.status_code == 201
    assert r.json()["role"] == "viewer"
    assert client.post("/api/users", json=NEW_USER).status_code == 409
    assert [u["email"] for u in client.get("/api/users").json()] == ["admin@example.com", "novy@example.com"]

    client.post("/api/auth/logout")
    login(client, "novy@example.com", "dlhe-heslo-456")


def test_state_change_requires_csrf(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/users", json=NEW_USER).status_code == 403
    client.headers["X-CSRF-Token"] = "zly-token"
    assert client.post("/api/users", json=NEW_USER).status_code == 403


def test_short_password_rejected(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    r = client.post("/api/users", json={**NEW_USER, "password": "kratke"})
    assert r.status_code == 422


def test_last_admin_cannot_be_demoted(client):
    admin = make_user("admin@example.com")
    login(client, "admin@example.com")
    assert client.patch(f"/api/users/{admin.id}", json={"role": "viewer"}).status_code == 409
    assert client.patch(f"/api/users/{admin.id}", json={"is_active": False}).status_code == 409

    other = make_user("druhy@example.com")
    assert client.patch(f"/api/users/{other.id}", json={"role": "viewer"}).json()["role"] == "viewer"
