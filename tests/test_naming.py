import hashlib

from applypilot import naming


def test_cv_filename_prefers_name_over_username() -> None:
    personal = {"full_name": "Jane Alexandra Doe", "preferred_name": "Jane"}
    out = naming.cv_filename(personal, ext="pdf", username="jdoe")
    assert out == "Jane_Doe_CV.pdf"


def test_cv_filename_falls_back_to_username_when_name_missing() -> None:
    personal = {"full_name": "", "preferred_name": ""}
    out = naming.cv_filename(personal, ext="pdf", username="candidate_user")
    assert out == "candidate_user_CV.pdf"


def test_cv_filename_includes_job_number_when_available() -> None:
    personal = {"full_name": "Jane Alexandra Doe", "preferred_name": "Jane"}
    job = {
        "job_id": 123,
        "title": "Data Analyst",
        "site": "LinkedIn",
        "url": "https://example.com/jobs/123",
    }
    out = naming.cv_filename(personal, ext="pdf", username="jdoe", job=job)
    assert out == f"Jane_Doe_CV_J123_{hashlib.sha1(job['url'].encode('utf-8')).hexdigest()[:8]}.pdf"


def test_display_name_does_not_duplicate_last_name_when_preferred_already_has_it() -> None:
    personal = {"full_name": "Ebenezer Otchere Brefo", "preferred_name": "Ebenezer Otchere Brefo"}
    assert naming.display_name(personal) == "Ebenezer Otchere Brefo"


def test_cover_letter_filename_uses_dashboard_id_fallback_key() -> None:
    personal = {"full_name": "Jane Alexandra Doe", "preferred_name": "Jane"}
    job = {
        "id": 77,
        "title": "BI Analyst",
        "site": "Indeed",
        "url": "https://example.com/jobs/bi-77",
    }
    out = naming.cover_letter_filename(personal, ext="pdf", username="jdoe", job=job)
    assert out == f"Jane_Doe_Cover_Letter_J77_{hashlib.sha1(job['url'].encode('utf-8')).hexdigest()[:8]}.pdf"
