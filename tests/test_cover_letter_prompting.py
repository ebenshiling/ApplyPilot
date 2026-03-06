from applypilot.scoring.cover_letter import _build_cover_letter_prompt
from applypilot.scoring.cover_letter import _extract_job_signals
from applypilot.scoring.cover_letter import _generic_issues
from applypilot.scoring.cover_letter import _pick_style_variant


def test_pick_style_variant_is_deterministic() -> None:
    job = {
        "url": "https://example.com/jobs/123",
        "title": "Data Insight Analyst",
        "site": "ExampleJobs",
    }
    a = _pick_style_variant(job)
    b = _pick_style_variant(job)
    assert a["name"] == b["name"]
    assert a["name"] in {"impact-first", "problem-solution", "stakeholder-value"}


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
    prompt = _build_cover_letter_prompt(
        profile,
        style={
            "name": "problem-solution",
            "opening": "Open with the concrete problem you solved.",
            "p2": "Show two wins with numbers.",
            "p3": "Tie to one JD challenge.",
        },
        job_signals=["Maintain data governance policies across reporting"],
        recent_openings=["I built and maintained Power BI dashboards to track incidents."],
    )
    assert "STYLE VARIANT (problem-solution)" in prompt
    assert "Job-specific signals to anchor against" in prompt
    assert "Do NOT reuse these recent opening sentences" in prompt


def test_generic_issues_flags_repeated_and_templated_opening() -> None:
    letter = (
        "Dear Hiring Manager,\n\n"
        "I built and maintained Power BI dashboards to track incident trends. "
        "I am interested in this role and particularly the opportunity to contribute to your team.\n\n"
        "Best,\nJane Doe"
    )
    issues = _generic_issues(letter, ["I built and maintained Power BI dashboards to track incident trends."])
    assert issues
