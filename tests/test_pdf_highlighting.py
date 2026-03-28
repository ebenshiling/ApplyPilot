from applypilot.scoring.pdf import build_html, parse_resume
from applypilot.scoring.keywords import build_keyword_bank


def test_keyword_bank_can_seed_responsibility_phrases() -> None:
    profile = {
        "skills_boundary": {
            "support": ["Microsoft 365", "Entra ID", "Incident management", "Service desk"],
            "tools": ["SQL"],
        }
    }
    jd = "Support users across Microsoft 365 and Entra ID. Investigate incidents and resolve access issues."
    resume = "Provided Microsoft 365 and Entra ID support. Investigated incidents and resolved access issues."

    bank = build_keyword_bank(
        jd,
        profile,
        resume_text=resume,
        seeded_phrases=["resolve access issues", "investigate incidents"],
    )
    highlights = [str(x).lower() for x in bank["highlight_keywords"]]
    assert "resolve access issues" in highlights or "investigate incidents" in highlights


def test_pdf_highlighting_wraps_keywords_once_professionally() -> None:
    text = """Ebenezer Otchere Brefo
IT SUPPORT ANALYST
ebenezer@example.com | 0700000000

SUMMARY
Provide Microsoft 365 support and resolve access issues quickly.

CORE TECHNICAL SKILLS
- Microsoft 365
- Entra ID

PROFESSIONAL EXPERIENCE
IT Support Analyst at Example Org
2025-Present
- Resolved Microsoft 365 incidents and improved service desk response.

EDUCATION
University Example
"""
    resume = parse_resume(text)
    resume["keywords"] = ["Microsoft 365", "resolve access issues"]
    html = build_html(resume)
    assert '<strong class="kw">Microsoft 365</strong>' in html
    assert '<strong class="kw">resolve access issues</strong>' in html
    assert '<strong class="kw"><strong' not in html
