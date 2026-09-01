def test_api_authentication(client, auth_headers):
    assert client.get("/api/v1/events", headers=auth_headers).status_code == 200
    assert client.get("/api/v1/events").status_code == 401
    assert client.get(
        "/api/v1/events",
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401
