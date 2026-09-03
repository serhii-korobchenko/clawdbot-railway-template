def test_root_requires_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_and_logout(client):
    wrong = client.post("/login", data={"password": "wrong"})
    assert wrong.status_code == 401

    ok = client.post("/login", data={"password": "dashboard-password"}, follow_redirects=False)
    assert ok.status_code == 303

    root = client.get("/")
    assert root.status_code == 200

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303

    root_after = client.get("/", follow_redirects=False)
    assert root_after.status_code == 303
