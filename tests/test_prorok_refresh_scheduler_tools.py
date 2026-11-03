from prorok import prorok_refresh_all_dry_run_quiet as refresh_all


def test_scheduler_default_tools_use_provider_agnostic_web_search() -> None:
    tools = refresh_all.DEFAULT_TOOLS.split()

    assert tools == ["web_search", "web_fetch", "read"]
    assert "tavily_search" not in tools
    assert "tavily_extract" not in tools
