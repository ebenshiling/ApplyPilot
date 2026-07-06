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


def test_assemble_resume_uses_configured_technical_projects_for_support_titles() -> None:
    profile = _base_profile()
    profile["resume_sections"] = {
        "application_support_projects": [
            {
                "header": "Poultry ERP Management System",
                "subtitle": "Technologies: Django REST Framework, React, SQL, REST APIs",
                "bullets": [
                    "Developed and supported an integrated ERP platform for poultry farm operations covering inventory, finance, sales, purchases, analytics, and operational reporting",
                    "Investigated and resolved business logic, API, and data consistency issues across interconnected modules",
                    "Worked with SQL-backed transactional data, automation workflows, and production-style operational processes",
                ],
            }
        ]
    }
    data = {
        "title": "Application Support Engineer",
        "summary": "Application support engineer focused on resolving incidents and supporting production systems.",
        "core_skills": ["SQL", "REST APIs", "Incident Management", "Monitoring", "Windows", "Linux"],
        "skills": {"Core": "SQL, REST APIs, Incident Management"},
        "experience": [
            {
                "header": "Application Support Engineer at Example Org",
                "subtitle": "2024-2025",
                "bullets": ["Investigated and resolved production issues across business-critical services [F1]"],
            }
        ],
        "projects": [],
        "education": "",
    }

    out = assemble_resume_text(data, profile)

    assert "TECHNICAL PROJECTS" in out
    assert "Poultry ERP Management System" in out
    assert "Technologies: Django REST Framework, React, SQL, REST APIs" in out
    assert "Developed and supported an integrated ERP platform" in out
    assert "\nPROJECTS\n" not in out


def test_assemble_resume_prefers_application_support_projects_from_job_context() -> None:
    profile = _base_profile()
    profile["resume_facts"]["preserved_projects"] = [
        "Optimisation of Supply Chain Using Analytics and Visualisation in a BI Environment"
    ]
    profile["resume_sections"] = {
        "application_support_projects": [
            {
                "header": "Poultry ERP Management System",
                "subtitle": "Technologies: Django REST Framework, React, SQL, REST APIs",
                "bullets": [
                    "Developed and supported an integrated ERP platform for poultry farm operations covering inventory, finance, sales, purchases, analytics, and operational reporting",
                    "Investigated and resolved business logic, API, and data consistency issues across interconnected modules",
                ],
            }
        ]
    }
    data = {
        "title": "Support Analyst",
        "summary": "Support analyst focused on production incidents and business application stability.",
        "core_skills": ["SQL", "REST APIs", "Incident Management", "Monitoring", "Windows", "Linux"],
        "skills": {"Core": "SQL, REST APIs, Incident Management"},
        "experience": [
            {
                "header": "Application Support Engineer at Example Org",
                "subtitle": "2024-2025",
                "bullets": ["Investigated and resolved production issues across business-critical services [F1]"],
            }
        ],
        "projects": [],
        "education": "",
    }
    job = {"title": "Application Support Engineer", "full_description": "Production support, application troubleshooting, SQL investigation."}

    out = assemble_resume_text(data, profile, job=job)

    assert "TECHNICAL PROJECTS" in out
    assert "Poultry ERP Management System" in out
    assert "Optimisation of Supply Chain" not in out


def test_assemble_resume_uses_support_certifications_for_application_support_job_context() -> None:
    profile = _base_profile()
    profile["resume_sections"] = {
        "support_certifications": [
            "Microsoft 365 Certified: Fundamentals",
            "ITIL 4 Foundation",
        ]
    }
    data = {
        "title": "Support Analyst",
        "summary": "Application support analyst focused on production incidents and business application stability.",
        "core_skills": ["SQL", "REST APIs", "Incident Management", "Monitoring", "Windows", "Linux"],
        "skills": {"Core": "SQL, REST APIs, Incident Management"},
        "experience": [
            {
                "header": "Application Support Engineer at Example Org",
                "subtitle": "2024-2025",
                "bullets": ["Investigated and resolved production issues across business-critical services [F1]"],
            }
        ],
        "projects": [],
        "education": "",
    }
    job = {"title": "", "full_description": "Application support, production support, SQL investigation, incident troubleshooting."}

    out = assemble_resume_text(data, profile, job=job)

    assert "CERTIFICATIONS" in out
    assert "Microsoft 365 Certified: Fundamentals" in out
    assert "ITIL 4 Foundation" in out


def test_assemble_resume_prefers_data_projects_over_preserved_fallback() -> None:
    profile = _base_profile()
    profile["resume_facts"]["preserved_projects"] = [
        "Optimisation of Supply Chain Using Analytics and Visualisation in a BI Environment"
    ]
    profile["resume_sections"] = {
        "data_projects": [
            {
                "header": "Sales Performance Dashboard",
                "subtitle": "Technologies: Power BI, SQL, Python",
                "bullets": [
                    "Built a dashboard for commercial reporting and KPI analysis",
                    "Used SQL and Python to prepare and validate reporting datasets",
                ],
            }
        ]
    }
    data = {
        "title": "Reporting Analyst",
        "summary": "Reporting analyst focused on dashboarding and analytics.",
        "core_skills": ["SQL", "Python", "Power BI", "Excel", "ETL", "Reporting"],
        "skills": {"Core": "SQL, Python, Power BI"},
        "experience": [
            {
                "header": "Reporting Analyst at Example Org",
                "subtitle": "2024-2025",
                "bullets": ["Built KPI dashboards and reporting packs [F1]"],
            }
        ],
        "projects": [],
        "education": "",
    }
    job = {"title": "Data Analyst", "full_description": "Reporting, analytics, dashboards, Power BI, SQL."}

    out = assemble_resume_text(data, profile, job=job)

    assert "\nPROJECTS\n" in out
    assert "Sales Performance Dashboard" in out
    assert "Optimisation of Supply Chain" not in out


def test_assemble_resume_uses_preserved_projects_as_general_fallback() -> None:
    profile = _base_profile()
    profile["resume_facts"]["preserved_projects"] = [
        "Optimisation of Supply Chain Using Analytics and Visualisation in a BI Environment"
    ]
    data = {
        "title": "Support Analyst",
        "summary": "Support analyst focused on technical incidents and user support.",
        "core_skills": ["Microsoft 365", "Windows", "SQL", "Service Desk", "Troubleshooting", "Documentation"],
        "skills": {"Core": "Microsoft 365, Windows, SQL"},
        "experience": [
            {
                "header": "Support Analyst at Example Org",
                "subtitle": "2024-2025",
                "bullets": ["Resolved technical issues across user and system workflows [F1]"],
            }
        ],
        "projects": [],
        "education": "",
    }
    job = {"title": "Support Analyst", "full_description": "Support incidents, user troubleshooting, service desk."}

    out = assemble_resume_text(data, profile, job=job)

    assert "TECHNICAL PROJECTS" in out
    assert "Optimisation of Supply Chain Using Analytics and Visualisation in a BI Environment" in out


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
