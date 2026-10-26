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


def normalize_nz_salary(text, fields=None, convention=None):
    """Keep evidence and units; a bare dollar sign is not universally NZD."""
    from decimal import Decimal, InvalidOperation
    fields = fields or {}
    raw = str(text or "")[:3000]
    lower = raw.casefold()
    def amount(value):
        try:
            value = str(value).strip().lower().replace(",", "")
            number = Decimal(value.rstrip("k")) * (1000 if value.endswith("k") else 1)
            return float(number) if number.is_finite() and 0 < number <= 1e12 else None
        except (InvalidOperation, ValueError, TypeError):
            return None
    currencies = (("NZD", r"\bnzd\b|nz\s*\$"), ("AUD", r"\baud\b|au\s*\$"),
                  ("USD", r"\busd\b|us\s*\$"), ("GBP", r"\bgbp\b|£"), ("EUR", r"\beur\b|€"))
    mentioned = [code for code, pattern in currencies if re.search(pattern, lower)]
    currency = str(fields.get("currency") or "").upper()
    currency = currency if currency in {code for code, _ in currencies} else None
    basis = "provider" if currency else "explicit_text" if len(mentioned) == 1 else "unknown"
    currency = currency or (mentioned[0] if len(mentioned) == 1 else None)
    if not currency and "$" in raw and not mentioned and convention:
        currency, basis = convention.get("currency"), "source_convention"
    interval_text = str(fields.get("interval") or "").lower()
    aliases = {"yearly": "year", "annually": "year", "annual": "year", "monthly": "month", "weekly": "week", "daily": "day", "hourly": "hour"}
    interval = aliases.get(interval_text, interval_text if interval_text in _INTERVAL_MULTIPLIERS else None)
    if not interval:
        for name, pattern in (("hour", r"per hour|/\s*(?:hr|hour)\b|hourly"), ("day", r"per day|/\s*day\b|daily"),
                              ("week", r"per week|/\s*week\b|weekly"), ("month", r"per month|/\s*month\b|monthly"),
                              ("year", r"per annum|p\.?a\.?\b|per year|/\s*(?:yr|year)\b|annual|yearly")):
            if re.search(pattern, lower):
                interval = name
                break
    low, high = amount(fields.get("min_amount")), amount(fields.get("max_amount"))
    if low is None and high is None:
        principal = re.split(r"\+|\binclud(?:es|ing)\b|\bkiwisaver\b|\bhours?\b", raw, maxsplit=1, flags=re.I)[0]
        tokens = list(re.finditer(r"\d[\d,]*(?:\.\d+)?\s*[kK]?", principal))
        if tokens:
            low = amount(tokens[0].group(0).replace(" ", ""))
            if len(tokens) > 1 and re.fullmatch(r"\s*(?:-|–|—|to)\s*(?:(?:NZD|NZ\$|AUD|USD|GBP|EUR|\$|£|€)\s*)?", principal[tokens[0].end():tokens[1].start()], re.I):
                high = amount(tokens[1].group(0).replace(" ", ""))
                if "k" in tokens[1].group(0).lower() and "k" not in tokens[0].group(0).lower() and low is not None and low < 1000:
                    low *= 1000
    if low is None:
        low = high
    high = high if high is not None else low
    if low is not None and high is not None and low > high:
        low, high = high, low
    compensation_basis = next((key for key in ("package", "net", "gross", "base") if re.search(r"\b" + key + r"\b", lower)), "unknown")
    kiwi = "excluded" if re.search(r"(?:excl|excluding|without|plus|\+)\s*(?:employer\s+)?kiwisaver", lower) else "included" if re.search(r"(?:incl|includes|including).*kiwisaver", lower) else "unknown"
    conflict = bool(fields.get("currency") and mentioned and mentioned != [currency])
    status = "parsed" if low is not None and currency and interval and len(mentioned) <= 1 and not conflict else "unknown"
    hours = fields.get("hours_per_week")
    if not hours:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per\s*week|/\s*week|weekly)", lower)
        hours = amount(match.group(1)) if match else None
    return {"raw": raw, "min": low, "max": high, "currency": currency, "currency_basis": basis,
            "currency_evidence": convention.get("evidence") if basis == "source_convention" else None,
            "interval": interval, "basis": compensation_basis, "kiwisaver": kiwi, "parse_status": status,
            "stated_hours_per_week": hours, "annualisation_basis": None}


def nz_salary_ok(salary, preference=None):
    """Reject only a known comparable range. No default FX or 2080-hour rule."""
    if not preference or salary.get("parse_status") != "parsed":
        return True
    if salary.get("currency") != str(preference.get("currency") or preference.get("salary_currency") or "NZD").upper():
        return True
    target_basis = preference.get("basis", "unknown")
    if target_basis == "unknown" or salary.get("basis", "unknown") != target_basis:
        return True
    low, high = salary["min"], salary["max"]
    period = preference.get("interval", "year")
    if salary.get("interval") != period:
        conversion = preference.get("annualisation") or {}
        factor = conversion.get("factor")
        if period != "year" or conversion.get("confirmed") is not True or isinstance(factor, bool) or not isinstance(factor, (int, float)) or not 0 < factor <= 10000 or not str(conversion.get("evidence") or "").strip():
            return True
        low, high = low * factor, high * factor
        salary["annualisation_basis"] = {"factor": factor, "evidence": str(conversion["evidence"])[:1000], "source": "user-confirmed comparison assumption"}
    try:
        minimum = float(preference.get("min", preference.get("salary_range_min"))) if preference.get("min", preference.get("salary_range_min")) is not None else None
        maximum = float(preference.get("max", preference.get("salary_range_max"))) if preference.get("max", preference.get("salary_range_max")) is not None else None
        if (minimum is not None and not 0 <= minimum <= 1e12) or (maximum is not None and not 0 <= maximum <= 1e12):
            return True
    except (ValueError, TypeError):
        return True
    return _bounds_overlap(low, high, {"min": minimum, "max": maximum})
