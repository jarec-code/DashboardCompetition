def test_healthz(client):
    assert client.get("/healthz").text == "ok"


def test_readyz_checks_database(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_security_headers(client):
    r = client.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_unknown_api_path_is_404_not_spa(client):
    assert client.get("/api/neexistuje").status_code == 404
