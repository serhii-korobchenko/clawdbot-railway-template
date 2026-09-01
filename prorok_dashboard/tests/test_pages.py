def test_overview_and_empty_assessment(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert "Test event" in response.text
    assert "40%" in response.text
    assert "Оцінки ще немає" in response.text
    assert "secret-upstream-token" not in response.text


def test_status_filter_and_search(logged_in_client):
    archived = logged_in_client.get("/?status=archived")
    assert archived.status_code == 200
    assert "No assessment" in archived.text

    search = logged_in_client.get("/?q=Test")
    assert search.status_code == 200
    assert "Test event" in search.text


def test_detail_contains_required_sections(logged_in_client):
    response = logged_in_client.get("/events/event-1")
    assert response.status_code == 200
    assert "Історія оцінок" in response.text
    assert "Evidence timeline" in response.text
    assert "Evidence summary" in response.text
    assert "Подія відбувається, якщо" in response.text
    assert "40%" in response.text


def test_unknown_event_is_404(logged_in_client):
    response = logged_in_client.get("/events/missing")
    assert response.status_code == 404
    assert "Подію не знайдено" in response.text
