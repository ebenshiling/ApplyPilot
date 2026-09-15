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


def test_extract_with_llm_keeps_page_text_when_structured_response_fails(monkeypatch) -> None:
    page_text = "<main><h1>Application Support Specialist</h1><p>" + ("Useful job description text. " * 12) + "</p></main>"

    monkeypatch.setattr(detail, "extract_main_content", lambda page: page_text)
    monkeypatch.setattr(detail, "structured_json", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad JSON")))

    result = detail.extract_with_llm(object(), "https://jobs.example.com/role/123")

    assert result["full_description"]
    assert "Useful job description text." in result["full_description"]
