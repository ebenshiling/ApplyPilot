from applypilot.scoring.tailor import _is_fatal_full_validation_error
from applypilot.scoring.tailor import _is_fatal_json_error
from applypilot.scoring.tailor import _lenient_tailor_enabled
from applypilot.scoring.tailor import _min_coverage_required
from applypilot.scoring.tailor import _strict_evidence_enabled


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


def test_fatal_error_classification() -> None:
    assert _is_fatal_json_error("Missing required field: summary") is True
    assert _is_fatal_json_error("Most recent role must have >= 3 bullets") is False
    assert _is_fatal_full_validation_error("Banned words: robust") is True
    assert _is_fatal_full_validation_error("Missing required section: PROJECTS") is False
