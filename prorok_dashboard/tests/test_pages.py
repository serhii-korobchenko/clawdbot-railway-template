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


def test_empty_status_means_all(logged_in_client):
    overview = logged_in_client.get("/?status=&q=Test")
    assert overview.status_code == 200
    assert "Test event" in overview.text

    partial = logged_in_client.get("/partials/events?status=&q=Test")
    assert partial.status_code == 200
    assert "Test event" in partial.text


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


def test_overview_shows_evidence_activity_chart(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert "Активність по подіях" in response.text
    assert "Official evidence, зафіксовані за останні 7 днів." in response.text
    assert "7 днів" in response.text
    assert "30 днів" in response.text
    assert "Весь час" in response.text
    assert 'id="activity-chart-data"' in response.text
    assert 'id="evidence-activity-chart"' in response.text
    assert 'class="activity-mobile-list"' in response.text
    assert 'class="activity-mobile-number">1<' in response.text
    assert "1 evidence" in response.text
    assert "🟢 1" in response.text
    assert "Останнє:" in response.text
    assert "Test event" in response.text

    script = logged_in_client.get("/static/js/dashboard.js")
    assert script.status_code == 200
    assert "renderEvidenceActivityChart" in script.text
    assert 'indexAxis: "y"' in script.text
    assert 'window.matchMedia("(max-width: 760px)")' in script.text
    assert "return String(index + 1)" in script.text
    assert "this.getLabelForValue(value)" in script.text
    assert 'label: "🟢 Indicator"' in script.text
    assert 'label: "🔴 Counterindicator"' in script.text
    assert 'stack: "activity"' in script.text
    assert "Останнє evidence" in script.text
    assert "Кількість official evidence" in script.text


def test_evidence_activity_window_selector(logged_in_client):
    response = logged_in_client.get("/?activity_window=30d&status=active&q=Test")
    assert response.status_code == 200
    assert "Official evidence, зафіксовані за останні 30 днів." in response.text
    assert 'name="activity_window" value="30d"' in response.text


def test_invalid_evidence_activity_window_is_422(logged_in_client):
    assert logged_in_client.get("/?activity_window=365d").status_code == 422
