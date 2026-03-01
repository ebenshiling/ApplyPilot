from applypilot import naming


def test_cv_filename_prefers_name_over_username() -> None:
    personal = {"full_name": "Jane Alexandra Doe", "preferred_name": "Jane"}
    out = naming.cv_filename(personal, ext="pdf", username="jdoe")
    assert out == "Jane_Doe_CV.pdf"


def test_cv_filename_falls_back_to_username_when_name_missing() -> None:
    personal = {"full_name": "", "preferred_name": ""}
    out = naming.cv_filename(personal, ext="pdf", username="candidate_user")
    assert out == "candidate_user_CV.pdf"
