from prorok.prorok_candidate_aggregation import aggregate_same_source_candidates


def candidate(url, direction, strength="medium", relevance=80, credibility=90, summary="fact"):
    return {
        "url": url,
        "direction": direction,
        "strength": strength,
        "relevance": relevance,
        "credibility": credibility,
        "summary": summary,
        "why_it_matters": summary,
    }


def test_same_canonical_url_becomes_one_signal():
    rows = aggregate_same_source_candidates([
        candidate("https://example.com/story?utm_source=x", "indicator", summary="for"),
        candidate("https://example.com/story", "counterindicator", summary="against"),
    ])
    assert len(rows) == 1
    assert rows[0]["source_component_count"] == 2
    assert len(rows[0]["source_components"]) == 2
    assert "for" in rows[0]["summary"] and "against" in rows[0]["summary"]


def test_conflicting_equal_weight_source_is_neutral():
    rows = aggregate_same_source_candidates([
        candidate("https://example.com/story", "indicator"),
        candidate("https://example.com/story", "counterindicator"),
    ])
    assert rows[0]["direction"] == "neutral"


def test_stronger_weight_controls_net_direction():
    rows = aggregate_same_source_candidates([
        candidate("https://example.com/story", "indicator", strength="strong", relevance=90, credibility=90),
        candidate("https://example.com/story", "counterindicator", strength="weak", relevance=60, credibility=80),
    ])
    assert rows[0]["direction"] == "indicator"


def test_different_urls_remain_independent_signals():
    rows = aggregate_same_source_candidates([
        candidate("https://example.com/a", "indicator"),
        candidate("https://example.com/b", "counterindicator"),
    ])
    assert len(rows) == 2
