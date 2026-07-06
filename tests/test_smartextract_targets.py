from applypilot.discovery import smartextract
from applypilot.discovery.smartextract import (
    _balanced_target_window,
    _dedupe_site_query_location_twins,
    _extract_jobs_with_selectors,
    _location_ok,
    _matches_exclude_titles,
    _normalize_selector_for_bs4,
    _obvious_irrelevant_api_reason,
    _salary_text_looks_valid,
    _title_matches_query,
    build_scrape_targets,
)


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


def test_build_scrape_targets_skips_disabled_sites() -> None:
    sites = [
        {
            "name": "GOV.UK Find a job",
            "disabled": True,
            "url": "https://findajob.dwp.gov.uk/search?q={query_encoded}",
            "type": "search",
        },
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}",
            "type": "search",
        },
    ]
    search_cfg = {"queries": [{"query": "Data Analyst"}]}

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 1
    assert targets[0]["name"] == "NHS Jobs"


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


def test_build_scrape_targets_can_trim_location_to_first_segment() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [{"query": "Data Analyst"}],
        "locations": [{"location": "London, UK"}],
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 1
    assert targets[0]["url"].endswith("keyword=Data+Analyst&location=London")


def test_build_scrape_targets_expands_all_configured_locations() -> None:
    sites = [
        {
            "name": "Jobs Go Public",
            "url": "https://www.jobsgopublic.com/jobs?keywords={query_encoded}&location={location_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [{"query": "IT Support Analyst"}, {"query": "Service Desk Analyst"}],
        "locations": [{"location": "London, UK"}, {"location": "Manchester, UK"}],
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 4
    assert [t["location"] for t in targets] == [
        "London, UK",
        "Manchester, UK",
        "Manchester, UK",
        "London, UK",
    ]
    assert [t["query"] for t in targets] == [
        "IT Support Analyst",
        "Service Desk Analyst",
        "IT Support Analyst",
        "Service Desk Analyst",
    ]


def test_build_scrape_targets_can_prune_nhs_location_specific_tier_two_queries() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
            "omit_generic_location": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Desktop Support Analyst", "tier": 2},
            {"query": "Data Analyst", "tier": 2},
            {"query": "Application Support Engineer", "tier": 1},
        ],
        "locations": [
            {"location": "London, UK"},
            {"location": "Remote"},
        ],
        "site_tier_location_pruning": {
            "NHS Jobs": [
                {"min_tier": 2, "location_required": True, "keep_queries": ["Data Analyst"]},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["query"], t["location"]) for t in targets}
    assert ("Desktop Support Analyst", "London") not in combos
    assert ("Desktop Support Analyst", "") in combos
    assert ("Data Analyst", "London") in combos
    assert ("Application Support Engineer", "London") in combos


def test_build_scrape_targets_does_not_expand_locations_without_placeholder() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}&JobSearch_re_0=1",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [{"query": "IT Support Analyst"}, {"query": "Service Desk Analyst"}],
        "locations": [{"location": "London, UK"}, {"location": "Manchester, UK"}],
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 2
    assert [t["query"] for t in targets] == ["IT Support Analyst", "Service Desk Analyst"]
    assert [t["location"] for t in targets] == ["", ""]


def test_build_scrape_targets_deduplicates_prepared_locations_per_site() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [{"query": "Data Analyst"}],
        "locations": [{"location": "London, UK"}, {"location": "London"}],
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 1
    assert targets[0]["location"] == "London"


def test_build_scrape_targets_can_exclude_locations_for_specific_site() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [{"query": "IT Support Analyst"}],
        "locations": [
            {"location": "London, UK"},
            {"location": "Cardiff, UK"},
            {"location": "Belfast, UK"},
        ],
        "site_location_exclusions": {"NHS Jobs": ["Cardiff", "Belfast"]},
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert len(targets) == 1
    assert targets[0]["location"] == "London"


def test_build_scrape_targets_can_exclude_queries_for_specific_site() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Systems Support Analyst"},
            {"query": "Service Desk Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {"HealthJobsUK": ["Systems Support Analyst"]},
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("HealthJobsUK", "Service Desk Analyst"),
    ]


def test_build_scrape_targets_can_exclude_multiple_queries_for_specific_site() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Business Application Support Analyst"},
            {"query": "Technical Systems Analyst"},
            {"query": "Service Desk Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "HealthJobsUK": [
                "Business Application Support Analyst",
                "Technical Systems Analyst",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("HealthJobsUK", "Service Desk Analyst"),
    ]


def test_build_scrape_targets_can_exclude_larger_healthjobsuk_query_blocklist() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Product Support Engineer"},
            {"query": "Platform Support Engineer"},
            {"query": "Desktop Support Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "HealthJobsUK": [
                "Product Support Engineer",
                "Platform Support Engineer",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("HealthJobsUK", "Desktop Support Analyst"),
    ]


def test_build_scrape_targets_can_exclude_healthjobsuk_desktop_and_data_queries() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Desktop Support Analyst"},
            {"query": "Data Analyst"},
            {"query": "1st Line Support Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "HealthJobsUK": [
                "Desktop Support Analyst",
                "Data Analyst",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("HealthJobsUK", "1st Line Support Analyst"),
    ]


def test_build_scrape_targets_can_exclude_healthjobsuk_line_support_queries() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "1st Line Support Analyst"},
            {"query": "2nd Line Support Analyst"},
            {"query": "Helpdesk Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "HealthJobsUK": [
                "1st Line Support Analyst",
                "2nd Line Support Analyst",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("HealthJobsUK", "Helpdesk Analyst"),
    ]


def test_build_scrape_targets_can_exclude_healthjobsuk_helpdesk_and_cloud_queries() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Helpdesk Analyst"},
            {"query": "Cloud Support Engineer"},
            {"query": "Infrastructure Support Engineer"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "HealthJobsUK": [
                "Helpdesk Analyst",
                "Cloud Support Engineer",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("HealthJobsUK", "Infrastructure Support Engineer"),
    ]


def test_build_scrape_targets_can_exclude_healthjobsuk_infra_and_erp_queries() -> None:
    sites = [
        {
            "name": "HealthJobsUK",
            "url": "https://www.healthjobsuk.com/job_list?JobSearch_q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Infrastructure Support Engineer"},
            {"query": "ERP Support Analyst"},
            {"query": "Business Systems Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "HealthJobsUK": [
                "Infrastructure Support Engineer",
                "ERP Support Analyst",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("HealthJobsUK", "Business Systems Analyst"),
    ]


def test_build_scrape_targets_can_exclude_govuk_query() -> None:
    sites = [
        {
            "name": "GOV.UK Find a job",
            "url": "https://findajob.dwp.gov.uk/search?q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Specialist"},
            {"query": "Business Application Support Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "GOV.UK Find a job": [
                "Application Support Specialist",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("GOV.UK Find a job", "Business Application Support Analyst"),
    ]


def test_build_scrape_targets_can_exclude_multiple_govuk_queries() -> None:
    sites = [
        {
            "name": "GOV.UK Find a job",
            "url": "https://findajob.dwp.gov.uk/search?q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Specialist"},
            {"query": "Product Support Engineer"},
            {"query": "Technical Systems Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "GOV.UK Find a job": [
                "Application Support Specialist",
                "Product Support Engineer",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("GOV.UK Find a job", "Technical Systems Analyst"),
    ]


def test_build_scrape_targets_can_exclude_business_and_platform_govuk_queries() -> None:
    sites = [
        {
            "name": "GOV.UK Find a job",
            "url": "https://findajob.dwp.gov.uk/search?q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Business Application Support Analyst"},
            {"query": "Platform Support Engineer"},
            {"query": "Technical Systems Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "GOV.UK Find a job": [
                "Business Application Support Analyst",
                "Platform Support Engineer",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("GOV.UK Find a job", "Technical Systems Analyst"),
    ]


def test_build_scrape_targets_can_exclude_technical_systems_analyst_govuk_query() -> None:
    sites = [
        {
            "name": "GOV.UK Find a job",
            "url": "https://findajob.dwp.gov.uk/search?q={query_encoded}",
            "type": "search",
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Systems Analyst"},
            {"query": "Desktop Support Analyst"},
        ],
        "locations": [{"location": "London, UK"}],
        "site_query_exclusions": {
            "GOV.UK Find a job": [
                "Technical Systems Analyst",
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert [(t["name"], t["query"]) for t in targets] == [
        ("GOV.UK Find a job", "Desktop Support Analyst"),
    ]


def test_build_scrape_targets_can_exclude_exact_query_location_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Specialist"},
            {"query": "Service Desk Analyst"},
        ],
        "locations": [
            {"location": "Bristol, UK"},
            {"location": "Leeds, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Application Support Specialist", "location": "Bristol"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    assert ("NHS Jobs", "Application Support Specialist", "Bristol") not in {
        (t["name"], t["query"], t["location"]) for t in targets
    }
    assert ("NHS Jobs", "Service Desk Analyst", "Bristol") in {(t["name"], t["query"], t["location"]) for t in targets}


def test_build_scrape_targets_can_exclude_multiple_exact_query_location_slots() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Business Application Support Analyst"},
            {"query": "Technical Systems Analyst"},
        ],
        "locations": [
            {"location": "Edinburgh, UK"},
            {"location": "Glasgow, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Business Application Support Analyst", "location": "Edinburgh"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Business Application Support Analyst", "Edinburgh") not in combos
    assert ("NHS Jobs", "Business Application Support Analyst", "Glasgow") in combos
    assert ("NHS Jobs", "Technical Systems Analyst", "Edinburgh") in combos


def test_build_scrape_targets_can_exclude_technical_systems_analyst_glasgow_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Systems Analyst"},
            {"query": "Desktop Support Analyst"},
        ],
        "locations": [
            {"location": "Glasgow, UK"},
            {"location": "London, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Technical Systems Analyst", "location": "Glasgow"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Technical Systems Analyst", "Glasgow") not in combos
    assert ("NHS Jobs", "Technical Systems Analyst", "London") in combos
    assert ("NHS Jobs", "Desktop Support Analyst", "Glasgow") in combos


def test_build_scrape_targets_can_exclude_blank_location_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Product Support Engineer"},
            {"query": "Technical Support Analyst"},
        ],
        "locations": [{"location": "Remote"}],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Product Support Engineer", "location": ""},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Product Support Engineer", "") not in combos
    assert ("NHS Jobs", "Technical Support Analyst", "") in combos


def test_build_scrape_targets_can_exclude_platform_support_engineer_london_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Platform Support Engineer"},
            {"query": "Desktop Support Analyst"},
        ],
        "locations": [
            {"location": "London, UK"},
            {"location": "Manchester, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Platform Support Engineer", "location": "London"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Platform Support Engineer", "London") not in combos
    assert ("NHS Jobs", "Platform Support Engineer", "Manchester") in combos
    assert ("NHS Jobs", "Desktop Support Analyst", "London") in combos


def test_build_scrape_targets_can_exclude_desktop_support_analyst_manchester_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Desktop Support Analyst"},
            {"query": "Data Analyst"},
        ],
        "locations": [
            {"location": "Manchester, UK"},
            {"location": "Birmingham, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Desktop Support Analyst", "location": "Manchester"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Desktop Support Analyst", "Manchester") not in combos
    assert ("NHS Jobs", "Desktop Support Analyst", "Birmingham") in combos
    assert ("NHS Jobs", "Data Analyst", "Manchester") in combos


def test_build_scrape_targets_can_exclude_data_analyst_birmingham_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Data Analyst"},
            {"query": "Helpdesk Analyst"},
        ],
        "locations": [
            {"location": "Birmingham, UK"},
            {"location": "Leeds, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Data Analyst", "location": "Birmingham"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Data Analyst", "Birmingham") not in combos
    assert ("NHS Jobs", "Data Analyst", "Leeds") in combos
    assert ("NHS Jobs", "Helpdesk Analyst", "Birmingham") in combos


def test_build_scrape_targets_can_exclude_first_line_support_analyst_leeds_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "1st Line Support Analyst"},
            {"query": "2nd Line Support Analyst"},
        ],
        "locations": [
            {"location": "Leeds, UK"},
            {"location": "Bristol, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "1st Line Support Analyst", "location": "Leeds"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "1st Line Support Analyst", "Leeds") not in combos
    assert ("NHS Jobs", "1st Line Support Analyst", "Bristol") in combos
    assert ("NHS Jobs", "2nd Line Support Analyst", "Leeds") in combos


def test_build_scrape_targets_can_exclude_second_line_support_analyst_bristol_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "2nd Line Support Analyst"},
            {"query": "Helpdesk Analyst"},
        ],
        "locations": [
            {"location": "Bristol, UK"},
            {"location": "Edinburgh, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "2nd Line Support Analyst", "location": "Bristol"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "2nd Line Support Analyst", "Bristol") not in combos
    assert ("NHS Jobs", "2nd Line Support Analyst", "Edinburgh") in combos
    assert ("NHS Jobs", "Helpdesk Analyst", "Bristol") in combos


def test_build_scrape_targets_can_exclude_helpdesk_analyst_edinburgh_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Helpdesk Analyst"},
            {"query": "Cloud Support Engineer"},
        ],
        "locations": [
            {"location": "Edinburgh, UK"},
            {"location": "Glasgow, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Helpdesk Analyst", "location": "Edinburgh"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Helpdesk Analyst", "Edinburgh") not in combos
    assert ("NHS Jobs", "Helpdesk Analyst", "Glasgow") in combos
    assert ("NHS Jobs", "Cloud Support Engineer", "Edinburgh") in combos


def test_build_scrape_targets_can_exclude_cloud_support_engineer_glasgow_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Cloud Support Engineer"},
            {"query": "Infrastructure Support Engineer"},
        ],
        "locations": [
            {"location": "Glasgow, UK"},
            {"location": "London, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Cloud Support Engineer", "location": "Glasgow"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Cloud Support Engineer", "Glasgow") not in combos
    assert ("NHS Jobs", "Cloud Support Engineer", "London") in combos
    assert ("NHS Jobs", "Infrastructure Support Engineer", "Glasgow") in combos


def test_build_scrape_targets_can_exclude_erp_support_analyst_london_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "ERP Support Analyst"},
            {"query": "Infrastructure Support Engineer"},
        ],
        "locations": [
            {"location": "London, UK"},
            {"location": "Leeds, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "ERP Support Analyst", "location": "London"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "ERP Support Analyst", "London") not in combos
    assert ("NHS Jobs", "ERP Support Analyst", "Leeds") in combos
    assert ("NHS Jobs", "Infrastructure Support Engineer", "London") in combos


def test_build_scrape_targets_can_exclude_it_support_analyst_manchester_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "IT Support Analyst"},
            {"query": "Service Desk Analyst"},
        ],
        "locations": [
            {"location": "Manchester, UK"},
            {"location": "Bristol, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "IT Support Analyst", "location": "Manchester"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "IT Support Analyst", "Manchester") not in combos
    assert ("NHS Jobs", "IT Support Analyst", "Bristol") in combos
    assert ("NHS Jobs", "Service Desk Analyst", "Manchester") in combos


def test_build_scrape_targets_can_exclude_application_support_engineer_birmingham_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Engineer"},
            {"query": "Technical Support Engineer"},
        ],
        "locations": [
            {"location": "Birmingham, UK"},
            {"location": "Leeds, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Application Support Engineer", "location": "Birmingham"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Application Support Engineer", "Birmingham") not in combos
    assert ("NHS Jobs", "Application Support Engineer", "Leeds") in combos
    assert ("NHS Jobs", "Technical Support Engineer", "Birmingham") in combos


def test_build_scrape_targets_can_exclude_blank_location_infrastructure_support_engineer_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Infrastructure Support Engineer"},
            {"query": "Data Analyst"},
        ],
        "locations": [{"location": "Remote"}],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Infrastructure Support Engineer", "location": ""},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Infrastructure Support Engineer", "") not in combos
    assert ("NHS Jobs", "Data Analyst", "") in combos


def test_build_scrape_targets_can_exclude_technical_support_engineer_leeds_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Support Engineer"},
            {"query": "Service Desk Analyst"},
        ],
        "locations": [
            {"location": "Leeds, UK"},
            {"location": "Bristol, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Technical Support Engineer", "location": "Leeds"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Technical Support Engineer", "Leeds") not in combos
    assert ("NHS Jobs", "Technical Support Engineer", "Bristol") in combos
    assert ("NHS Jobs", "Service Desk Analyst", "Leeds") in combos


def test_build_scrape_targets_can_exclude_service_desk_analyst_bristol_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Service Desk Analyst"},
            {"query": "Application Support Analyst"},
        ],
        "locations": [
            {"location": "Bristol, UK"},
            {"location": "Glasgow, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Service Desk Analyst", "location": "Bristol"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Service Desk Analyst", "Bristol") not in combos
    assert ("NHS Jobs", "Service Desk Analyst", "Glasgow") in combos
    assert ("NHS Jobs", "Application Support Analyst", "Bristol") in combos


def test_build_scrape_targets_can_exclude_systems_support_analyst_edinburgh_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Systems Support Analyst"},
            {"query": "Application Support Analyst"},
        ],
        "locations": [
            {"location": "Edinburgh, UK"},
            {"location": "Glasgow, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Systems Support Analyst", "location": "Edinburgh"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Systems Support Analyst", "Edinburgh") not in combos
    assert ("NHS Jobs", "Systems Support Analyst", "Glasgow") in combos
    assert ("NHS Jobs", "Application Support Analyst", "Edinburgh") in combos


def test_build_scrape_targets_can_exclude_application_support_analyst_glasgow_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Analyst"},
            {"query": "Production Support Engineer"},
        ],
        "locations": [
            {"location": "Glasgow, UK"},
            {"location": "London, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Application Support Analyst", "location": "Glasgow"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Application Support Analyst", "Glasgow") not in combos
    assert ("NHS Jobs", "Application Support Analyst", "London") in combos
    assert ("NHS Jobs", "Production Support Engineer", "Glasgow") in combos


def test_build_scrape_targets_can_exclude_technical_support_analyst_london_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Support Analyst"},
            {"query": "IT Service Desk Analyst"},
        ],
        "locations": [
            {"location": "London, UK"},
            {"location": "Manchester, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Technical Support Analyst", "location": "London"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Technical Support Analyst", "London") not in combos
    assert ("NHS Jobs", "Technical Support Analyst", "Manchester") in combos
    assert ("NHS Jobs", "IT Service Desk Analyst", "London") in combos


def test_build_scrape_targets_can_exclude_blank_location_production_support_engineer_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Production Support Engineer"},
            {"query": "Technical Support Analyst"},
        ],
        "locations": [{"location": "Remote"}],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Production Support Engineer", "location": ""},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Production Support Engineer", "") not in combos
    assert ("NHS Jobs", "Technical Support Analyst", "") in combos


def test_build_scrape_targets_can_exclude_it_service_desk_analyst_manchester_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "IT Service Desk Analyst"},
            {"query": "IT Support Engineer"},
        ],
        "locations": [
            {"location": "Manchester, UK"},
            {"location": "Birmingham, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "IT Service Desk Analyst", "location": "Manchester"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "IT Service Desk Analyst", "Manchester") not in combos
    assert ("NHS Jobs", "IT Service Desk Analyst", "Birmingham") in combos
    assert ("NHS Jobs", "IT Support Engineer", "Manchester") in combos


def test_build_scrape_targets_can_exclude_it_support_engineer_birmingham_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "IT Support Engineer"},
            {"query": "Software Support Engineer"},
        ],
        "locations": [
            {"location": "Birmingham, UK"},
            {"location": "Leeds, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "IT Support Engineer", "location": "Birmingham"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "IT Support Engineer", "Birmingham") not in combos
    assert ("NHS Jobs", "IT Support Engineer", "Leeds") in combos
    assert ("NHS Jobs", "Software Support Engineer", "Birmingham") in combos


def test_build_scrape_targets_can_exclude_software_support_engineer_leeds_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Software Support Engineer"},
            {"query": "Technical Application Support Engineer"},
        ],
        "locations": [
            {"location": "Leeds, UK"},
            {"location": "Bristol, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Software Support Engineer", "location": "Leeds"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Software Support Engineer", "Leeds") not in combos
    assert ("NHS Jobs", "Software Support Engineer", "Bristol") in combos
    assert ("NHS Jobs", "Technical Application Support Engineer", "Leeds") in combos


def test_build_scrape_targets_can_exclude_technical_application_support_engineer_bristol_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Application Support Engineer"},
            {"query": "Application Support Specialist"},
        ],
        "locations": [
            {"location": "Bristol, UK"},
            {"location": "Edinburgh, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Technical Application Support Engineer", "location": "Bristol"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Technical Application Support Engineer", "Bristol") not in combos
    assert ("NHS Jobs", "Technical Application Support Engineer", "Edinburgh") in combos
    assert ("NHS Jobs", "Application Support Specialist", "Bristol") in combos


def test_build_scrape_targets_can_exclude_application_support_specialist_edinburgh_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Specialist"},
            {"query": "Business Application Support Analyst"},
        ],
        "locations": [
            {"location": "Edinburgh, UK"},
            {"location": "Glasgow, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Application Support Specialist", "location": "Edinburgh"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Application Support Specialist", "Edinburgh") not in combos
    assert ("NHS Jobs", "Application Support Specialist", "Glasgow") in combos
    assert ("NHS Jobs", "Business Application Support Analyst", "Edinburgh") in combos


def test_build_scrape_targets_can_exclude_business_application_support_analyst_glasgow_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Business Application Support Analyst"},
            {"query": "Technical Systems Analyst"},
        ],
        "locations": [
            {"location": "Glasgow, UK"},
            {"location": "Remote"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Business Application Support Analyst", "location": "Glasgow"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Business Application Support Analyst", "Glasgow") not in combos
    assert ("NHS Jobs", "Technical Systems Analyst", "Glasgow") in combos
    assert ("NHS Jobs", "Technical Systems Analyst", "Remote") in combos


def test_build_scrape_targets_can_exclude_product_support_engineer_london_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Product Support Engineer"},
            {"query": "Platform Support Engineer"},
        ],
        "locations": [
            {"location": "London, UK"},
            {"location": "Manchester, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Product Support Engineer", "location": "London"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Product Support Engineer", "London") not in combos
    assert ("NHS Jobs", "Product Support Engineer", "Manchester") in combos
    assert ("NHS Jobs", "Platform Support Engineer", "London") in combos


def test_build_scrape_targets_can_exclude_platform_support_engineer_manchester_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Platform Support Engineer"},
            {"query": "Desktop Support Analyst"},
        ],
        "locations": [
            {"location": "Manchester, UK"},
            {"location": "Birmingham, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Platform Support Engineer", "location": "Manchester"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Platform Support Engineer", "Manchester") not in combos
    assert ("NHS Jobs", "Platform Support Engineer", "Birmingham") in combos
    assert ("NHS Jobs", "Desktop Support Analyst", "Manchester") in combos


def test_build_scrape_targets_can_exclude_blank_location_technical_systems_analyst_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Systems Analyst"},
            {"query": "Data Analyst"},
        ],
        "locations": [{"location": "Remote"}],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Technical Systems Analyst", "location": ""},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Technical Systems Analyst", "") not in combos
    assert ("NHS Jobs", "Data Analyst", "") in combos


def test_build_scrape_targets_can_exclude_desktop_support_analyst_birmingham_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Desktop Support Analyst"},
            {"query": "Data Analyst"},
        ],
        "locations": [
            {"location": "Birmingham, UK"},
            {"location": "Leeds, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Desktop Support Analyst", "location": "Birmingham"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Desktop Support Analyst", "Birmingham") not in combos
    assert ("NHS Jobs", "Desktop Support Analyst", "Leeds") in combos
    assert ("NHS Jobs", "Data Analyst", "Birmingham") in combos


def test_build_scrape_targets_can_exclude_data_analyst_leeds_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Data Analyst"},
            {"query": "1st Line Support Analyst"},
        ],
        "locations": [
            {"location": "Leeds, UK"},
            {"location": "Bristol, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Data Analyst", "location": "Leeds"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Data Analyst", "Leeds") not in combos
    assert ("NHS Jobs", "Data Analyst", "Bristol") in combos
    assert ("NHS Jobs", "1st Line Support Analyst", "Leeds") in combos


def test_build_scrape_targets_can_exclude_first_line_support_analyst_bristol_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "1st Line Support Analyst"},
            {"query": "2nd Line Support Analyst"},
        ],
        "locations": [
            {"location": "Bristol, UK"},
            {"location": "Edinburgh, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "1st Line Support Analyst", "location": "Bristol"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "1st Line Support Analyst", "Bristol") not in combos
    assert ("NHS Jobs", "1st Line Support Analyst", "Edinburgh") in combos
    assert ("NHS Jobs", "2nd Line Support Analyst", "Bristol") in combos


def test_build_scrape_targets_can_exclude_helpdesk_analyst_glasgow_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Helpdesk Analyst"},
            {"query": "2nd Line Support Analyst"},
        ],
        "locations": [
            {"location": "Glasgow, UK"},
            {"location": "Edinburgh, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Helpdesk Analyst", "location": "Glasgow"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Helpdesk Analyst", "Glasgow") not in combos
    assert ("NHS Jobs", "Helpdesk Analyst", "Edinburgh") in combos
    assert ("NHS Jobs", "2nd Line Support Analyst", "Glasgow") in combos


def test_build_scrape_targets_can_exclude_second_line_support_analyst_edinburgh_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "2nd Line Support Analyst"},
            {"query": "Cloud Support Engineer"},
        ],
        "locations": [
            {"location": "Edinburgh, UK"},
            {"location": "Remote"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "2nd Line Support Analyst", "location": "Edinburgh"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "2nd Line Support Analyst", "Edinburgh") not in combos
    assert ("NHS Jobs", "Cloud Support Engineer", "Edinburgh") in combos


def test_build_scrape_targets_can_exclude_infrastructure_support_engineer_london_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Infrastructure Support Engineer"},
            {"query": "Cloud Support Engineer"},
        ],
        "locations": [
            {"location": "London, UK"},
            {"location": "Remote"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Infrastructure Support Engineer", "location": "London"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Infrastructure Support Engineer", "London") not in combos
    assert ("NHS Jobs", "Cloud Support Engineer", "London") in combos


def test_build_scrape_targets_can_exclude_blank_location_cloud_support_engineer_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Cloud Support Engineer"},
            {"query": "ERP Support Analyst"},
        ],
        "locations": [{"location": "Remote"}],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Cloud Support Engineer", "location": ""},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Cloud Support Engineer", "") not in combos
    assert ("NHS Jobs", "ERP Support Analyst", "") in combos


def test_build_scrape_targets_can_exclude_erp_support_analyst_manchester_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "ERP Support Analyst"},
            {"query": "IT Support Analyst"},
        ],
        "locations": [
            {"location": "Manchester, UK"},
            {"location": "Birmingham, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "ERP Support Analyst", "location": "Manchester"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "ERP Support Analyst", "Manchester") not in combos
    assert ("NHS Jobs", "ERP Support Analyst", "Birmingham") in combos
    assert ("NHS Jobs", "IT Support Analyst", "Manchester") in combos


def test_build_scrape_targets_can_exclude_it_support_analyst_birmingham_slot_again() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "IT Support Analyst"},
            {"query": "Application Support Engineer"},
        ],
        "locations": [
            {"location": "Birmingham, UK"},
            {"location": "Leeds, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "IT Support Analyst", "location": "Birmingham"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "IT Support Analyst", "Birmingham") not in combos
    assert ("NHS Jobs", "IT Support Analyst", "Leeds") in combos
    assert ("NHS Jobs", "Application Support Engineer", "Birmingham") in combos


def test_build_scrape_targets_can_exclude_application_support_engineer_leeds_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Engineer"},
            {"query": "Technical Support Engineer"},
        ],
        "locations": [
            {"location": "Leeds, UK"},
            {"location": "Bristol, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Application Support Engineer", "location": "Leeds"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Application Support Engineer", "Leeds") not in combos
    assert ("NHS Jobs", "Application Support Engineer", "Bristol") in combos
    assert ("NHS Jobs", "Technical Support Engineer", "Leeds") in combos


def test_build_scrape_targets_can_exclude_technical_support_engineer_bristol_slot_again() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Support Engineer"},
            {"query": "Service Desk Analyst"},
        ],
        "locations": [
            {"location": "Bristol, UK"},
            {"location": "Edinburgh, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Technical Support Engineer", "location": "Bristol"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Technical Support Engineer", "Bristol") not in combos
    assert ("NHS Jobs", "Technical Support Engineer", "Edinburgh") in combos
    assert ("NHS Jobs", "Service Desk Analyst", "Bristol") in combos


def test_build_scrape_targets_can_exclude_service_desk_analyst_edinburgh_slot_again() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Service Desk Analyst"},
            {"query": "Systems Support Analyst"},
        ],
        "locations": [
            {"location": "Edinburgh, UK"},
            {"location": "Glasgow, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Service Desk Analyst", "location": "Edinburgh"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Service Desk Analyst", "Edinburgh") not in combos
    assert ("NHS Jobs", "Service Desk Analyst", "Glasgow") in combos
    assert ("NHS Jobs", "Systems Support Analyst", "Edinburgh") in combos


def test_build_scrape_targets_can_exclude_systems_support_analyst_glasgow_slot_again() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Systems Support Analyst"},
            {"query": "Application Support Analyst"},
        ],
        "locations": [
            {"location": "Glasgow, UK"},
            {"location": "Remote"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Systems Support Analyst", "location": "Glasgow"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Systems Support Analyst", "Glasgow") not in combos
    assert ("NHS Jobs", "Application Support Analyst", "Glasgow") in combos


def test_build_scrape_targets_can_exclude_blank_location_application_support_analyst_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Application Support Analyst"},
            {"query": "Production Support Engineer"},
        ],
        "locations": [{"location": "Remote"}],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Application Support Analyst", "location": ""},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Application Support Analyst", "") not in combos
    assert ("NHS Jobs", "Production Support Engineer", "") in combos


def test_build_scrape_targets_can_exclude_production_support_engineer_london_slot() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Production Support Engineer"},
            {"query": "Technical Support Analyst"},
        ],
        "locations": [
            {"location": "London, UK"},
            {"location": "Manchester, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Production Support Engineer", "location": "London"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["name"], t["query"], t["location"]) for t in targets}
    assert ("NHS Jobs", "Production Support Engineer", "London") not in combos
    assert ("NHS Jobs", "Production Support Engineer", "Manchester") in combos
    assert ("NHS Jobs", "Technical Support Analyst", "London") in combos


def test_build_scrape_targets_can_exclude_bulk_nhs_location_slots() -> None:
    sites = [
        {
            "name": "NHS Jobs",
            "url": "https://www.jobs.nhs.uk/candidate/search/results?keyword={query_encoded}&location={location_encoded}",
            "type": "search",
            "omit_generic_location": True,
            "location_first_segment": True,
        }
    ]
    search_cfg = {
        "queries": [
            {"query": "Technical Support Analyst"},
            {"query": "IT Service Desk Analyst"},
            {"query": "IT Support Engineer"},
            {"query": "Software Support Engineer"},
            {"query": "Technical Application Support Engineer"},
            {"query": "Application Support Specialist"},
            {"query": "Business Application Support Analyst"},
            {"query": "Technical Systems Analyst"},
            {"query": "Product Support Engineer"},
            {"query": "Platform Support Engineer"},
        ],
        "locations": [
            {"location": "Manchester, UK"},
            {"location": "Birmingham, UK"},
            {"location": "Leeds, UK"},
            {"location": "Bristol, UK"},
            {"location": "Edinburgh, UK"},
            {"location": "Glasgow, UK"},
            {"location": "Remote"},
            {"location": "London, UK"},
        ],
        "site_query_location_exclusions": {
            "NHS Jobs": [
                {"query": "Technical Support Analyst", "location": "Manchester"},
                {"query": "IT Service Desk Analyst", "location": "Birmingham"},
                {"query": "IT Support Engineer", "location": "Leeds"},
                {"query": "Software Support Engineer", "location": "Bristol"},
                {"query": "Technical Application Support Engineer", "location": "Edinburgh"},
                {"query": "Application Support Specialist", "location": "Glasgow"},
                {"query": "Business Application Support Analyst", "location": ""},
                {"query": "Technical Systems Analyst", "location": "London"},
                {"query": "Product Support Engineer", "location": "Manchester"},
                {"query": "Platform Support Engineer", "location": "Birmingham"},
                {"query": "Desktop Support Analyst", "location": "Leeds"},
                {"query": "Data Analyst", "location": "Bristol"},
                {"query": "1st Line Support Analyst", "location": "Edinburgh"},
                {"query": "2nd Line Support Analyst", "location": "Glasgow"},
                {"query": "Helpdesk Analyst", "location": ""},
                {"query": "Cloud Support Engineer", "location": "London"},
                {"query": "Infrastructure Support Engineer", "location": "Manchester"},
                {"query": "ERP Support Analyst", "location": "Birmingham"},
                {"query": "IT Support Analyst", "location": "Leeds"},
                {"query": "Application Support Engineer", "location": "Bristol"},
            ]
        },
    }

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    combos = {(t["query"], t["location"]) for t in targets}
    assert ("Technical Support Analyst", "Manchester") not in combos
    assert ("IT Service Desk Analyst", "Birmingham") not in combos
    assert ("IT Support Engineer", "Leeds") not in combos
    assert ("Software Support Engineer", "Bristol") not in combos
    assert ("Technical Application Support Engineer", "Edinburgh") not in combos
    assert ("Application Support Specialist", "Glasgow") not in combos
    assert ("Business Application Support Analyst", "") not in combos
    assert ("Technical Systems Analyst", "London") not in combos
    assert ("Product Support Engineer", "Manchester") not in combos
    assert ("Platform Support Engineer", "Birmingham") not in combos
    assert ("Technical Support Analyst", "Birmingham") in combos
    assert ("IT Service Desk Analyst", "Manchester") in combos
    assert ("Business Application Support Analyst", "Manchester") in combos
    assert ("Technical Systems Analyst", "") in combos


def test_balanced_target_window_interleaves_sites_before_capping() -> None:
    targets = [
        {"name": "NHS Jobs", "query": "q1"},
        {"name": "NHS Jobs", "query": "q2"},
        {"name": "NHS Jobs", "query": "q3"},
        {"name": "HealthJobsUK", "query": "q1"},
        {"name": "HealthJobsUK", "query": "q2"},
        {"name": "HealthJobsUK", "query": "q3"},
    ]

    window = _balanced_target_window(targets, max_targets=4)

    assert [(t["name"], t["query"]) for t in window] == [
        ("NHS Jobs", "q1"),
        ("HealthJobsUK", "q1"),
        ("NHS Jobs", "q2"),
        ("HealthJobsUK", "q2"),
    ]


def test_balanced_target_window_can_rotate_window() -> None:
    targets = [
        {"name": "GOV.UK Find a job", "query": "q1"},
        {"name": "GOV.UK Find a job", "query": "q2"},
        {"name": "NHS Jobs", "query": "q1"},
        {"name": "NHS Jobs", "query": "q2"},
        {"name": "HealthJobsUK", "query": "q1"},
        {"name": "HealthJobsUK", "query": "q2"},
        {"name": "MoJ Jobs", "query": "q1"},
        {"name": "MoJ Jobs", "query": "q2"},
    ]

    window = _balanced_target_window(targets, max_targets=4, offset=3)

    assert [(t["name"], t["query"]) for t in window] == [
        ("GOV.UK Find a job", "q2"),
        ("NHS Jobs", "q2"),
        ("HealthJobsUK", "q2"),
        ("MoJ Jobs", "q1"),
    ]


def test_balanced_target_window_prioritizes_gov_and_nhs_over_moj() -> None:
    targets = [
        {"name": "GOV.UK Find a job", "query": "q1"},
        {"name": "GOV.UK Find a job", "query": "q2"},
        {"name": "NHS Jobs", "query": "q1"},
        {"name": "NHS Jobs", "query": "q2"},
        {"name": "HealthJobsUK", "query": "q1"},
        {"name": "HealthJobsUK", "query": "q2"},
        {"name": "Jobs Go Public", "query": "q1"},
        {"name": "LG Jobs", "query": "q1"},
        {"name": "MoJ Jobs", "query": "q1"},
    ]

    window = _balanced_target_window(targets, max_targets=6)

    assert [(t["name"], t["query"]) for t in window] == [
        ("GOV.UK Find a job", "q1"),
        ("NHS Jobs", "q1"),
        ("HealthJobsUK", "q1"),
        ("GOV.UK Find a job", "q2"),
        ("NHS Jobs", "q2"),
        ("HealthJobsUK", "q2"),
    ]


def test_balanced_target_window_can_deprioritize_nhs_by_config() -> None:
    targets = [
        {"name": "GOV.UK Find a job", "query": "q1"},
        {"name": "NHS Jobs", "query": "q1"},
        {"name": "HealthJobsUK", "query": "q1"},
        {"name": "GOV.UK Find a job", "query": "q2"},
        {"name": "NHS Jobs", "query": "q2"},
        {"name": "HealthJobsUK", "query": "q2"},
        {"name": "GOV.UK Find a job", "query": "q3"},
    ]

    window = _balanced_target_window(
        targets,
        max_targets=6,
        search_cfg={
            "smart_window_site_priority": {
                "GOV.UK Find a job": 0,
                "HealthJobsUK": 1,
                "NHS Jobs": 2,
            }
        },
    )

    assert [(t["name"], t["query"]) for t in window] == [
        ("GOV.UK Find a job", "q1"),
        ("HealthJobsUK", "q1"),
        ("NHS Jobs", "q1"),
        ("GOV.UK Find a job", "q2"),
        ("HealthJobsUK", "q2"),
        ("NHS Jobs", "q2"),
    ]


def test_balanced_target_window_can_cap_nhs_by_config() -> None:
    targets = [
        {"name": "GOV.UK Find a job", "query": "q1"},
        {"name": "NHS Jobs", "query": "q1"},
        {"name": "HealthJobsUK", "query": "q1"},
        {"name": "GOV.UK Find a job", "query": "q2"},
        {"name": "NHS Jobs", "query": "q2"},
        {"name": "HealthJobsUK", "query": "q2"},
        {"name": "NHS Jobs", "query": "q3"},
        {"name": "Jobs Go Public", "query": "q1"},
    ]

    window = _balanced_target_window(
        targets,
        max_targets=6,
        search_cfg={
            "smart_window_site_caps": {
                "NHS Jobs": 1,
            }
        },
    )

    assert [(t["name"], t["query"]) for t in window] == [
        ("GOV.UK Find a job", "q1"),
        ("NHS Jobs", "q1"),
        ("HealthJobsUK", "q1"),
        ("GOV.UK Find a job", "q2"),
        ("HealthJobsUK", "q2"),
        ("Jobs Go Public", "q1"),
    ]


def test_dedupe_site_query_location_twins_prefers_nhs_before_healthjobs() -> None:
    targets = [
        {"name": "HealthJobsUK", "query": "Helpdesk Analyst", "location": "Manchester, UK"},
        {"name": "NHS Jobs", "query": "Helpdesk Analyst", "location": "Manchester"},
        {"name": "HealthJobsUK", "query": "Software Support Engineer", "location": "Manchester, UK"},
        {"name": "NHS Jobs", "query": "Software Support Engineer", "location": "Manchester"},
    ]

    deduped = _dedupe_site_query_location_twins(targets)

    assert [(t["name"], t["query"]) for t in deduped[:2]] == [
        ("NHS Jobs", "Helpdesk Analyst"),
        ("NHS Jobs", "Software Support Engineer"),
    ]
    assert sorted((t["name"], t["query"]) for t in deduped[2:]) == [
        ("HealthJobsUK", "Helpdesk Analyst"),
        ("HealthJobsUK", "Software Support Engineer"),
    ]


def test_dedupe_site_query_location_twins_keeps_non_search_targets_in_order() -> None:
    targets = [
        {"name": "NHS Jobs", "query": "Helpdesk Analyst", "location": "Manchester"},
        {"name": "HealthJobsUK", "query": "Helpdesk Analyst", "location": "Manchester, UK"},
        {"name": "MoJ Jobs", "query": None, "location": ""},
    ]

    deduped = _dedupe_site_query_location_twins(targets)

    assert deduped[0]["name"] == "NHS Jobs"
    assert deduped[1]["name"] == "MoJ Jobs"
    assert deduped[2]["name"] == "HealthJobsUK"


def test_normalize_selector_for_bs4_upgrades_deprecated_contains() -> None:
    selector = 'ul.search-result-details > li:contains("PS")'
    normalized = _normalize_selector_for_bs4(selector)
    assert normalized == 'ul.search-result-details > li:-soup-contains("PS")'


def test_extract_jobs_with_selectors_recovers_location_text_and_absolute_url() -> None:
    html = """
    <div data-testid="jcl-job-teaser-wrapper">
      <div class="jcl-job-teaser-title"><a href="/job/finance-trainee-51973">Finance Trainee</a></div>
      <div data-testid="jcl-job-teaser-location">
        <span class="icon"><svg><title>location</title></svg></span>
        <span class="popoverlist-no-list-item">Wembley Park, Wembley HA9 0FJ, UK</span>
      </div>
      <div class="jcl-job-teaser-salary"><span class="icon"><svg><title>salary</title></svg></span>£37,692 - £51,138 per year</div>
    </div>
    """
    selectors = {
        "job_card": '[data-testid="jcl-job-teaser-wrapper"]',
        "title": ".jcl-job-teaser-title a",
        "salary": ".jcl-job-teaser-salary",
        "description": None,
        "location": '[data-testid="jcl-job-teaser-location"] span',
        "url": ".jcl-job-teaser-title a",
    }

    jobs = _extract_jobs_with_selectors(
        html,
        selectors,
        site_name="Jobs Go Public",
        page_url="https://www.jobsgopublic.com/jobs?keywords=IT+Support+Analyst&location=London%2C+UK",
    )

    assert jobs == [
        {
            "title": "Finance Trainee",
            "salary": "£37,692 - £51,138 per year",
            "description": None,
            "location": "Wembley Park, Wembley HA9 0FJ, UK",
            "url": "https://www.jobsgopublic.com/job/finance-trainee-51973",
        }
    ]


def test_extract_jobs_with_selectors_handles_govuk_find_a_job_card() -> None:
    html = """
    <div data-testid="searchResultCard-abc123">
      <a data-testid="jobTitle-abc123" href="/jobs/abc123/view">Application Support Analyst (2nd &amp; 3rd Line Support)</a>
      <p data-testid="searchResultCardEmployer"><span>System C</span><span> - Birmingham, West Midlands</span></p>
      <p>£45,000 to £51,000 a year</p>
      <p data-testid="searchResultCardTags"><span>Hybrid</span><span>Permanent</span></p>
      <p data-testid="searchResultCardJobDescription">Support critical business applications.</p>
    </div>
    """
    selectors = {
        "job_card": 'div[data-testid^="searchResultCard-"]',
        "title": 'a[data-testid^="jobTitle-"]',
        "salary": "p",
        "description": 'p[data-testid="searchResultCardJobDescription"]',
        "location": 'p[data-testid="searchResultCardEmployer"] span:nth-of-type(2)',
        "url": 'a[data-testid^="jobTitle-"]',
    }

    jobs = _extract_jobs_with_selectors(
        html,
        selectors,
        site_name="GOV.UK Find a job",
        page_url="https://www.jobs.service.gov.uk/jobs/search?keywords=Application+Support+Analyst&location=",
    )

    assert jobs == [
        {
            "title": "Application Support Analyst (2nd & 3rd Line Support)",
            "salary": "£45,000 to £51,000 a year",
            "description": "Support critical business applications.",
            "location": "Birmingham, West Midlands",
            "url": "https://www.jobs.service.gov.uk/jobs/abc123/view",
        }
    ]


def test_salary_text_looks_valid_rejects_govuk_date_and_employer_noise() -> None:
    assert _salary_text_looks_valid("13 April 2026") is False
    assert _salary_text_looks_valid("System C") is False
    assert _salary_text_looks_valid("Care UK Plc") is False


def test_salary_text_looks_valid_accepts_real_salary_forms() -> None:
    assert _salary_text_looks_valid("£45,000 to £51,000 per year") is True
    assert _salary_text_looks_valid("Competitive + Benefits") is True
    assert _salary_text_looks_valid("35k - 40k") is True


def test_title_matches_query_drops_obvious_mismatch() -> None:
    assert _title_matches_query("Senior EPR Support Analyst", "Technical Support Analyst") is True
    assert _title_matches_query("ICT-Service Support Manager", "IT Support Analyst") is True
    assert _title_matches_query("IT Support Analyst, Office Based", "Helpdesk Analyst") is True
    assert _title_matches_query("Service Desk Analyst", "Helpdesk Analyst") is True
    assert _title_matches_query("Applications Analyst", "Application Support Analyst") is True
    assert _title_matches_query("IT - Clinical Systems Analyst", "Application Support Analyst") is True
    assert _title_matches_query("EUC Engineer", "Infrastructure Support Engineer") is True
    assert _title_matches_query("Technical Support Engineer", "Application Support Engineer") is True
    assert _title_matches_query("Technical Support Engineer", "Infrastructure Support Engineer") is True
    assert _title_matches_query("3216 - ICT Infrastructure Support Officer", "Infrastructure Support Engineer") is True
    assert _title_matches_query("Business Information Analyst", "Helpdesk Analyst") is False
    assert _title_matches_query("Lead Business Intelligence Analyst - GM Cancer", "Helpdesk Analyst") is False
    assert _title_matches_query("Business Intelligence Support Analyst", "Application Support Analyst") is False
    assert _title_matches_query("ESG Systems Analyst", "Technical Systems Analyst") is False
    assert _title_matches_query("Finance Trainee", "IT Support Analyst") is False


def test_matches_exclude_titles_uses_shared_discovery_filter() -> None:
    exclude_titles = ["principal", "manager", "architect"]

    assert _matches_exclude_titles("Chief Planning Officer", exclude_titles) is False
    assert _matches_exclude_titles("Principal Analyst", exclude_titles) is True
    assert _matches_exclude_titles("IT Service Desk Manager", exclude_titles) is True


def test_location_ok_accepts_common_uk_location_forms(monkeypatch) -> None:
    monkeypatch.setattr(smartextract, "_location_country", lambda search_cfg=None: "UK")

    accept = ["London", "Manchester", "Birmingham", "Leeds", "Bristol", "Edinburgh", "Glasgow", "Cardiff", "Belfast"]
    reject = ["United States", "USA", "Canada", "India"]

    assert _location_ok("Wakefield, West Yorkshire", accept, reject) is True
    assert _location_ok("Redruth, TR15 1LU", accept, reject) is True
    assert _location_ok("BA1", accept, reject) is True
    assert _location_ok("Ashford, Kent, TN23 1ED", accept, reject) is True
    assert _location_ok("Manchester (M3 5NA), M3 5", accept, reject) is True


def test_location_ok_still_rejects_explicit_foreign_locations(monkeypatch) -> None:
    monkeypatch.setattr(smartextract, "_location_country", lambda search_cfg=None: "UK")

    accept = ["London", "Manchester"]
    reject = ["United States", "USA", "Canada", "India"]

    assert _location_ok("Austin, Texas, United States", accept, reject) is False
    assert _location_ok("Toronto, Canada", accept, reject) is False


def test_obvious_irrelevant_api_reason_flags_cookie_endpoints() -> None:
    resp = {"url": "https://crossdomain.cookie-script.com/getCookie", "status": 200, "size": 128}
    assert _obvious_irrelevant_api_reason(resp) == "cookie script endpoint"


def test_run_all_threads_salary_pref_to_storage(monkeypatch) -> None:
    captured: dict = {}

    monkeypatch.setattr(smartextract, "init_db", lambda: object())
    monkeypatch.setattr(smartextract, "get_stats", lambda conn: {"total": 0, "pending_detail": 0})
    monkeypatch.setattr(
        smartextract,
        "_run_one_site",
        lambda name, url: {
            "status": "PASS",
            "name": name,
            "total": 1,
            "titles": 1,
            "strategy": "css_selectors",
            "jobs": [{"url": "https://example.com/job", "title": "IT Support Analyst"}],
        },
    )

    def fake_store_jobs_filtered(
        conn,
        jobs,
        site,
        strategy,
        accept_locs,
        reject_locs,
        search_query=None,
        salary_pref=None,
        exclude_titles=None,
    ):
        captured["salary_pref"] = salary_pref
        return 1, 0

    monkeypatch.setattr(smartextract, "_store_jobs_filtered", fake_store_jobs_filtered)

    salary_pref = {"min": 35000.0, "max": 45000.0, "currency": "GBP"}

    result = smartextract._run_all(
        [{"name": "Example Jobs", "url": "https://example.com/search", "query": "IT Support Analyst"}],
        ["London"],
        [],
        workers=1,
        salary_pref=salary_pref,
    )

    assert captured["salary_pref"] == salary_pref
    assert result == {"total_new": 1, "total_existing": 0, "passed": 1, "total": 1}


def test_run_all_threads_exclude_titles_to_storage(monkeypatch) -> None:
    captured: dict = {}

    monkeypatch.setattr(smartextract, "init_db", lambda: object())
    monkeypatch.setattr(smartextract, "get_stats", lambda conn: {"total": 0, "pending_detail": 0})
    monkeypatch.setattr(
        smartextract,
        "_run_one_site",
        lambda name, url: {
            "status": "PASS",
            "name": name,
            "total": 1,
            "titles": 1,
            "strategy": "css_selectors",
            "jobs": [{"url": "https://example.com/job", "title": "IT Support Analyst"}],
        },
    )

    def fake_store_jobs_filtered(
        conn,
        jobs,
        site,
        strategy,
        accept_locs,
        reject_locs,
        search_query=None,
        salary_pref=None,
        exclude_titles=None,
    ):
        captured["exclude_titles"] = exclude_titles
        return 1, 0

    monkeypatch.setattr(smartextract, "_store_jobs_filtered", fake_store_jobs_filtered)

    exclude_titles = ["principal", "manager"]

    result = smartextract._run_all(
        [{"name": "Example Jobs", "url": "https://example.com/search", "query": "IT Support Analyst"}],
        ["London"],
        [],
        workers=1,
        exclude_titles=exclude_titles,
    )

    assert captured["exclude_titles"] == exclude_titles
    assert result == {"total_new": 1, "total_existing": 0, "passed": 1, "total": 1}
