def test_list_filters_search_and_counts(client, auth_headers):
    response = client.get("/api/v1/events", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["status_counts"] == {
        "active": 1,
        "paused": 0,
        "resolved": 0,
        "archived": 1,
    }

    active = client.get(
        "/api/v1/events?status=active",
        headers=auth_headers,
    ).json()
    assert active["filtered_total"] == 1
    assert active["items"][0]["event_id"] == "active_event"

    archived = client.get(
        "/api/v1/events?status=archived",
        headers=auth_headers,
    ).json()
    assert archived["items"][0]["current_assessment"] is None

    search = client.get(
        "/api/v1/events?q=nuclear",
        headers=auth_headers,
    ).json()
    assert [item["event_id"] for item in search["items"]] == ["active_event"]

    tag_search = client.get(
        "/api/v1/events?q=legacy",
        headers=auth_headers,
    ).json()
    assert [item["event_id"] for item in tag_search["items"]] == ["archived_empty"]


def test_invalid_status_is_422(client, auth_headers):
    response = client.get(
        "/api/v1/events?status=invalid",
        headers=auth_headers,
    )
    assert response.status_code == 422
