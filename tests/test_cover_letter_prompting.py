from applypilot.scoring.cover_letter import _build_cover_letter_prompt
from applypilot.scoring.cover_letter import _build_paragraph_plan
from applypilot.scoring.cover_letter import _extract_company_name
from applypilot.scoring.cover_letter import _extract_job_signals
from applypilot.scoring.cover_letter import _polish_reporting_closer
from applypilot.scoring.cover_letter import _sanitize_cover_letter_text
from applypilot.scoring.cover_letter import generate_cover_letter_with_diagnostics
from applypilot.scoring.cover_letter import _generic_issues
from applypilot.scoring.cover_letter import _pick_role_letter_pack
from applypilot.scoring.cover_letter import _polish_support_closer


def test_pick_role_letter_pack_is_deterministic() -> None:
    job = {
        "url": "https://example.com/jobs/123",
        "title": "IT Support Analyst",
        "site": "ExampleJobs",
        "full_description": "Provide Microsoft 365, Entra ID, and service desk support.",
    }
    a = _pick_role_letter_pack(job)
    b = _pick_role_letter_pack(job)
    assert a["name"] == b["name"]
    assert a["name"] == "it_support"


def test_extract_job_signals_prefers_relevant_lines() -> None:
    job = {
        "full_description": """
        We are looking for an analyst to improve data quality across reporting workflows.
        You will build SQL and Python pipelines for KPI dashboards.
        Partner with stakeholders across operations and product teams.
        Great culture and benefits package.
        """
    }
    out = _extract_job_signals(job)
    assert out
    text = " ".join(out).lower()
    assert "data quality" in text or "pipelines" in text or "stakeholders" in text


def test_build_prompt_includes_style_signals_and_opening_guards() -> None:
    profile = {
        "personal": {"full_name": "Jane Doe"},
        "skills_boundary": {"core": ["SQL", "Python"]},
        "resume_facts": {},
    }
    plan = {
        "company_name": "Example Org",
        "responsibilities": ["Support users across Microsoft 365"],
        "evidence": ["Resolved Microsoft 365 issues for staff and students."],
    }
    prompt = _build_cover_letter_prompt(
        profile,
        style={
            "name": "it_support",
            "opening": "Open by stating direct alignment with IT support.",
            "body_focus": "Use the strongest support evidence first.",
            "skills_focus": "Microsoft 365, Entra ID",
            "motivation": "Close on reliable support delivery.",
        },
        job_signals=["Maintain data governance policies across reporting"],
        recent_openings=["I built and maintained Power BI dashboards to track incidents."],
        plan=plan,
    )
    assert "ROLE LETTER PACK (it_support)" in prompt
    assert "Job-specific signals to anchor against" in prompt
    assert "Do NOT reuse these recent opening sentences" in prompt
    assert "Resume evidence to prioritize" in prompt
    assert "Top job responsibilities to address" in prompt


def test_build_paragraph_plan_prefers_relevant_evidence() -> None:
    profile = {
        "skills_boundary": {"support": ["Microsoft 365", "Entra ID", "Windows"]},
    }
    job = {
        "title": "IT Support Analyst",
        "company": "Example Org",
        "full_description": "Support users across Microsoft 365 and troubleshoot access issues.",
    }
    resume = (
        "Resolved Microsoft 365 access issues for students and staff.\n"
        "Supported onboarding and Entra ID account access.\n"
        "Worked with SQL reporting workflows."
    )
    plan = _build_paragraph_plan(job, resume, profile)
    assert plan["company_name"] == "Example Org"
    assert plan["responsibilities"]
    assert plan["evidence"]


def test_pick_public_and_commercial_cover_letter_packs() -> None:
    public_job = {
        "title": "IT Support Analyst",
        "site": "NHS Jobs",
        "full_description": "Support users in an NHS trust environment, handle service desk issues, and maintain Microsoft 365 access.",
    }
    commercial_job = {
        "title": "Technical Support Engineer",
        "site": "Example SaaS",
        "full_description": "Join our SaaS platform team to troubleshoot API and customer-facing technical support issues in a commercial product environment.",
    }

    assert _pick_role_letter_pack(public_job)["name"] == "public_it_support"
    assert _pick_role_letter_pack(commercial_job)["name"] == "commercial_technical_support"


def test_pick_reporting_packs_for_public_and_general_reporting() -> None:
    nhs_job = {
        "title": "Reporting Analyst | Mid and South Essex NHS Foundation Trust",
        "site": "GOV.UK Find a job",
        "full_description": "Healthcare Analytics department, regular reporting, ad-hoc analysis, and new report development using Power BI within an NHS trust.",
    }
    reporting_job = {
        "title": "Finance Reporting Analyst",
        "site": "GOV.UK Find a job",
        "full_description": "Monitor KPI performance, produce dashboards, and support financial reporting using Power BI and Excel.",
    }
    assert _pick_role_letter_pack(nhs_job)["name"] == "public_reporting"
    assert _pick_role_letter_pack(reporting_job)["name"] == "data_reporting"


def test_pick_general_reporting_pack_not_public_from_generic_trust_word() -> None:
    job = {
        "title": "Finance Reporting Analyst",
        "site": "GOV.UK Find a job",
        "full_description": (
            "Join Our Finance Team at The Compleat Food Group. "
            "You will produce KPI reporting and Power BI dashboards. "
            "Customers trust our brands and rely on accurate reporting."
        ),
    }
    assert _pick_role_letter_pack(job)["name"] == "data_reporting"


def test_extract_company_name_prefers_title_suffix_over_board_name() -> None:
    job = {
        "title": "Reporting Analyst | Mid and South Essex NHS Foundation Trust",
        "site": "GOV.UK Find a job",
    }
    assert _extract_company_name(job) == "Mid and South Essex NHS Foundation Trust"


def test_extract_company_name_from_job_description_intro() -> None:
    job = {
        "title": "Finance Reporting Analyst",
        "site": "GOV.UK Find a job",
        "full_description": (
            "Join Our Finance Team at The Compleat Food Group!\n"
            "At The Compleat Food Group, we're building better reporting across the business."
        ),
    }
    assert _extract_company_name(job) == "The Compleat Food Group"


def test_sanitize_cover_letter_text_removes_banned_soft_phrases() -> None:
    text = "I am a dedicated analyst with robust reporting skills and adept at stakeholder communication."
    cleaned = _sanitize_cover_letter_text(text)
    assert "dedicated" not in cleaned.lower()
    assert "robust" not in cleaned.lower()
    assert "adept at" not in cleaned.lower()


def test_sanitize_cover_letter_text_preserves_paragraph_breaks() -> None:
    text = "Dear Hiring Manager,\n\nParagraph one.\n\nParagraph two.\n\nBest,\nJane Doe"
    cleaned = _sanitize_cover_letter_text(text)
    assert "\n\nParagraph two." in cleaned
    assert cleaned.count("\n\n") >= 2


def test_sanitize_cover_letter_text_rewrites_banned_experience_phrase() -> None:
    text = "I have experience with stakeholder reporting, ad-hoc analysis, and new report development."
    cleaned = _sanitize_cover_letter_text(text)
    assert "i have experience with" not in cleaned.lower()
    assert "my experience includes stakeholder reporting" in cleaned.lower()


def test_sanitize_cover_letter_text_rewrites_i_believe() -> None:
    text = "I believe my reporting background fits this role well."
    cleaned = _sanitize_cover_letter_text(text)
    assert "i believe" not in cleaned.lower()
    assert cleaned.startswith("I know my reporting background")


def test_sanitize_cover_letter_text_rewrites_generic_closer_phrases() -> None:
    text = "I am interested in this opportunity at The Compleat Food Group and I am keen to contribute my skills to your finance team."
    cleaned = _sanitize_cover_letter_text(text)
    assert "i am interested in this opportunity at" not in cleaned.lower()
    assert "i am keen to contribute my skills to" not in cleaned.lower()
    assert "i am drawn to the compleat food group" in cleaned.lower()
    assert "i can bring this experience to your finance team" in cleaned.lower()


def test_sanitize_cover_letter_text_repairs_broken_i_am_fragment() -> None:
    text = (
        "I am stakeholder reporting, ad-hoc analysis, and new report development, ensuring accurate and timely outputs."
    )
    cleaned = _sanitize_cover_letter_text(text)
    assert cleaned.startswith("My experience includes stakeholder reporting")


def test_sanitize_cover_letter_text_repairs_broken_i_am_gerund_fragment() -> None:
    text = "I am producing comprehensive variance analyses and stakeholder reporting."
    cleaned = _sanitize_cover_letter_text(text)
    assert cleaned.startswith("My work includes producing comprehensive variance analyses")


def test_polish_reporting_closer_rewrites_finance_reporting_close() -> None:
    letter = (
        "Dear Hiring Manager,\n\n"
        "Paragraph one.\n\n"
        "Paragraph two.\n\n"
        "Paragraph three.\n\n"
        "I am drawn to The Compleat Food Group due to the focus on actionable insight. I am keen to contribute my expertise to your finance team.\n\n"
        "Best,\nJane Doe"
    )
    job = {
        "company": "The Compleat Food Group",
        "title": "Finance Reporting Analyst",
        "full_description": "Meet reporting timescales and support KPI visibility.",
    }
    plan = {
        "company_name": "The Compleat Food Group",
        "role_pack": {"name": "data_reporting"},
        "responsibilities": ["Ensure reports are completed accurately and within agreed timescales."],
    }
    polished = _polish_reporting_closer(letter, job, plan)
    assert "i am keen to contribute" not in polished.lower()
    assert "variance analysis" in polished.lower() or "reporting deadlines" in polished.lower()


def test_polish_reporting_closer_rewrites_public_reporting_close() -> None:
    letter = (
        "Dear Hiring Manager,\n\n"
        "Paragraph one.\n\n"
        "Paragraph two.\n\n"
        "Paragraph three.\n\n"
        "I want to support Mid and South Essex NHS Foundation Trust because the role matters. I can contribute to the Analysis and Reporting function.\n\n"
        "Best,\nJane Doe"
    )
    job = {
        "company": "Mid and South Essex NHS Foundation Trust",
        "title": "Reporting Analyst",
        "full_description": "Support Healthcare Analytics and the EPR programme.",
    }
    plan = {
        "company_name": "Mid and South Essex NHS Foundation Trust",
        "role_pack": {"name": "public_reporting"},
        "responsibilities": ["Support timely reporting for the EPR programme."],
    }
    polished = _polish_reporting_closer(letter, job, plan)
    assert "healthcare analytics" in polished.lower() or "epr programme" in polished.lower()


def test_polish_support_closer_rewrites_it_support_close() -> None:
    letter = (
        "Dear Hiring Manager,\n\n"
        "Paragraph one.\n\n"
        "Paragraph two.\n\n"
        "Paragraph three.\n\n"
        "I am interested in this opportunity because it looks like a good fit. I am keen to contribute to your team.\n\n"
        "Best,\nJane Doe"
    )
    job = {
        "company": "Example University",
        "title": "IT Support Analyst",
        "full_description": "Support Microsoft 365, onboarding, and ticket handling for staff and students.",
    }
    plan = {
        "company_name": "Example University",
        "role_pack": {"name": "it_support"},
        "responsibilities": ["Provide Microsoft 365 support and ticket resolution."],
    }
    polished = _polish_support_closer(letter, job, plan)
    assert "keen to contribute" not in polished.lower()
    assert "microsoft 365 support" in polished.lower() or "ticket handling" in polished.lower()


def test_polish_support_closer_rewrites_application_support_close() -> None:
    letter = (
        "Dear Hiring Manager,\n\n"
        "Paragraph one.\n\n"
        "Paragraph two.\n\n"
        "Paragraph three.\n\n"
        "I am interested in joining Example Org. I am keen to contribute to your team.\n\n"
        "Best,\nJane Doe"
    )
    job = {
        "company": "Example Org",
        "title": "Application Support Engineer",
        "full_description": "Support incidents, SQL investigation, and service continuity for critical applications.",
    }
    plan = {
        "company_name": "Example Org",
        "role_pack": {"name": "application_support"},
        "responsibilities": ["Handle incidents and support service continuity."],
    }
    polished = _polish_support_closer(letter, job, plan)
    assert "incident" in polished.lower()
    assert "services stable" in polished.lower() or "services running" in polished.lower()


def test_generate_cover_letter_with_diagnostics_uses_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        "applypilot.scoring.cover_letter.generate_cover_letter",
        lambda resume_text, job, profile, max_retries=3: (
            "Dear Hiring Manager,\n\nExample letter body.\n\nBest,\nJane Doe"
        ),
    )
    profile = {
        "personal": {"full_name": "Jane Doe"},
        "skills_boundary": {"support": ["Microsoft 365", "Entra ID"]},
    }
    job = {
        "title": "IT Support Analyst",
        "company": "Example Org",
        "full_description": "Support users across Microsoft 365 and troubleshoot access issues.",
    }
    letter, diagnostics = generate_cover_letter_with_diagnostics(
        "Resolved Microsoft 365 access issues for staff and students.",
        job,
        profile,
    )
    assert "Dear Hiring Manager" in letter
    assert diagnostics["role_pack"] == "it_support"
    assert diagnostics["company_name"] == "Example Org"
    assert diagnostics["responsibilities"]
    assert diagnostics["evidence"]


def test_generic_issues_flags_repeated_and_templated_opening() -> None:
    letter = (
        "Dear Hiring Manager,\n\n"
        "I built and maintained Power BI dashboards to track incident trends. "
        "I am interested in this role and particularly the opportunity to contribute to your team.\n\n"
        "Best,\nJane Doe"
    )
    issues = _generic_issues(letter, ["I built and maintained Power BI dashboards to track incident trends."])
    assert issues


def test_generic_issues_flags_generic_reporting_closer() -> None:
    letter = (
        "Dear Hiring Manager,\n\n"
        "I am applying for the Reporting Analyst role at Example Org.\n\n"
        "I am interested in Example Org because of its mission. I am keen to contribute to a forward-thinking team.\n\n"
        "Best,\nJane Doe"
    )
    issues = _generic_issues(letter, [])
    assert issues
    assert any("closing paragraph still reads generic" in issue.lower() for issue in issues)
