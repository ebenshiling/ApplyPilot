from applypilot.scoring.tailor_strategy import (
    build_adaptive_budget,
    build_fact_library,
    build_jd_targets,
    build_summary_gap_guidance,
    check_quant_consistency,
    detect_role_pack,
    evaluate_requirement_gaps,
    extract_jd_requirements,
    extract_job_responsibilities,
    format_responsibility_map_for_prompt,
    map_responsibilities_to_evidence,
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


def test_detect_application_support_and_business_analysis_packs() -> None:
    profile = _profile()

    app_job = {
        "title": "Application Support Engineer",
        "full_description": "Production support, incident management, monitoring, log analysis, and SQL investigation.",
    }
    ba_job = {
        "title": "Business Analyst",
        "full_description": "Gather requirements, run stakeholder workshops, define user stories, and map workflows.",
    }

    assert detect_role_pack(app_job, profile)["pack"] == "application_support"
    assert detect_role_pack(ba_job, profile)["pack"] == "business_analysis"

    app_budget = build_adaptive_budget(app_job, profile, role_pack="application_support")
    assert app_budget["runtime_rules"]["required_sections"]["projects"] is False


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
    assert strip_fact_citations("Delivered KPI dashboards [F3].") == "Delivered KPI dashboards."
    assert strip_fact_citations("Managed incidents [F2, F7].") == "Managed incidents."
    assert strip_fact_citations("Built ETL [F1] and reporting [F2].") == "Built ETL and reporting."


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


def test_extract_jd_requirements_and_gap_evaluation() -> None:
    profile = _profile()
    profile["skills_boundary"]["tools"].extend(["Microsoft 365", "ITIL"])
    resume_text = "SQL Power BI Git Microsoft 365 Delivered dashboards and reporting improvements."
    jd = (
        "Essential: SQL, Power BI, and ITIL. Required: security clearance. "
        "Preferred: Tableau. NHS trust experience is desirable."
    )
    kw = {"prompt_keywords": ["SQL", "Power BI", "ITIL", "Tableau"]}

    req = extract_jd_requirements(
        job_title="Application Support Analyst",
        job_description=jd,
        profile=profile,
        keyword_bank=kw,
    )
    assert "SQL" in req["must_have_skills"]
    assert "ITIL" in req["required_credentials"]
    assert "security clearance" in req["hard_constraints"]
    assert "healthcare" in req["domains"]

    gaps = evaluate_requirement_gaps(req, resume_text, profile)
    assert "security clearance" in gaps["missing_hard_requirements"]
    assert "healthcare" in gaps["missing_domains"]


def test_extract_responsibilities_and_map_to_evidence() -> None:
    responsibilities = extract_job_responsibilities(
        "Support users across Microsoft 365 and Entra ID.\n"
        "Investigate incidents and resolve access issues.\n"
        "Maintain documentation and improve service desk workflows.",
        limit=4,
    )
    assert any("Microsoft 365" in r for r in responsibilities)

    facts = [
        {"id": "F1", "text": "Resolved Microsoft 365 access issues for staff and students.", "numbers": []},
        {"id": "F2", "text": "Maintained support documentation and knowledge base updates.", "numbers": []},
        {"id": "F3", "text": "Investigated service desk incidents and restored access quickly.", "numbers": []},
    ]
    mapped = map_responsibilities_to_evidence(responsibilities, facts, per_resp_limit=2)
    assert mapped
    assert any(item["evidence"] for item in mapped)
    prompt_block = format_responsibility_map_for_prompt(mapped, limit=3)
    assert "Responsibility:" in prompt_block
    assert "Closest evidence:" in prompt_block


def test_build_summary_gap_guidance_mentions_missing_gaps() -> None:
    guidance = build_summary_gap_guidance(
        {
            "missing_hard_requirements": ["security clearance"],
            "missing_must_have_skills": ["ITIL"],
            "missing_domains": ["healthcare"],
        }
    )
    assert "security clearance" in guidance
    assert "ITIL" in guidance
    assert "healthcare" in guidance


def test_jd_extraction_filters_junk_phrases_and_brand_lines() -> None:
    profile = _profile()
    req = extract_jd_requirements(
        job_title="Reporting Analyst",
        job_description=(
            "You will join a supportive, ambitious organisation where quality matters. "
            "Required: Excel and Power BI. Preferred: Tableau. "
            "We value our employees and offer a competitive salary."
        ),
        profile=profile,
        keyword_bank={"prompt_keywords": ["Excel", "Power BI", "committed to", "FE"]},
    )
    assert "Excel" in req["must_have_skills"]
    assert "Power BI" in req["must_have_skills"]
    assert "committed to" not in req["must_have_skills"]
    assert "FE" not in req["must_have_skills"]


def test_responsibility_extraction_skips_branding_lines() -> None:
    responsibilities = extract_job_responsibilities(
        "You will join a supportive, ambitious organisation where quality matters.\n"
        "This is an exciting opportunity to work with great people.\n"
        "Design, build, and maintain Excel-based reports and dashboards.\n"
        "Develop and maintain Power BI dashboards to support performance monitoring.",
        limit=4,
    )
    joined = " ".join(responsibilities).lower()
    assert "exciting opportunity" not in joined
    assert "supportive, ambitious organisation" not in joined
    assert any("power bi dashboards" in r.lower() for r in responsibilities)


def test_responsibility_extraction_merges_wrapped_reporting_lines() -> None:
    responsibilities = extract_job_responsibilities(
        "You’ll take ownership of financial reporting and performance analysis, ensuring our teams have the visibility they need to meet\n"
        "targets and continuously improve. Working closely with operational leaders, you’ll support the development and enhancement of key\n"
        "projects, performance measures, and reporting frameworks that keep pace with the evolving needs of the organisation.",
        limit=4,
    )
    assert any("meet targets and continuously improve" in r.lower() for r in responsibilities)
    assert any("development and enhancement of key projects" in r.lower() for r in responsibilities)
    assert all(r.lower() != "targets and continuously improve." for r in responsibilities)
    assert all(not r.lower().endswith(" of key") for r in responsibilities)


def test_responsibility_extraction_skips_department_context_lines() -> None:
    responsibilities = extract_job_responsibilities(
        "The Healthcare Analytics department is pivotal to ensuring the data and analytics needs of the organisation are met and in supporting the organisation to make data driven decisions.\n"
        "You will have a range of responsibilities including regular reporting, adhoc analysis and insight, and new report development using Power BI.\n"
        "You will contribute to the delivery of core functions within the team and be responsible for providing accurate, complete and timely outputs.",
        limit=4,
    )
    joined = " ".join(responsibilities).lower()
    assert "department is pivotal" not in joined
    assert any("regular reporting" in r.lower() for r in responsibilities)
    assert any("accurate, complete and timely outputs" in r.lower() for r in responsibilities)
