def test_latest_refresh_card_renders_decision_summary(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert "Останнє оновлення" in response.text
    assert "Refresh #10 · completed" in response.text
    assert "Нових candidate evidence" in response.text
    assert "Рекомендація:" in response.text
    assert "60%" in response.text
    assert "70%" in response.text
    assert "Рішення користувача:" in response.text
    assert "рекомендацію прийнято" in response.text
    assert "Official forecast:" in response.text
