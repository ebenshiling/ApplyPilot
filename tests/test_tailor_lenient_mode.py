from applypilot.scoring.tailor import _is_fatal_full_validation_error
from applypilot.scoring.tailor import _is_fatal_json_error
from applypilot.scoring.tailor import _hard_requirement_gating_enabled
from applypilot.scoring.tailor import _lenient_tailor_enabled
from applypilot.scoring.tailor import _load_focus_roles
from applypilot.scoring.tailor import _max_missing_domain_requirements
from applypilot.scoring.tailor import _max_missing_must_haves
from applypilot.scoring.tailor import _min_coverage_required
from applypilot.scoring.tailor import _current_role_alignment_enabled
from applypilot.scoring.tailor import _strict_title_filter_enabled
from applypilot.scoring.tailor import _strict_evidence_enabled
from applypilot.scoring.tailor import _title_matches_focus_roles


def test_lenient_tailor_enabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv("APPLYPILOT_TAILOR_LENIENT", "1")
    assert _lenient_tailor_enabled({}) is True


def test_lenient_tailor_enabled_via_profile_mode() -> None:
    profile = {"tailoring": {"mode": "lenient"}}
    assert _lenient_tailor_enabled(profile) is True


def test_strict_evidence_disabled_when_lenient(monkeypatch) -> None:
    monkeypatch.setenv("APPLYPILOT_TAILOR_LENIENT", "1")
    profile = {"tailoring": {"strict_evidence": True}}
    assert _strict_evidence_enabled(profile) is False


def test_strict_evidence_enabled_by_default_in_strict_mode(monkeypatch) -> None:
    monkeypatch.delenv("APPLYPILOT_TAILOR_LENIENT", raising=False)
    monkeypatch.delenv("APPLYPILOT_TAILOR_STRICT_EVIDENCE", raising=False)
    assert _strict_evidence_enabled({"tailoring": {"mode": "strict"}}) is True


def test_min_coverage_defaults_follow_mode(monkeypatch) -> None:
    monkeypatch.delenv("APPLYPILOT_TAILOR_MIN_COVERAGE", raising=False)
    assert _min_coverage_required({"tailoring": {"mode": "lenient"}}) == 0.0
    assert _min_coverage_required({"tailoring": {"mode": "strict"}}) > 0.0


def test_strict_title_filter_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("APPLYPILOT_TAILOR_STRICT_TITLE_FILTER", raising=False)
    assert _strict_title_filter_enabled({"tailoring": {"mode": "strict"}}) is True
    monkeypatch.setenv("APPLYPILOT_TAILOR_STRICT_TITLE_FILTER", "0")
    assert _strict_title_filter_enabled({"tailoring": {"mode": "strict"}}) is False


def test_title_matches_focus_roles_behavior() -> None:
    roles = ["Data Analyst", "Analytics Engineer", "Business Intelligence Analyst"]
    assert _title_matches_focus_roles("Senior Data Analyst", roles) is True
    assert _title_matches_focus_roles("BI Developer", roles) is True
    assert _title_matches_focus_roles("Summer Intern", roles) is False


def test_title_matches_focus_roles_support_variations() -> None:
    roles = ["IT Support Analyst", "Application Support Engineer", "Systems Support Analyst"]
    assert _title_matches_focus_roles("1st / 2nd Line Support", roles) is True
    assert _title_matches_focus_roles("Service Desk Technician", roles) is True
    assert _title_matches_focus_roles("Helpdesk Analyst", roles) is True
    assert _title_matches_focus_roles("Summer Intern", roles) is False


def test_load_focus_roles_combines_profile_and_queries(monkeypatch) -> None:
    monkeypatch.setattr(
        "applypilot.scoring.tailor.load_search_config",
        lambda: {"queries": [{"query": "Data Engineer"}, {"query": "Insight Analyst"}]},
    )
    profile = {"experience": {"target_role": "Data Analyst / Analytics Engineer"}}
    roles = _load_focus_roles(profile)
    assert "Data Analyst" in roles
    assert "Analytics Engineer" in roles
    assert "Data Engineer" in roles
    assert "Insight Analyst" in roles


def test_fatal_error_classification() -> None:
    assert _is_fatal_json_error("Missing required field: summary") is True
    assert _is_fatal_json_error("Most recent role must have >= 3 bullets") is False
    assert _is_fatal_full_validation_error("Banned words: robust") is True
    assert _is_fatal_full_validation_error("Missing required section: PROJECTS") is False


def test_requirement_gate_defaults_and_overrides(monkeypatch) -> None:
    monkeypatch.delenv("APPLYPILOT_TAILOR_HARD_REQUIREMENT_GATE", raising=False)
    assert _hard_requirement_gating_enabled({"tailoring": {"mode": "strict"}}) is True
    assert _hard_requirement_gating_enabled({"tailoring": {"mode": "lenient"}}) is False
    assert _hard_requirement_gating_enabled({"tailoring": {"mode": "strict", "hard_requirement_gate": False}}) is False


def test_requirement_gap_threshold_defaults() -> None:
    strict_profile = {"tailoring": {"mode": "strict"}}
    assert _max_missing_must_haves(strict_profile) == 2
    assert _max_missing_domain_requirements(strict_profile) == 1


def test_current_role_alignment_toggle() -> None:
    assert _current_role_alignment_enabled({"tailoring": {}}) is True
    assert _current_role_alignment_enabled({"tailoring": {"align_current_role_header": False}}) is False
