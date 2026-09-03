def test_security_headers(logged_in_client):
    response = logged_in_client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_health_and_readiness(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
