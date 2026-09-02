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


def test_htmx_filter_state_contract(logged_in_client):
    response = logged_in_client.get("/?status=active&q=Test")
    assert response.status_code == 200
    assert 'data-status="archived"' in response.text
    assert 'hx-include="#event-search"' in response.text
    assert 'id="status-field" type="hidden" name="status" value="active"' in response.text

    script = logged_in_client.get("/static/js/dashboard.js")
    assert script.status_code == 200
    assert "statusField.value = status" in script.text
    assert "window.history.pushState" in script.text
    assert "window.history.replaceState" in script.text


def test_static_assets_use_origin_relative_urls(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert 'href="/static/css/app.css"' in response.text
    assert 'src="/static/js/dashboard.js"' in response.text
    assert 'href="http://testserver/static/css/app.css"' not in response.text
    assert 'src="http://testserver/static/js/dashboard.js"' not in response.text


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
