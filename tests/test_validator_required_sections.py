from applypilot.scoring.validator import validate_json_fields, validate_tailored_resume


def _sample_resume_without_projects() -> str:
    return (
        "SUMMARY\n"
        "Data analyst with strong SQL and BI reporting experience.\n\n"
        "CORE TECHNICAL SKILLS\n"
        "SQL | Python | Power BI | Excel\n\n"
        "PROFESSIONAL EXPERIENCE\n"
        "Data Analyst, Example Ltd\n"
        "- Built SQL dashboards that improved SLA adherence by 20 percent.\n"
        "- Developed Python ETL jobs that reduced reporting turnaround by 35 percent.\n"
        "- Maintained data quality checks in Excel and SQL for weekly KPI packs.\n\n"
        "EDUCATION\n"
        "MSc Information Technology\n"
    )


def test_projects_not_required_by_default() -> None:
    profile = {
        "personal": {"full_name": "Test User"},
        "resume_validation": {},
        "resume_facts": {},
        "resume_sections": {},
    }

    result = validate_tailored_resume(_sample_resume_without_projects(), profile, original_text="")
    assert result["passed"] is True
    assert not any("PROJECTS" in e for e in result["errors"])


def test_projects_can_be_forced_via_profile_rules() -> None:
    profile = {
        "personal": {"full_name": "Test User"},
        "resume_validation": {"required_sections": {"projects": True}},
        "resume_facts": {},
        "resume_sections": {},
    }

    result = validate_tailored_resume(_sample_resume_without_projects(), profile, original_text="")
    assert result["passed"] is False
    assert any("Missing required section: PROJECTS" in e for e in result["errors"])


def test_validate_json_fields_allows_two_experience_entries_by_default() -> None:
    profile = {
        "personal": {"full_name": "Test User"},
        "experience": {},
        "resume_validation": {},
        "resume_facts": {},
    }
    data = {
        "title": "Data Analyst",
        "summary": "Data analyst with SQL, Python, and dashboard experience delivering measurable outcomes.",
        "skills": {"data": "SQL, Python, Power BI", "tools": "Excel, Git"},
        "core_skills": ["SQL", "Python", "Power BI", "Excel", "Git", "ETL"],
        "experience": [
            {
                "header": "Data Analyst at Example Ltd",
                "subtitle": "2024-2026",
                "bullets": [
                    "Built SQL KPI reporting used by leadership every week.",
                    "Automated data cleanup in Python to reduce manual effort.",
                    "Created Power BI views for operational trend analysis.",
                ],
            },
            {
                "header": "Reporting Analyst at Sample Co",
                "subtitle": "2022-2024",
                "bullets": [
                    "Delivered monthly reporting packs with validated source data.",
                    "Maintained dashboard refresh workflows and data checks.",
                ],
            },
        ],
        "projects": [],
        "education": "MSc Information Technology",
    }

    result = validate_json_fields(data, profile)
    assert result["passed"] is True
