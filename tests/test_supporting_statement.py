from applypilot.scoring.supporting_statement import (
    _build_prompt,
    _pick_variant,
    _trim_text_to_word_limit,
    _validate_statement,
    evaluate_statement_quality,
)
from applypilot.scoring import supporting_statement as supporting_statement_module


def test_trim_text_to_word_limit_enforces_cap() -> None:
    text = " ".join(f"word{i}" for i in range(1, 401))

    out = _trim_text_to_word_limit(text, 250)

    assert len(out.split()) <= 250
    assert out.split()[0] == "word1"


def test_validate_statement_respects_custom_short_cap() -> None:
    text = " ".join(f"word{i}" for i in range(1, 211))

    errs = _validate_statement(text, min_words=150, max_words=250)

    assert errs == []


def test_build_prompt_uses_requested_short_word_limit() -> None:
    messages = _build_prompt(
        variant={"name": "criteria-led", "guidance": "Use short sections."},
        resume_text="Resume text",
        job={"title": "Analyst", "company": "Entain", "full_description": "Job text"},
        criteria=[],
        profile={},
        min_words=150,
        max_words=250,
    )

    user = messages[1]["content"]
    assert "under 250 words" in user
    assert "150 to 250 words" in user


def test_build_prompt_includes_supplemental_facts_with_guardrail() -> None:
    messages = _build_prompt(
        variant={"name": "criteria-led", "guidance": "Use short sections."},
        resume_text="Resume text",
        job={"title": "Systems Tester", "company": "NHS", "full_description": "Job text"},
        criteria=[],
        profile={},
        supplemental_facts="Personal ERP project: wrote UAT cases and SQL data checks.",
        min_words=900,
        max_words=1400,
    )

    user = messages[1]["content"]
    assert "SUPPLEMENTAL CANDIDATE FACTS / EVIDENCE" in user
    assert "Personal ERP project: wrote UAT cases and SQL data checks." in user
    assert "do not treat it as employment unless it explicitly says so" in user


def test_nhs_statement_prompt_blocks_speculative_and_overclaimed_training() -> None:
    job = {
        "title": "Application Support Specialist",
        "company": "Hywel Dda University Health Board",
        "full_description": "NHS Supporting Information Person Specification Clinical Applications training groups",
    }

    assert _pick_variant(job)["name"] == "criteria-led"

    messages = _build_prompt(
        variant=_pick_variant(job),
        resume_text="Application support, user onboarding, SQL investigation, documentation.",
        job=job,
        criteria=[
            "Experience of training groups of learners with variable skill levels",
            "Experience providing application support",
            "In depth knowledge of Patient administration and clinical systems",
        ],
        profile={},
        supplemental_facts=(
            "Supporting Information Fields marked with an asterisk are mandatory. "
            "Google Advanced Data Analytics Professional Certificate and IBM Data Engineering Professional Certificate."
        ),
    )

    user = messages[1]["content"]
    assert "Do not write speculative first-day" in messages[0]["content"]
    assert "Treat pasted application-form instructions" in user
    assert "Do not claim direct clinical systems" in user
    assert "Do not claim group training" in user
    assert "Google Advanced Data Analytics Professional Certificate" in user
    assert "IBM Data Engineering Professional Certificate" in user


def test_validate_statement_rejects_contact_details_and_joining_plan_language() -> None:
    text = (
        "My immediate focus upon joining would be to use my experience. "
        "Please contact me on ebenezer@example.com or 07799 833024."
    )

    errs = _validate_statement(text, min_words=1, max_words=100)

    assert "Contains speculative joining plan language" in errs
    assert "Contains email address" in errs
    assert "Contains phone number" in errs


def test_statement_quality_harness_rejects_unsupported_nhs_claims() -> None:
    job = {
        "title": "Application Support Specialist",
        "company": "University Health Board",
        "full_description": (
            "Person Specification\nEssential\n"
            "Educated to Degree standard or Equivalent\n"
            "Experience providing application support\n"
            "Experience of training groups of learners\n"
            "Desirable\nIn depth knowledge of Patient administration and clinical systems"
        ),
    }
    resume = "MSc Information Technology. Supported live web applications. Provided user guidance."
    statement = (
        "My immediate focus upon joining would be to learn the service. "
        "I have extensive clinical systems, patient administration, group training, and training administration experience. "
        "Please contact me at candidate@example.invalid."
    )

    quality = evaluate_statement_quality(statement, resume, job)

    assert quality["status"] == "fail"
    assert quality["unsupported_claims"]
    assert "Repeats application-form instructions" not in quality["hard_failures"]
    assert "Contains personal email address" in quality["hard_failures"] or "Contains personal phone number" in quality["hard_failures"]


def test_generation_retries_after_statement_quality_failure(monkeypatch, tmp_path) -> None:
    responses = [
        {"statement": "My immediate focus upon joining would be to learn the service. Contact candidate@example.invalid."},
        {"statement": "I support live applications through incident triage, SQL investigation, user guidance, and clear technical documentation."},
    ]
    calls = []

    def fake_structured_json(messages, **kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(supporting_statement_module, "STATEMENT_DIR", tmp_path)
    monkeypatch.setattr(supporting_statement_module, "structured_json", fake_structured_json)

    result = supporting_statement_module.generate_supporting_statement(
        "Application support, SQL investigation, user guidance, documentation.",
        {"title": "Application Support Specialist", "company": "NHS", "full_description": "Experience providing application support."},
        {},
        min_words=1,
        max_words=100,
    )

    assert result.startswith("I support live applications")
    assert len(calls) == 2
