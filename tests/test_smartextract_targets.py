from applypilot.discovery.smartextract import _normalize_selector_for_bs4, build_scrape_targets


def test_build_scrape_targets_omits_generic_location_for_flagged_site() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
        }
    ]
    search_cfg = {
        "queries": [{"query": "Data Analyst"}],
        "locations": [{"location": "United Kingdom"}],
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 1
    assert targets[0]["url"].endswith("keyword=Data+Analyst&location=")


def test_build_scrape_targets_keeps_specific_location_for_flagged_site() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
        }
    ]
    search_cfg = {
        "queries": [{"query": "Data Analyst"}],
        "locations": [{"location": "London"}],
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 1
    assert targets[0]["url"].endswith("keyword=Data+Analyst&location=London")


def test_normalize_selector_for_bs4_upgrades_deprecated_contains() -> None:
    selector = 'ul.search-result-details > li:contains("PS")'
    normalized = _normalize_selector_for_bs4(selector)
    assert normalized == 'ul.search-result-details > li:-soup-contains("PS")'
