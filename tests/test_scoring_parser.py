import sqlite3

from applypilot.scoring.scorer import _parse_score_response
from applypilot.scoring.scorer import _repair_zero_scores


def test_parse_score_response_json_object() -> None:
    raw = '{"score": 8, "keywords": ["python", "sql"], "reasoning": "Strong fit.", "confidence": 0.91}'
    got = _parse_score_response(raw)
    assert got["score"] == 8
    assert got["keywords"] == "python, sql"
    assert got["reasoning"] == "Strong fit."
    assert got["confidence"] == 0.91


def test_parse_score_response_truncated_json_recovers_score() -> None:
    raw = '\n```json\n{\n  "score": 6,\n  "keywords": ['
    got = _parse_score_response(raw)
    assert got["score"] == 6
    assert got["confidence"] == 0.6


def test_parse_score_response_legacy_line_format() -> None:
    raw = "SCORE: 7\nKEYWORDS: python, etl\nREASONING: good match\nCONFIDENCE: 0.8"
    got = _parse_score_response(raw)
    assert got["score"] == 7
    assert got["keywords"] == "python, etl"
    assert got["reasoning"] == "good match"
    assert got["confidence"] == 0.8


def test_repair_zero_scores_updates_recoverable_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE jobs (url TEXT PRIMARY KEY, fit_score INTEGER, score_confidence REAL, score_reasoning TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (url, fit_score, score_confidence, score_reasoning) VALUES (?, ?, ?, ?)",
        ("https://example.com/1", 0, 0.0, '\n```json\n{\n  "score": 7,\n  "keywords": ['),
    )
    conn.execute(
        "INSERT INTO jobs (url, fit_score, score_confidence, score_reasoning) VALUES (?, ?, ?, ?)",
        ("https://example.com/2", 0, 0.0, "LLM error: timeout"),
    )

    repaired = _repair_zero_scores(conn)

    assert repaired == 1
    row1 = conn.execute(
        "SELECT fit_score, score_confidence FROM jobs WHERE url=?", ("https://example.com/1",)
    ).fetchone()
    row2 = conn.execute("SELECT fit_score FROM jobs WHERE url=?", ("https://example.com/2",)).fetchone()
    assert row1 == (7, 0.6)
    assert row2 == (0,)
