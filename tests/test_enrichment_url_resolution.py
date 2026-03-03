from applypilot.enrichment import detail


def test_resolve_url_uses_config_base_url_for_reed() -> None:
    got = detail.resolve_url("/jobs/data-analyst/56505883", "Reed")
    assert got == "https://www.reed.co.uk/jobs/data-analyst/56505883"


def test_resolve_url_falls_back_to_site_registry_when_base_missing(monkeypatch) -> None:
    monkeypatch.setattr(detail, "_load_base_urls", lambda: {})
    monkeypatch.setattr(
        detail.config,
        "load_sites_config",
        lambda: {
            "sites": [
                {
                    "name": "Example Board",
                    "url": "https://jobs.example.com/search?q={query_encoded}&l={location_encoded}",
                    "type": "search",
                }
            ]
        },
    )

    got = detail.resolve_url("/role/123", "Example Board")
    assert got == "https://jobs.example.com/role/123"
