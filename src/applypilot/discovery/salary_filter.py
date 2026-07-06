"""Shared salary filtering helpers for discovery.

These helpers apply the user's compensation bounds conservatively:
- If a salary cannot be parsed, keep the job.
- If a salary range overlaps the user's target range, keep the job.
- Reject only when the parsed compensation is clearly outside the user's range.
"""

from __future__ import annotations

import re
from typing import Any


_INTERVAL_MULTIPLIERS = {
    "year": 1.0,
    "yr": 1.0,
    "annual": 1.0,
    "annually": 1.0,
    "month": 12.0,
    "monthly": 12.0,
    "week": 52.0,
    "weekly": 52.0,
    "day": 260.0,
    "daily": 260.0,
    "hour": 2080.0,
    "hourly": 2080.0,
    "hr": 2080.0,
}

_UNSUPPORTED_RATE_MARKER_RE = re.compile(r"\b(?:per|a|an)\s+([a-z]+)\b", re.I)


def _parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None


def _detect_interval(text: str | None) -> str | None:
    s = str(text or "").strip().lower()
    if not s:
        return None
    if any(tok in s for tok in ("per year", "a year", "p.a", "pa", "per annum", "/year", "/yr", "annual")):
        return "year"
    if any(tok in s for tok in ("per month", "a month", "/month", "monthly")):
        return "month"
    if any(tok in s for tok in ("per week", "a week", "/week", "weekly")):
        return "week"
    if any(tok in s for tok in ("per day", "a day", "/day", "daily")):
        return "day"
    if any(tok in s for tok in ("per hour", "an hour", "a hour", "/hour", "/hr", "hourly")):
        return "hour"
    return s if s in _INTERVAL_MULTIPLIERS else None


def _has_unsupported_rate_marker(text: str | None) -> bool:
    s = str(text or "").strip().lower()
    if not s:
        return False
    for match in _UNSUPPORTED_RATE_MARKER_RE.finditer(s):
        unit = match.group(1).lower()
        if unit in {"annum", "hour", "day", "week", "month", "year", "yr", "hr"}:
            continue
        return True
    return False


def _annualize(amount: float | None, interval: str | None) -> float | None:
    if amount is None:
        return None
    key = _detect_interval(interval) or "year"
    mult = _INTERVAL_MULTIPLIERS.get(key)
    if mult is None:
        return None
    return amount * mult


def load_salary_preference(profile: dict | None) -> dict[str, Any] | None:
    comp = (profile or {}).get("compensation") or {}
    pref_min = _parse_amount(comp.get("salary_range_min") or comp.get("salary_expectation"))
    pref_max = _parse_amount(comp.get("salary_range_max"))
    currency = str(comp.get("salary_currency") or "").strip().upper() or None
    if pref_min is None and pref_max is None:
        return None
    return {"min": pref_min, "max": pref_max, "currency": currency}


def _bounds_overlap(low: float | None, high: float | None, pref: dict[str, Any] | None) -> bool:
    if not pref:
        return True
    if low is None and high is None:
        return True
    if low is None:
        low = high
    if high is None:
        high = low
    if low is None or high is None:
        return True
    if low > high:
        low, high = high, low

    pref_min = pref.get("min")
    pref_max = pref.get("max")
    if pref_min is not None and high < float(pref_min):
        return False
    if pref_max is not None and low > float(pref_max):
        return False
    return True


def jobspy_salary_ok(row: Any, pref: dict[str, Any] | None) -> bool:
    if not pref:
        return True
    currency = str(row.get("currency", "") or "").strip().upper()
    pref_currency = str(pref.get("currency") or "").strip().upper()
    if currency and pref_currency and currency != pref_currency:
        return True
    interval = str(row.get("interval", "") or "")
    low = _annualize(_parse_amount(row.get("min_amount")), interval)
    high = _annualize(_parse_amount(row.get("max_amount")), interval)
    return _bounds_overlap(low, high, pref)


def salary_text_ok(text: str | None, pref: dict[str, Any] | None) -> bool:
    if not pref:
        return True
    s = str(text or "").strip()
    if not s:
        return True

    pref_currency = str(pref.get("currency") or "").strip().upper()
    if pref_currency == "GBP" and "$" in s and "£" not in s:
        return True
    if pref_currency == "GBP" and "EUR" in s.upper() and "£" not in s:
        return True

    nums = []
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", s):
        try:
            nums.append(float(m.group(0).replace(",", "")))
        except Exception:
            continue
    if not nums:
        return True

    interval = _detect_interval(s)
    if interval is None and _has_unsupported_rate_marker(s):
        return True
    interval = interval or "year"
    low = _annualize(nums[0], interval)
    high = _annualize(nums[1], interval) if len(nums) > 1 else low
    return _bounds_overlap(low, high, pref)
