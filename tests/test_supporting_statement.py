from applypilot.scoring.supporting_statement import _build_prompt, _trim_text_to_word_limit, _validate_statement


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
