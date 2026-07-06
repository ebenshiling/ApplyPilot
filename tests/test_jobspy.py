from applypilot.discovery import jobspy
from applypilot.discovery.jobspy import _normalize_search_cfg, run_discovery


def test_normalize_search_cfg_derives_location_labels_from_location_values() -> None:
    cfg = {
        "locations": [
            {"location": "London, UK", "remote": False},
            {"location": "Remote", "remote": True},
        ]
    }

    normalized = _normalize_search_cfg(cfg)

    assert normalized["location_labels"] == ["London, UK", "Remote"]
    assert normalized["locations"] == [
        {"location": "London, UK", "remote": False, "label": "London, UK"},
        {"location": "Remote", "remote": True, "label": "Remote"},
    ]


def test_normalize_search_cfg_preserves_existing_location_labels() -> None:
    cfg = {
        "location_labels": ["Remote"],
        "locations": [
            {"label": "London", "location": "London, UK", "remote": False},
            {"location": "Remote", "remote": True},
        ],
    }

    normalized = _normalize_search_cfg(cfg)

    assert normalized["location_labels"] == ["Remote"]
    assert normalized["locations"] == [
        {"label": "London", "location": "London, UK", "remote": False},
        {"location": "Remote", "remote": True, "label": "Remote"},
    ]


def test_normalize_search_cfg_derives_board_override_location_labels() -> None:
    cfg = {
        "board_overrides": {
            "Indeed": {
                "locations": [
                    {"location": "London, UK"},
                    {"location": "Remote"},
                ]
            }
        }
    }

    normalized = _normalize_search_cfg(cfg)

    assert normalized["board_overrides"] == {
        "indeed": {
            "locations": [
                {"location": "London, UK"},
                {"location": "Remote"},
            ],
            "location_labels": ["London, UK", "Remote"],
        }
    }


def test_run_discovery_applies_board_overrides(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(jobspy.config, "load_profile", lambda: {})
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **kwargs: calls.append(kwargs) or {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 1})

    run_discovery(
        {
            "sites": ["indeed", "linkedin"],
            "queries": [
                {"query": "IT Support Analyst", "tier": 1},
                {"query": "Application Support Engineer", "tier": 1},
            ],
            "locations": [
                {"location": "London, UK", "remote": False},
                {"location": "Remote", "remote": True},
            ],
            "defaults": {"results_per_site": 40, "hours_old": 168},
            "board_overrides": {
                "indeed": {
                    "queries": ["IT Support Analyst"],
                    "location_labels": ["Remote"],
                    "results_per_site": 10,
                    "hours_old": 72,
                }
            },
        }
    )

    assert len(calls) == 2
    indeed_call = next(call for call in calls if call["sites"] == ["indeed"])
    linkedin_call = next(call for call in calls if call["sites"] == ["linkedin"])

    assert indeed_call["results_per_site"] == 10
    assert indeed_call["hours_old"] == 72
    assert indeed_call["locations"] == ["Remote"]
    assert indeed_call["search_cfg"]["queries"] == [{"query": "IT Support Analyst", "tier": 1}]

    assert linkedin_call["results_per_site"] == 40
    assert linkedin_call["hours_old"] == 168
    assert linkedin_call["locations"] == ["London, UK", "Remote"]
    assert linkedin_call["search_cfg"]["queries"] == [
        {"query": "IT Support Analyst", "tier": 1},
        {"query": "Application Support Engineer", "tier": 1},
    ]
