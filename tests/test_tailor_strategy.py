from applypilot.scoring.tailor_strategy import (
    build_adaptive_budget,
    build_fact_library,
    build_jd_targets,
    check_quant_consistency,
    detect_role_pack,
    score_jd_coverage,
    strip_fact_citations,
    validate_fact_citations,
)


def _profile() -> dict:
    return {
        "skills_boundary": {
            "languages": ["SQL", "Python"],
            "analytics": ["Power BI", "Tableau"],
            "tools": ["Git"],
        },
        "resume_facts": {
            "preserved_companies": ["Acme Corp"],
            "preserved_projects": ["Sales Dashboard Revamp"],
            "preserved_school": "Example University",
            "real_metrics": ["40%", "12"],
        },
        "tailoring": {
            "role_pack_override": "auto",
            "draft_candidates": 3,
            "safe_synonyms": {"sql": ["query optimization"]},
        },
    }


def test_detect_role_pack_and_adaptive_budget() -> None:
    profile = _profile()
    job = {
        "title": "Senior Data Analyst",
        "full_description": "We need analytics, dashboards, reporting, and stakeholder communication.",
    }

    pack = detect_role_pack(job, profile)
    assert pack["pack"] == "data_bi"

    budget = build_adaptive_budget(job, profile, role_pack=pack["pack"])
    rules = budget["runtime_rules"]
    assert int(rules["experience_bullets"]["most_recent"]["min"]) >= 5
    assert int(rules["project_bullets"]["min"]) >= 1


def test_fact_citations_and_strip() -> None:
    profile = _profile()
    resume_text = "Built SQL reports that reduced errors by 40%.\nImproved pipeline latency by 12%."
    facts = build_fact_library(resume_text, profile)
    fact_ids = {f["id"] for f in facts}
    assert "F1" in fact_ids

    data = {
        "experience": [
            {
                "header": "Data Analyst at Acme Corp",
                "subtitle": "2022-2025",
                "bullets": [
                    "Built SQL reporting workflows and reduced manual effort by 40% [F1]",
                    "Stabilized ETL checks with Python scripts [F2]",
                ],
            }
        ],
        "projects": [
            {
                "header": "Sales Dashboard Revamp",
                "subtitle": "Power BI",
                "bullets": ["Delivered KPI dashboards used by leadership [F3]"],
            }
        ],
    }

    ck = validate_fact_citations(data, fact_ids)
    assert ck["passed"] is True
    assert strip_fact_citations("Delivered KPI dashboards [F3]") == "Delivered KPI dashboards"


def test_jd_targets_coverage_and_quant_consistency() -> None:
    profile = _profile()
    resume_text = "SQL Python Power BI Tableau 40%"
    jd = "Looking for SQL, Power BI dashboarding, and data analysis experience."
    kw = {"prompt_keywords": ["SQL", "Power BI", "stakeholder communication"]}

    bundle = build_jd_targets(jd, profile, kw, resume_text=resume_text)
    targets = bundle["targets"]
    assert any(t.lower() == "sql" for t in targets)

    text = "Built SQL data models and Power BI dashboards for executive reporting."
    coverage = score_jd_coverage(text, targets, bundle["synonyms"])
    assert float(coverage["ratio"]) > 0

    data = {
        "summary": "Improved reporting reliability by 40%.",
        "experience": [{"bullets": ["Reduced errors by 40% [F1]"]}],
        "projects": [{"bullets": ["Shipped dashboard updates in 12 days [F2]"]}],
    }
    evidence = {"40", "12", "40%"}
    qc = check_quant_consistency(data, evidence)
    assert qc["passed"] is True
