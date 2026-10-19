"""Security checks over every API route, so a new endpoint without protection fails the build."""
import re

import pytest

from app.main import app
from tests.conftest import PASSWORD, login, make_user

PUBLIC = {("POST", "/api/auth/login")}
SELF_SERVICE = {("POST", "/api/auth/logout"), ("POST", "/api/auth/password")}  # any signed-in user
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def api_routes():
    """Every API operation, read from the OpenAPI schema (independent of how routers are nested)."""
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/api/"):
            for method in operations:
                yield method.upper(), path


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


ROUTES = sorted(api_routes())


def test_routes_are_found():
    assert len(ROUTES) > 50  # all operations of all routers


@pytest.mark.parametrize("method, path", [r for r in ROUTES if r not in PUBLIC])
def test_every_route_requires_login(client, method, path):
    # Reads answer 401; changes are already stopped by the CSRF check (403) before the login check.
    expected = {401, 403} if method in MUTATING else {401}
    assert client.request(method, concrete(path)).status_code in expected, (method, path)


@pytest.mark.parametrize("method, path", [r for r in ROUTES if r[0] in MUTATING and r not in PUBLIC | SELF_SERVICE])
def test_viewer_cannot_change_anything(client, method, path):
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.request(method, concrete(path)).status_code == 403, (method, path)


@pytest.mark.parametrize("method, path", [r for r in ROUTES if r[0] in MUTATING and r not in PUBLIC])
def test_changes_require_csrf_token(client, method, path):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    del client.headers["X-CSRF-Token"]
    assert client.request(method, concrete(path)).status_code == 403, (method, path)


def test_security_headers_and_csp(client):
    r = client.get("/healthz")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_password_change_ends_other_sessions(client):
    from fastapi.testclient import TestClient

    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    with TestClient(app) as other:  # the same user signed in on another device
        login(other, "viewer@example.com")
        assert client.post("/api/auth/password", json={"current_password": "zle", "new_password": "nove-heslo-123"}
                           ).status_code == 400
        assert client.post("/api/auth/password", json={"current_password": PASSWORD,
                                                        "new_password": "nove-heslo-123"}).status_code == 204
        assert client.get("/api/auth/me").status_code == 200       # this session continues
        assert other.get("/api/auth/me").status_code == 401        # the other one ended
    client.post("/api/auth/logout")
    login(client, "viewer@example.com", "nove-heslo-123")


def test_admin_reset_and_deactivation_end_sessions(client):
    from fastapi.testclient import TestClient

    make_user("admin@example.com")
    viewer = make_user("viewer@example.com", role="viewer")
    login(client, "admin@example.com")
    with TestClient(app) as other:
        login(other, "viewer@example.com")
        client.patch(f"/api/users/{viewer.id}", json={"password": "reset-heslo-123"})
        assert other.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200


def test_unexpected_errors_hide_details(client, monkeypatch):
    make_user("admin@example.com")
    login(client, "admin@example.com")

    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr("app.api.comparison.comparison.build", boom)
    with TestClient_no_raise() as c:
        login(c, "admin@example.com")
        r = c.get("/api/comparison")
    assert r.status_code == 500 and "secret" not in r.text and "Neočakávaná chyba" in r.json()["detail"]


def TestClient_no_raise():
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)
