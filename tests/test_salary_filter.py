from applypilot.discovery.salary_filter import jobspy_salary_ok, load_salary_preference, salary_text_ok


def test_load_salary_preference_reads_bounds():
    pref = load_salary_preference(
        {
            "compensation": {
                "salary_range_min": "35000",
                "salary_range_max": "45000",
                "salary_currency": "GBP",
            }
        }
    )
    assert pref == {"min": 35000.0, "max": 45000.0, "currency": "GBP"}


def test_jobspy_salary_rejects_annual_range_below_min():
    pref = {"min": 35000.0, "max": 45000.0, "currency": "GBP"}
    row = {"min_amount": 28000, "max_amount": 32000, "interval": "yearly", "currency": "GBP"}
    assert jobspy_salary_ok(row, pref) is False


def test_jobspy_salary_accepts_overlapping_hourly_range():
    pref = {"min": 35000.0, "max": 45000.0, "currency": "GBP"}
    row = {"min_amount": 18, "max_amount": 22, "interval": "hourly", "currency": "GBP"}
    assert jobspy_salary_ok(row, pref) is True


def test_salary_text_rejects_annual_value_above_max():
    pref = {"min": 35000.0, "max": 45000.0, "currency": "GBP"}
    assert salary_text_ok("£55,000 to £65,000 per year", pref) is False


def test_salary_text_keeps_unparseable_competitive_salary():
    pref = {"min": 35000.0, "max": 45000.0, "currency": "GBP"}
    assert salary_text_ok("Competitive", pref) is True


def test_salary_text_accepts_supported_annual_phrase_a_year():
    pref = {"min": 38000.0, "max": 50000.0, "currency": "GBP"}
    assert salary_text_ok("£27,000 a year", pref) is False


def test_salary_text_accepts_supported_hourly_phrase_an_hour():
    pref = {"min": 38000.0, "max": 50000.0, "currency": "GBP"}
    assert salary_text_ok("£12.71 an hour", pref) is False


def test_salary_text_keeps_unsupported_rate_phrase():
    pref = {"min": 38000.0, "max": 50000.0, "currency": "GBP"}
    assert salary_text_ok("£51 to £326.50 a session", pref) is True
