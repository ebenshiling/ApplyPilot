from applypilot.scoring.tailor import assemble_resume_text


def _base_profile() -> dict:
    return {
        "personal": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "555-0100",
            "linkedin_url": "https://linkedin.com/in/janedoe",
        },
        "experience": {
            "education_level": "Masters Degree",
        },
        "skills_boundary": {
            "languages": ["SQL", "Python"],
            "analytics": ["Power BI"],
        },
        "resume_facts": {
            "preserved_school": "Example University",
            "preserved_projects": [],
        },
        "resume_sections": {},
    }


def test_assemble_resume_injects_non_empty_projects_and_education_fallback() -> None:
    profile = _base_profile()
    data = {
        "title": "Data Analyst",
        "summary": "Data analyst with SQL and dashboard delivery experience across operations and reporting.",
        "core_skills": ["SQL", "Python", "Power BI", "Excel", "ETL", "Data Quality"],
        "skills": {},
        "experience": [
            {
                "header": "Data Analyst at Acme",
                "subtitle": "2022-2025",
                "bullets": [
                    "Built KPI dashboards and reduced manual reporting by 40% [F1]",
                    "Automated quality checks with SQL and Python [F2]",
                ],
            }
        ],
        "projects": [],
        "education": "",
    }

    out = assemble_resume_text(data, profile)

    assert "PROJECTS" in out
    assert "Selected Data Analyst Project Work" in out
    assert "Built KPI dashboards and reduced manual reporting by 40%" in out
    assert "[F1]" not in out
    assert "EDUCATION" in out
    assert "Example University" in out
    assert "Masters Degree" in out


def test_assemble_resume_accepts_string_resume_sections() -> None:
    profile = _base_profile()
    profile["resume_sections"] = {
        "education": "MSc Data Science - Example University\nBSc Computer Science - City College",
        "certifications": "PL-300 Data Analyst Associate\nGoogle Data Analytics",
    }
    data = {
        "title": "Data Analyst",
        "summary": "Experienced analyst focused on measurable reporting improvements.",
        "core_skills": ["SQL", "Python", "Power BI", "Excel", "ETL", "Data Quality"],
        "skills": {},
        "experience": [
            {
                "header": "Analyst at Acme",
                "subtitle": "2021-2025",
                "bullets": ["Delivered monthly dashboard packs for leadership [F1]"],
            }
        ],
        "projects": [
            {
                "header": "Sales Analytics Dashboard",
                "subtitle": "Power BI | 2024",
                "bullets": ["Built executive views for weekly pipeline reviews [F3]"],
            }
        ],
        "education": "",
    }

    out = assemble_resume_text(data, profile)

    assert "MSc Data Science - Example University" in out
    assert "BSc Computer Science - City College" in out
    assert "Masters Degree" not in out
    assert "CERTIFICATIONS" in out
    assert "PL-300 Data Analyst Associate" in out


def test_assemble_resume_omits_projects_for_support_titles() -> None:
    profile = _base_profile()
    data = {
        "title": "IT Support Analyst",
        "summary": "Support analyst focused on resolving incidents and supporting Microsoft 365 users.",
        "core_skills": ["Microsoft 365", "Entra ID", "Windows", "Ticketing"],
        "skills": {"Core": "Microsoft 365, Entra ID, Windows"},
        "experience": [
            {
                "header": "IT Support Intern at Example University",
                "subtitle": "2024-2025",
                "bullets": ["Resolved 15-25 tickets daily across email and service desk channels [F1]"],
            }
        ],
        "projects": [],
        "education": "",
    }

    out = assemble_resume_text(data, profile)
    assert "PROJECTS" not in out


def test_assemble_resume_normalizes_it_role_casing_in_headers() -> None:
    profile = _base_profile()
    data = {
        "title": "it support analyst",
        "summary": "Support analyst focused on incident resolution and user support.",
        "core_skills": ["Microsoft 365", "Entra ID"],
        "skills": {"Core": "Microsoft 365, Entra ID"},
        "experience": [
            {
                "header": "it support Intern at University of Derby",
                "subtitle": "2024-2025",
                "bullets": ["Resolved tickets and supported onboarding [F1]"],
            }
        ],
        "projects": [],
        "education": "",
    }

    out = assemble_resume_text(data, profile)
    assert "IT Support Analyst" in out
    assert "IT Support Intern at University of Derby" in out
