"""Assisted apply engine using Playwright.

This engine is intentionally conservative by default:
- It navigates to the application URL.
- It fills what it safely can (basic contact fields).
- It does NOT click the final Submit/Apply button unless explicitly enabled.

The goal is to reduce repetitive typing while keeping the human in control.

It runs at Tier 2 (LLM API key) by design (future: LLM-guided form mapping),
but the baseline behavior is safe even without LLM calls.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Any

from applypilot import config
from applypilot import naming
from applypilot.apply.chrome import launch_chrome, cleanup_worker, BASE_CDP_PORT
from applypilot.database import get_connection
from applypilot.llm import get_client

log = logging.getLogger(__name__)


@dataclass
class AssistResult:
    status: str
    duration_ms: int
    note: str = ""


@dataclass
class SubmitAttempt:
    submitted: bool
    page: Any
    reason: str = ""


def _js_click_best_apply_entry(page: Any) -> dict:
    """JS click the best "apply" entry control.

    Returns a dict with keys: clicked(bool), text(str), href(str), id(str).
    """
    try:
        return page.evaluate(
            r"""
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const normL = (s) => norm(s).toLowerCase();

              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };

              const candidates = [];

              // Prefer known IDs when present.
              const preferredIds = ['apply-bottom'];
              for (const id of preferredIds) {
                const el = document.getElementById(id);
                if (el && isVisible(el)) {
                  candidates.push(el);
                }
              }

              for (const el of Array.from(document.querySelectorAll('a, button, [role="button"], [role="link"]'))) {
                if (!isVisible(el)) continue;
                const t = normL(el.innerText || el.textContent || el.value);
                if (!t) continue;
                if (!(t.includes('apply') || t.includes('start application') || t.includes('begin application'))) continue;
                candidates.push(el);
              }

              const score = (el) => {
                const t = normL(el.innerText || el.textContent || el.value);
                const href = (el.getAttribute && el.getAttribute('href')) ? el.getAttribute('href') : '';
                const onclick = (el.getAttribute && el.getAttribute('onclick')) ? el.getAttribute('onclick') : '';
                let s = 0;
                if (el.id && el.id.toLowerCase().includes('apply')) s += 5;
                if (t.includes('apply now')) s += 6;
                if (t.includes('start application') || t.includes('begin application')) s += 4;
                if (href && href.toLowerCase().startsWith('javascript')) s += 10;
                if (onclick) s += 6;
                if (href && href.startsWith('#')) s -= 1; // anchors often only scroll
                return s;
              };

              let best = null;
              let bestScore = -1e9;
              for (const el of candidates) {
                const s = score(el);
                if (s > bestScore) {
                  best = el;
                  bestScore = s;
                }
              }

              if (!best) return {clicked: false, text: '', href: '', id: ''};

              try { best.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
              best.click();

              return {
                clicked: true,
                text: norm(best.innerText || best.textContent || best.value).slice(0, 80),
                href: (best.getAttribute && best.getAttribute('href')) ? best.getAttribute('href') : '',
                id: best.id || '',
              };
            }
            """
        )
    except Exception:
        return {"clicked": False, "text": "", "href": "", "id": ""}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _acquire_job(
    min_score: int,
    worker_id: int,
    target_url: str | None = None,
    *,
    allow_prepared: bool = False,
    lock: bool = True,
) -> dict | None:
    """Acquire a job for assisted apply.

    Uses the same general eligibility criteria as the Claude engine,
    but doesn't require apply_status == failed.
    """
    conn = get_connection()
    try:
        if lock:
            conn.execute("BEGIN IMMEDIATE")

        # Optional: allow users to skip specific jobs by title.
        try:
            search_cfg = config.load_search_config()
        except Exception:
            search_cfg = {}
        skip_titles = search_cfg.get("skip_titles", []) or []

        if target_url:
            like = f"%{target_url.split('?')[0].rstrip('/')}%"
            row = conn.execute(
                """
                SELECT url, title, site, application_url, tailored_resume_path,
                       fit_score, location, full_description, cover_letter_path
                FROM jobs
                WHERE (url = ? OR application_url = ? OR application_url LIKE ? OR url LIKE ?)
                  AND tailored_resume_path IS NOT NULL
                  AND applied_at IS NULL
                LIMIT 1
                """,
                (target_url, target_url, like, like),
            ).fetchone()
        else:
            statuses = ["failed", "ready", "manual"]
            if allow_prepared:
                statuses.append("prepared")

            placeholders = ", ".join(["?"] * len(statuses))

            # Prefer highest score first
            row = conn.execute(
                f"""
                SELECT url, title, site, application_url, tailored_resume_path,
                       fit_score, location, full_description, cover_letter_path
                FROM jobs
                WHERE tailored_resume_path IS NOT NULL
                  AND applied_at IS NULL
                  AND application_url IS NOT NULL
                  AND fit_score >= ?
                  AND (apply_status IS NULL OR apply_status IN ({placeholders}))
                ORDER BY fit_score DESC, url
                LIMIT 1
                """,
                (min_score, *statuses),
            ).fetchone()

        if row and skip_titles:
            title = str(row["title"] or "")
            t_low = title.lower()
            if any(st and str(st).lower() in t_low for st in skip_titles):
                # Mark skipped to keep the queue moving.
                if lock:
                    conn.execute(
                        """
                        UPDATE jobs
                           SET apply_status = 'skipped',
                               apply_error = 'skip_titles',
                               agent_id = NULL
                         WHERE url = ?
                        """,
                        (row["url"],),
                    )
                    conn.commit()
                else:
                    conn.rollback()
                return None

        if not row:
            if lock:
                conn.rollback()
            return None

        if not lock:
            return dict(row)

        conn.execute(
            """
            UPDATE jobs
               SET apply_status = 'in_progress',
                   agent_id = ?,
                   last_attempted_at = ?
             WHERE url = ?
            """,
            (f"llm-worker-{worker_id}", _now_iso(), row["url"]),
        )
        conn.commit()
        return dict(row)
    except Exception:
        if lock:
            conn.rollback()
        raise


def _mark_prepared(url: str, note: str, duration_ms: int | None = None) -> None:
    """Mark a job as prepared for manual submission."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE jobs
           SET apply_status = 'prepared',
               apply_error = ?,
               apply_duration_ms = ?,
               agent_id = NULL
         WHERE url = ?
        """,
        (note[:500] if note else None, duration_ms, url),
    )
    conn.commit()


def _mark_applied(url: str, note: str, duration_ms: int | None = None) -> None:
    """Mark a job as applied (best-effort automation)."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE jobs
           SET apply_status = 'applied',
               applied_at = ?,
               apply_error = ?,
               apply_duration_ms = ?,
               agent_id = NULL
         WHERE url = ?
        """,
        (_now_iso(), (note[:500] if note else None), duration_ms, url),
    )
    conn.commit()


def _mark_failed(url: str, reason: str, duration_ms: int | None = None) -> None:
    conn = get_connection()
    conn.execute(
        """
        UPDATE jobs
           SET apply_status = 'failed',
               apply_error = ?,
               apply_attempts = COALESCE(apply_attempts, 0) + 1,
               apply_duration_ms = ?,
               agent_id = NULL
         WHERE url = ?
        """,
        (reason[:500], duration_ms, url),
    )
    conn.commit()


def _assist_one_job(
    job: dict,
    worker_id: int,
    headless: bool,
    keep_open: bool,
    submit: bool,
) -> AssistResult:
    """Open application URL and fill safe basics.

    For now this does not do deep form understanding. It's a safe baseline
    that still saves time (resume upload + contact details are the common pain).
    """
    start = time.time()
    chrome_proc = None
    port = BASE_CDP_PORT + worker_id

    try:
        chrome_proc = launch_chrome(worker_id, port=port, headless=headless)

        # Connect Playwright to this Chrome via CDP
        from playwright.sync_api import sync_playwright

        target_url = job.get("application_url") or job.get("url")
        if not target_url:
            return AssistResult(status="failed", duration_ms=0, note="missing application_url")

        profile = config.load_profile()
        personal = profile.get("personal", {})

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

            # --- Heuristic fill (fast wins) ---

            # Best-effort fill: try common fields if visible
            def _fill_if_present(selectors: list[str], value: str) -> bool:
                if not value:
                    return False
                for sel in selectors:
                    loc = page.locator(sel)
                    try:
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.fill(value)
                            return True
                    except Exception:
                        continue
                return False

            _fill_if_present(
                [
                    'input[name="firstName"]',
                    'input[autocomplete="given-name"]',
                    'input[name*="first"][type="text"]',
                ],
                (personal.get("full_name", "").split(" ", 1)[0] if personal.get("full_name") else ""),
            )
            _fill_if_present(
                [
                    'input[name="lastName"]',
                    'input[autocomplete="family-name"]',
                    'input[name*="last"][type="text"]',
                ],
                (personal.get("full_name", "").split(" ")[-1] if personal.get("full_name") else ""),
            )
            _fill_if_present(
                [
                    'input[type="email"]',
                    'input[autocomplete="email"]',
                    'input[name*="email"]',
                ],
                personal.get("email", ""),
            )
            _fill_if_present(
                [
                    'input[type="tel"]',
                    'input[autocomplete="tel"]',
                    'input[name*="phone"]',
                ],
                personal.get("phone", ""),
            )

            # Try resume upload if there's a file input
            resume_path = job.get("tailored_resume_path")
            pdf_path = str(Path(resume_path).with_suffix(".pdf")) if resume_path else ""
            if pdf_path and Path(pdf_path).exists():
                pdf_path = _copy_resume_for_upload(pdf_path, personal, job=job)
                try:
                    file_inputs = page.locator('input[type="file"]')
                    if file_inputs.count() > 0:
                        file_inputs.first.set_input_files(pdf_path)
                except Exception:
                    pass

            # --- LLM-guided fill (assisted, no submit) ---
            try:
                fields = _extract_visible_fields(page, limit=40)
                if fields:
                    fills = _llm_plan_fills(job, personal, fields)
                    _apply_fills(page, fills)
            except Exception:
                # Never fail the whole job on LLM planning errors
                pass

            # Stop here unless submit mode is enabled.
            note = "prepared (assisted fill; review and submit manually)"
            if not submit:
                if keep_open:
                    # Keep Chrome open for the user to review/submit.
                    # Important: do NOT call browser.close(); it may close the remote browser.
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                    return AssistResult(
                        status="prepared",
                        duration_ms=int((time.time() - start) * 1000),
                        note=note,
                    )

                # If not keeping open, disconnect from CDP and then close Chrome.
                try:
                    browser.close()
                except Exception:
                    pass

                return AssistResult(
                    status="prepared",
                    duration_ms=int((time.time() - start) * 1000),
                    note=note,
                )

            # --- Submit mode (best-effort) ---
            submitted = False
            try:
                submitted, page = _maybe_submit_application(page, job=job, personal=personal, resume_pdf=pdf_path)
            except Exception:
                submitted = False

            note = (
                "submitted (llm submit mode; verify confirmation page)"
                if submitted
                else "prepared (submit mode could not confirm submission)"
            )
            if keep_open:
                # Keep Chrome open for the user to review/submit.
                # Important: do NOT call browser.close(); it may close the remote browser.
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                return AssistResult(
                    status="applied" if submitted else "prepared",
                    duration_ms=int((time.time() - start) * 1000),
                    note=note,
                )

            # If not keeping open, disconnect from CDP and then close Chrome.
            try:
                browser.close()
            except Exception:
                pass

            return AssistResult(
                status="applied" if submitted else "prepared",
                duration_ms=int((time.time() - start) * 1000),
                note=note,
            )
    except Exception as e:
        return AssistResult(status="failed", duration_ms=int((time.time() - start) * 1000), note=str(e))
    finally:
        if chrome_proc and not keep_open:
            cleanup_worker(worker_id, chrome_proc)


def _extract_visible_fields(page, limit: int = 40) -> list[dict]:
    """Extract a lightweight list of visible form fields.

    Returns a list of dicts with keys: tag, type, name, id, placeholder, label.
    """
    script = """
    (limit) => {
      const els = Array.from(document.querySelectorAll('input, textarea, select'));
      const out = [];
      const isVisible = (el) => {
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const getLabel = (el) => {
        const aria = el.getAttribute('aria-label');
        if (aria) return aria;
        const id = el.getAttribute('id');
        if (id) {
          const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
          if (l && l.textContent) return l.textContent.trim();
        }
        const parentLabel = el.closest('label');
        if (parentLabel && parentLabel.textContent) return parentLabel.textContent.trim();
        const ph = el.getAttribute('placeholder');
        return ph || '';
      };
      for (const el of els) {
        if (out.length >= limit) break;
        if (!isVisible(el)) continue;
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (tag === 'input' && ['hidden', 'submit', 'button', 'image'].includes(type)) continue;
        const label = getLabel(el);
        out.push({
          tag,
          type,
          name: el.getAttribute('name') || '',
          id: el.getAttribute('id') || '',
          placeholder: el.getAttribute('placeholder') || '',
          label,
          autocomplete: el.getAttribute('autocomplete') || '',
          required: !!el.required,
        });
      }
      return out;
    }
    """

    # Many ATS flows render the actual application inside an iframe.
    # Try the main frame and all child frames; return the first frame that has fields.
    frames: list[Any] = []
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        try:
            frames = list(page.frames())
        except Exception:
            frames = []

    if not frames:
        try:
            return page.evaluate(script, limit)
        except Exception:
            return []

    for fr in frames:
        try:
            fields = fr.evaluate(script, limit)
            if isinstance(fields, list) and fields:
                return fields
        except Exception:
            continue

    return []


def _profile_values(personal: dict) -> dict:
    full = (personal.get("full_name") or "").strip()
    parts = [p for p in full.split(" ") if p]
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    return {
        "first_name": first,
        "last_name": last,
        "full_name": full,
        "email": (personal.get("email") or "").strip(),
        "phone": (personal.get("phone") or "").strip(),
        "address": (personal.get("address") or "").strip(),
        "city": (personal.get("city") or "").strip(),
        "postal_code": (personal.get("postal_code") or "").strip(),
        "linkedin_url": (personal.get("linkedin_url") or "").strip(),
        "github_url": (personal.get("github_url") or "").strip(),
        "website_url": (personal.get("website_url") or "").strip(),
        "portfolio_url": (personal.get("portfolio_url") or "").strip(),
    }


def _copy_resume_for_upload(pdf_path: str, personal: dict, *, job: dict | None = None) -> str:
    """Copy a tailored resume PDF to a recruiter-friendly upload filename."""
    if not pdf_path:
        return ""

    src = Path(pdf_path)
    if not src.exists():
        return ""

    dest_dir = config.APPLY_WORKER_DIR / "current"
    dest_dir.mkdir(parents=True, exist_ok=True)
    username = str(os.environ.get("APPLYPILOT_USER", "") or "").strip()
    out = dest_dir / naming.cv_filename(personal, ext="pdf", username=username, job=job)

    try:
        if src.resolve() == out.resolve():
            return str(out)
    except Exception:
        pass

    try:
        out.write_bytes(src.read_bytes())
        return str(out)
    except Exception:
        return str(src)


def _llm_plan_fills(job: dict, personal: dict, fields: list[dict]) -> list[dict]:
    """Ask the LLM which fields to fill and with what values.

    Returns a list of fill actions: {"by": "id"|"name", "key": str, "value": str}
    """
    values = _profile_values(personal)
    # Only include non-empty values to reduce leakage and noise
    values = {k: v for k, v in values.items() if v}

    if not values:
        return []

    # Limit field list payload size
    compact_fields = []
    for f in fields[:40]:
        compact_fields.append(
            {
                "tag": f.get("tag", ""),
                "type": f.get("type", ""),
                "name": f.get("name", ""),
                "id": f.get("id", ""),
                "label": (f.get("label") or "")[:80],
                "placeholder": (f.get("placeholder") or "")[:80],
                "autocomplete": f.get("autocomplete", ""),
                "required": bool(f.get("required")),
            }
        )

    prompt = (
        "You help fill a job application form. "
        "This is ASSISTED mode: never submit the form. "
        "Choose a small set of obvious fields to fill with the applicant values. "
        "Only fill: first_name, last_name, full_name, email, phone, address, city, postal_code, linkedin_url, github_url, website_url, portfolio_url. "
        "Never fill passwords. Never guess missing values. "
        "Return ONLY valid JSON, no markdown.\n\n"
        f"JOB_TITLE: {job.get('title', '')}\n\n"
        f"APPLICANT_VALUES: {json.dumps(values, ensure_ascii=True)}\n\n"
        f"FIELDS: {json.dumps(compact_fields, ensure_ascii=True)}\n\n"
        "Output schema:\n"
        '{"fills":[{"by":"id"|"name","key":"...","value_key":"first_name"|...}],"notes":[...]}\n'
        "Rules: Use by=id if field has id else by=name if field has name. "
        "Use value_key from APPLICANT_VALUES keys. "
        "Prefer required fields. Max 12 fills."
    )

    client = get_client()
    raw = client.ask(prompt, temperature=0.0, max_tokens=800)
    data = _safe_json(raw)
    if not data or not isinstance(data, dict):
        return []
    fills = data.get("fills", [])
    if not isinstance(fills, list):
        return []

    out: list[dict] = []
    for item in fills[:12]:
        if not isinstance(item, dict):
            continue
        by = (item.get("by") or "").strip().lower()
        key = (item.get("key") or "").strip()
        value_key = (item.get("value_key") or "").strip()
        if by not in ("id", "name"):
            continue
        if not key:
            continue
        if value_key not in values:
            continue
        out.append({"by": by, "key": key, "value": values[value_key]})

    return out


def _safe_json(text: str) -> dict | None:
    """Parse JSON from an LLM response, tolerating leading/trailing junk."""
    if not text:
        return None
    s = text.strip()
    # Try direct parse
    try:
        return json.loads(s)
    except Exception:
        pass
    # Try to extract first JSON object
    m = re.search(r"\{.*\}", s, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _apply_fills(page, fills: list[dict]) -> None:
    # Apply fills across main page + frames (many ATS forms are embedded).
    frames: list[Any] = []
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        try:
            frames = list(page.frames())
        except Exception:
            frames = []

    targets: list[Any] = frames + [page]

    for f in fills:
        by = f.get("by")
        key = f.get("key")
        val = f.get("value")
        if not (by and key and val):
            continue
        if by == "id":
            sel = f'[id="{_css_escape(key)}"]'
        else:
            sel = f'[name="{_css_escape(key)}"]'

        filled = False
        for t in targets:
            try:
                loc = t.locator(sel)
                if loc.count() == 0:
                    continue
                el = loc.first
                if not el.is_visible():
                    continue
                try:
                    el.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                el.fill(str(val))
                filled = True
                break
            except Exception:
                continue
        if not filled:
            continue


def _css_escape(s: str) -> str:
    # Minimal escaping for attribute selectors
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _page_text(page) -> str:
    # Combine top page + a few frames. Confirmation pages and login walls
    # often render inside iframes.
    parts: list[str] = []

    try:
        t = (page.inner_text("body") or "").strip()
        if t:
            parts.append(t)
    except Exception:
        pass

    frames: list[Any] = []
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        try:
            frames = list(page.frames())
        except Exception:
            frames = []

    for fr in frames[:6]:
        try:
            txt = fr.evaluate(
                "() => (document.body && (document.body.innerText || document.body.textContent)) ? (document.body.innerText || document.body.textContent) : ''"
            )
            if isinstance(txt, str):
                txt = txt.strip()
            else:
                txt = ""
            if txt:
                parts.append(txt)
        except Exception:
            continue

    if parts:
        # Cap to avoid gigantic pages.
        return "\n".join(parts)[:80000]

    try:
        return page.content() or ""
    except Exception:
        return ""


def _looks_submitted(page) -> bool:
    txt = _page_text(page).lower()
    if not txt:
        return False
    # Common success markers across ATS.
    return any(
        k in txt
        for k in (
            "application received",
            "application submitted",
            "successfully submitted",
            "thank you for applying",
            "thank you for your application",
            "we have received your application",
            "your application has been received",
        )
    )


def _click_by_text(page: Any, texts: list[str]) -> bool:
    """Click a visible control by label text.

    Tries multiple strategies because many ATS pages use non-semantic elements
    (e.g. <div role="button">, styled <a>, extra whitespace, etc.).
    """

    def _phrase_re(s: str) -> re.Pattern:
        parts = [p for p in re.split(r"\s+", (s or "").strip()) if p]
        if not parts:
            return re.compile(r"$a")
        core = r"\\s+".join(re.escape(p) for p in parts)
        # Match as a phrase anywhere in the accessible name/text.
        return re.compile(rf"{core}", re.I)

    patterns = [_phrase_re(t) for t in texts if (t or "").strip()]
    if not patterns:
        return False

    # Support both Page and Frame.
    # - Page: iterate all frames (including main frame)
    # - Frame: operate on that frame + its child frames
    frame_list: list[Any] = []

    # Heuristic: Playwright Page has main_frame; Frame does not.
    is_page = bool(hasattr(page, "main_frame"))

    if is_page:
        try:
            frame_list = list(page.frames)
        except Exception:
            try:
                frame_list = list(page.frames())
            except Exception:
                frame_list = []
        if not frame_list:
            try:
                frame_list = [page.main_frame]
            except Exception:
                frame_list = []
    else:
        frame_list = [page]
        try:
            # Include child frames if present.
            kids = list(getattr(page, "child_frames", []) or [])
            frame_list.extend(kids)
        except Exception:
            pass

    for frame in frame_list:
        for pat in patterns:
            # Role-based click (best when accessible name is correct)
            for role in ("button", "link"):
                try:
                    loc = frame.get_by_role(role, name=pat)
                    if loc.count():
                        el = loc.first
                        if el.is_visible():
                            try:
                                el.scroll_into_view_if_needed(timeout=2000)
                            except Exception:
                                pass
                            try:
                                el.click(timeout=5000)
                            except Exception:
                                el.click(timeout=5000, force=True)
                            return True
                except Exception:
                    pass

            # Generic clickable elements by visible text
            try:
                loc = frame.locator("button, a, [role='button'], [role='link']").filter(has_text=pat)
                if loc.count():
                    el = loc.first
                    if el.is_visible():
                        try:
                            el.scroll_into_view_if_needed(timeout=2000)
                        except Exception:
                            pass
                        try:
                            el.click(timeout=5000)
                        except Exception:
                            el.click(timeout=5000, force=True)
                        return True
            except Exception:
                pass

            # Inputs with value attribute
            try:
                inputs = frame.locator("input[type='submit'], input[type='button']")
                n = inputs.count()
                for i in range(min(n, 20)):
                    el = inputs.nth(i)
                    try:
                        val = (el.get_attribute("value") or "").strip()
                    except Exception:
                        val = ""
                    if not val or not pat.search(val):
                        continue
                    if not el.is_visible():
                        continue
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        el.click(timeout=5000)
                    except Exception:
                        el.click(timeout=5000, force=True)
                    return True
            except Exception:
                pass

            # JS-driven click (bypasses accessibility/locator quirks)
            try:
                clicked = frame.evaluate(
                    r"""
                    (phrase) => {
                      const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                      const needle = norm(phrase);
                      if (!needle) return false;

                      const els = Array.from(document.querySelectorAll(
                        "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']"
                      ));
                      const isVisible = (el) => {
                        const r = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                      };
                      for (const el of els) {
                        if (!isVisible(el)) continue;
                        const txt = norm(el.innerText || el.textContent || el.value);
                        if (!txt) continue;
                        if (txt.includes(needle)) {
                          try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
                          el.click();
                          return true;
                        }
                      }
                      return false;
                    }
                    """,
                    pat.pattern.replace("\\\\s+", " "),
                )
                if clicked:
                    return True
            except Exception:
                pass

            # Last-resort text match
            try:
                loc = frame.get_by_text(pat)
                if loc.count():
                    el = loc.first
                    if el.is_visible():
                        try:
                            el.scroll_into_view_if_needed(timeout=2000)
                        except Exception:
                            pass
                        try:
                            el.click(timeout=5000)
                        except Exception:
                            el.click(timeout=5000, force=True)
                        return True
            except Exception:
                pass

    return False


def _ats_frame(page: Any) -> Any | None:
    """Return a likely ATS frame to interact with (if embedded)."""
    frames: list[Any] = []
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        try:
            frames = list(page.frames())
        except Exception:
            frames = []

    # Prefer known ATS hosts.
    for fr in frames:
        try:
            u = (fr.url or "").lower()
        except Exception:
            u = ""
        if any(k in u for k in ("apply.talemetry.com", "talemetry", "greenhouse", "myworkdayjobs", "workday")):
            return fr

    # Some pages render an iframe element that hasn't yet loaded in the frames list.
    # Prefer the iframe element whose src matches known ATS hosts.
    try:
        handles = page.locator("iframe").element_handles()
    except Exception:
        handles = []
    for h in handles[:12]:
        try:
            src = (h.get_attribute("src") or "").lower()
        except Exception:
            src = ""
        if any(k in src for k in ("apply.talemetry.com", "talemetry", "greenhouse", "myworkdayjobs", "workday")):
            try:
                fr = h.content_frame()
            except Exception:
                fr = None
            if fr is not None:
                return fr

    # Otherwise pick the first frame with visible-ish form elements.
    for fr in frames:
        try:
            has_form = fr.evaluate(
                """
                () => {
                  const el = document.querySelector('form, input, textarea, select');
                  if (!el) return false;
                  const r = el.getBoundingClientRect();
                  const style = window.getComputedStyle(el);
                  return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                }
                """
            )
            if has_form:
                return fr
        except Exception:
            continue

    return None


def _click_exact_text(target: Any, labels: list[str]) -> bool:
    """Click a control whose visible text exactly matches one of labels."""
    for lab in labels:
        s = (lab or "").strip()
        if not s:
            continue
        pat = re.compile(rf"^\\s*{re.escape(s)}\\s*$", re.I)
        # Try role-based first.
        for role in ("button", "link"):
            try:
                loc = target.get_by_role(role, name=pat)
                n = loc.count()
                for i in range(min(n, 8)):
                    el = loc.nth(i)
                    try:
                        if not el.is_visible():
                            continue
                    except Exception:
                        continue
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        el.click(timeout=5000)
                    except Exception:
                        el.click(timeout=5000, force=True)
                    return True
            except Exception:
                pass

        # Try generic clickables filtered by exact text.
        try:
            loc = target.locator(
                "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']"
            ).filter(has_text=pat)
            n = loc.count()
            for i in range(min(n, 8)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                except Exception:
                    continue
                try:
                    el.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    el.click(timeout=5000)
                except Exception:
                    el.click(timeout=5000, force=True)
                return True
        except Exception:
            pass

        # Fallback to exact text.
        try:
            loc = target.get_by_text(pat)
            n = loc.count()
            for i in range(min(n, 8)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                except Exception:
                    continue
                try:
                    el.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    el.click(timeout=5000)
                except Exception:
                    el.click(timeout=5000, force=True)
                return True
        except Exception:
            pass

    return False


def _js_click_exact_text(target: Any, labels: list[str]) -> dict:
    """JS click a visible clickable whose text matches a label exactly."""
    labs = [((s or "").strip().lower()) for s in (labels or []) if (s or "").strip()]
    if not labs:
        return {"clicked": False, "text": ""}

    try:
        return target.evaluate(
            r"""
            (labels) => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const normL = (s) => norm(s).toLowerCase();
              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const els = Array.from(document.querySelectorAll(
                "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']"
              ));
              for (const el of els) {
                if (!isVisible(el)) continue;
                const t = normL(el.innerText || el.textContent || el.value);
                if (!t) continue;
                if (labels.includes(t)) {
                  try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
                  el.click();
                  return {clicked: true, text: norm(el.innerText || el.textContent || el.value).slice(0, 80)};
                }
              }
              return {clicked: false, text: ''};
            }
            """,
            labs,
        )
    except Exception:
        return {"clicked": False, "text": ""}

    return {"clicked": False, "text": ""}


def _js_click_forward_action(target: Any) -> dict:
    """Click a likely forward navigation action (next/continue/review) in a doc.

    Avoids "continue later", "cancel", "back" style actions.
    Returns dict: clicked(bool), text(str)
    """
    try:
        return target.evaluate(
            r"""
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const normL = (s) => norm(s).toLowerCase();

              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };

              const els = Array.from(document.querySelectorAll(
                "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']"
              )).filter(isVisible);

              const isDisabled = (el) => {
                try {
                  if (el.disabled) return true;
                } catch (e) {}
                try {
                  const aria = (el.getAttribute && el.getAttribute('aria-disabled')) ? el.getAttribute('aria-disabled') : '';
                  if ((aria || '').toLowerCase() === 'true') return true;
                } catch (e) {}
                try {
                  const cls = normL(el.className || '');
                  if (cls.includes('disabled') || cls.includes('is-disabled')) return true;
                } catch (e) {}
                try {
                  const style = window.getComputedStyle(el);
                  if ((style.pointerEvents || '').toLowerCase() === 'none') return true;
                  if ((style.cursor || '').toLowerCase() === 'not-allowed') return true;
                  const op = parseFloat(style.opacity || '1');
                  if (!isNaN(op) && op < 0.25) return true;
                } catch (e) {}
                return false;
              };

              const avoid = (t) =>
                t.includes('later') || t.includes('cancel') || t.includes('close') || t.includes('back') ||
                t.includes('previous') || t.includes('exit') || t.includes('decline') || t.includes('no thanks');

              const score = (t) => {
                if (!t) return -1e9;
                if (avoid(t)) return -1e9;
                let s = 0;
                if (t === 'continue') s += 30;
                if (t === 'next') s += 28;
                if (t === 'review') s += 20;
                if (t === 'save and continue') s += 18;
                if (t.startsWith('continue')) s += 14;
                if (t.startsWith('next')) s += 12;
                if (t.includes('continue')) s += 8;
                if (t.includes('next')) s += 7;
                if (t.includes('review')) s += 6;
                if (t.includes('submit')) s += 4; // sometimes forward is the only button
                return s;
              };

              let best = null;
              let bestScore = -1e9;
              for (const el of els) {
                if (isDisabled(el)) continue;
                const t = normL(el.innerText || el.textContent || el.value);
                const s = score(t);
                if (s > bestScore) {
                  best = el;
                  bestScore = s;
                }
              }
              if (!best || bestScore < 10) return {clicked: false, text: ''};
              try { best.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
              best.click();
              return {clicked: true, text: norm(best.innerText || best.textContent || best.value).slice(0, 80)};
            }
            """
        )
    except Exception:
        return {"clicked": False, "text": ""}


def _js_accept_privacy_consent(target: Any) -> bool:
    """Best-effort accept privacy/consent checkboxes in embedded ATS."""
    try:
        return bool(
            target.evaluate(
                r"""
                () => {
                  const normL = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                  const isVisible = (el) => {
                    const r = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                  };

                  const wants = (txt) => {
                    const t = normL(txt);
                    return (
                      t.includes('privacy') || t.includes('recruitment privacy') || t.includes('consent') ||
                      t.includes('i agree') || t.includes('i accept') || t.includes('i acknowledge') ||
                      t.includes('terms')
                    );
                  };

                  let clicked = false;

                  // Role-based checkboxes (custom components).
                  const roleCbs = Array.from(document.querySelectorAll('[role="checkbox"]')).filter(isVisible);
                  for (const el of roleCbs) {
                    const t = normL(el.innerText || el.textContent);
                    const aria = normL(el.getAttribute('aria-checked'));
                    if (aria === 'true') continue;
                    if (wants(t) || wants(el.getAttribute('aria-label') || '')) {
                      el.click();
                      clicked = true;
                    }
                  }

                  // Prefer labels that mention privacy/consent.
                  const labels = Array.from(document.querySelectorAll('label')).filter(isVisible);
                  for (const lab of labels) {
                    if (!wants(lab.innerText || lab.textContent)) continue;
                    const f = lab.getAttribute('for');
                    if (f) {
                      const cb = document.getElementById(f);
                      if (cb && cb.type === 'checkbox' && !cb.checked) {
                        cb.click();
                        clicked = true;
                      }
                    } else {
                      const cb = lab.querySelector('input[type="checkbox"]');
                      if (cb && !cb.checked) {
                        cb.click();
                        clicked = true;
                      }
                    }
                  }

                  // If nothing matched, tick any single visible unchecked checkbox (common on privacy step).
                  if (!clicked) {
                    const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(isVisible);
                    if (cbs.length === 1 && !cbs[0].checked) {
                      cbs[0].click();
                      clicked = true;
                    }
                  }

                  // Last resort: click the first visible unchecked checkbox.
                  if (!clicked) {
                    const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(isVisible);
                    for (const cb of cbs) {
                      if (!cb.checked) {
                        cb.click();
                        clicked = true;
                        break;
                      }
                    }
                  }

                  return clicked;
                }
                """
            )
        )
    except Exception:
        return False


def _js_talemetry_privacy_unblock(target: Any) -> dict:
    """Best-effort unblock Talemetry 'Recruitment Privacy Notice' step.

    Talemetry sometimes requires scrolling a notice container to the bottom and/or
    clicking an acknowledge/accept action before CONTINUE advances.
    Returns a dict for logging.
    """
    try:
        return target.evaluate(
            r"""
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const normL = (s) => norm(s).toLowerCase();
              const bodyText = normL(document.body ? (document.body.innerText || document.body.textContent) : '');
              const matched = bodyText.includes('recruitment privacy notice') ||
                              (bodyText.includes('privacy notice') && bodyText.includes('continue'));
              if (!matched) return {matched: false, did_scroll: false, did_accept: false, did_click_continue: false, overlay: ''};

              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const clickablesSel = "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']";

              // 1) Scroll the most likely notice container to bottom.
              let did_scroll = false;
              let scrollCandidates = [];
              try {
                const els = Array.from(document.querySelectorAll('*')).slice(0, 2500);
                for (const el of els) {
                  if (!isVisible(el)) continue;
                  const style = window.getComputedStyle(el);
                  const oy = (style.overflowY || '').toLowerCase();
                  if (!(oy.includes('auto') || oy.includes('scroll'))) continue;
                  const sh = el.scrollHeight || 0;
                  const ch = el.clientHeight || 0;
                  if (sh <= ch + 80) continue;
                  const t = normL(el.innerText || el.textContent || '');
                  let s = Math.min(2000, sh - ch);
                  if (t.includes('privacy')) s += 500;
                  if (t.includes('recruitment')) s += 200;
                  if (t.includes('notice')) s += 150;
                  scrollCandidates.push({el, score: s});
                }
              } catch (e) {}
              scrollCandidates.sort((a, b) => b.score - a.score);
              if (scrollCandidates.length) {
                const el = scrollCandidates[0].el;
                try {
                  el.scrollTop = el.scrollHeight;
                  el.dispatchEvent(new Event('scroll', {bubbles: true}));
                  did_scroll = true;
                } catch (e) {}
              }

              const avoid = (t) =>
                t.includes('later') || t.includes('cancel') || t.includes('close') || t.includes('back') ||
                t.includes('previous') || t.includes('exit') || t.includes('decline') || t.includes('no thanks');

              // 2) Click any explicit accept/acknowledge action if present.
              let did_accept = false;
              try {
                const els = Array.from(document.querySelectorAll(clickablesSel));
                for (const el of els) {
                  if (!isVisible(el)) continue;
                  const t = normL(el.innerText || el.textContent || el.value);
                  if (!t) continue;
                  if (avoid(t)) continue;
                  if (
                    t === 'i agree' || t === 'agree' || t === 'accept' || t === 'i accept' ||
                    t.includes('acknowledge') || t.includes('consent')
                  ) {
                    try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
                    try { el.click(); did_accept = true; } catch (e) {}
                    break;
                  }
                }
              } catch (e) {}

              // 3) Click CONTINUE with a more user-like event sequence.
              let did_click_continue = false;
              let overlay = '';
              try {
                const els = Array.from(document.querySelectorAll(clickablesSel));
                for (const el of els) {
                  if (!isVisible(el)) continue;
                  const t = normL(el.innerText || el.textContent || el.value);
                  if (t !== 'continue') continue;
                  try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
                  try { el.focus(); } catch (e) {}
                  try {
                    const r = el.getBoundingClientRect();
                    const cx = Math.floor(r.left + r.width / 2);
                    const cy = Math.floor(r.top + r.height / 2);
                    const top = document.elementFromPoint(cx, cy);
                    if (top && top !== el && !(el.contains && el.contains(top))) {
                      overlay = (top.tagName || '').toLowerCase() + (top.id ? ('#' + top.id) : '') + (top.className ? ('.' + String(top.className).split(/\s+/).slice(0, 2).join('.')) : '');
                    }
                    const targetEl = (top && (top === el || (el.contains && el.contains(top)))) ? top : el;
                    const evs = ['pointerdown', 'mousedown', 'mouseup', 'click'];
                    for (const type of evs) {
                      targetEl.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                    }
                  } catch (e) {}
                  try { el.click(); } catch (e) {}
                  did_click_continue = true;
                  break;
                }
              } catch (e) {}

              return {matched: true, did_scroll, did_accept, did_click_continue, overlay};
            }
            """
        )
    except Exception:
        return {"matched": False, "did_scroll": False, "did_accept": False, "did_click_continue": False, "overlay": ""}


def _ats_step_state(target: Any) -> dict:
    """Return basic state hints from current ATS step (for debugging)."""
    try:
        return target.evaluate(
            r"""
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const normL = (s) => norm(s).toLowerCase();
              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const clickables = Array.from(document.querySelectorAll(
                "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']"
              )).filter(isVisible);

              const findForward = () => {
                for (const el of clickables) {
                  const t = normL(el.innerText || el.textContent || el.value);
                  if (t === 'continue' || t === 'next' || t === 'review' || t.includes('save and continue')) {
                    const disabled = !!(el.disabled);
                    const aria = normL(el.getAttribute('aria-disabled') || '');
                    return {found: true, text: norm(t), disabled, aria_disabled: aria};
                  }
                }
                return {found: false};
              };

              const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(isVisible);
              const unchecked = cbs.filter((c) => !c.checked).length;
              const roleCbs = Array.from(document.querySelectorAll('[role="checkbox"]')).filter(isVisible);
              const roleUnchecked = roleCbs.filter((c) => normL(c.getAttribute('aria-checked')) !== 'true').length;
              return {
                forward: findForward(),
                checkboxes: {total: cbs.length, unchecked},
                role_checkboxes: {total: roleCbs.length, unchecked: roleUnchecked},
              };
            }
            """
        )
    except Exception:
        return {}


def _frame_snapshot_text(target: Any, max_len: int = 1200) -> str:
    """Small normalized snapshot of the current document text."""
    try:
        s = target.evaluate(
            r"""
            () => {
              const t = (document.body && (document.body.innerText || document.body.textContent))
                ? (document.body.innerText || document.body.textContent)
                : '';
              return (t || '').replace(/\s+/g, ' ').trim();
            }
            """
        )
        if not isinstance(s, str):
            return ""
        return s[: max(0, int(max_len))]
    except Exception:
        return ""


def _wait_for_snapshot_change(target: Any, before: str, timeout_s: float = 8.0) -> bool:
    """Wait for the target document text snapshot to change."""
    start = time.time()
    before = (before or "").strip()
    while time.time() - start < timeout_s:
        cur = _frame_snapshot_text(target, max_len=max(400, len(before) or 1200))
        if cur and before and cur != before:
            return True
        if before and (not cur):
            # Document transitioned (navigation/blank)
            return True
        time.sleep(0.2)
    return False


def _wait_for_target_progress(target: Any, before_snapshot: str, timeout_s: float = 8.0) -> bool:
    """Wait for a likely in-frame progress signal after a click.

    Tries: snapshot change, URL change (best-effort), or presence of common form controls.
    """
    start = time.time()
    before_snapshot = (before_snapshot or "").strip()
    try:
        before_url = (getattr(target, "url", "") or "").strip()
    except Exception:
        before_url = ""

    while time.time() - start < timeout_s:
        try:
            cur_url = (getattr(target, "url", "") or "").strip()
        except Exception:
            cur_url = ""
        if before_url and cur_url and cur_url != before_url:
            return True

        cur = _frame_snapshot_text(target, max_len=max(400, len(before_snapshot) or 1200))
        if cur and before_snapshot and cur != before_snapshot:
            return True
        if before_snapshot and (not cur):
            return True

        try:
            if target.locator("input, textarea, select, form").count() > 0:
                # If the snapshot was empty or generic and we now see form controls, treat as progress.
                if not before_snapshot:
                    return True
        except Exception:
            pass

        time.sleep(0.2)
    return False


def _js_click_forward_prefer_continue(target: Any) -> dict:
    """Prefer clicking CONTINUE/NEXT over CONTINUE LATER/CANCEL."""
    try:
        return target.evaluate(
            r"""
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const normL = (s) => norm(s).toLowerCase();
              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const isDisabled = (el) => {
                try { if (el.disabled) return true; } catch (e) {}
                try {
                  const aria = (el.getAttribute && el.getAttribute('aria-disabled')) ? el.getAttribute('aria-disabled') : '';
                  if ((aria || '').toLowerCase() === 'true') return true;
                } catch (e) {}
                try {
                  const cls = normL(el.className || '');
                  if (cls.includes('disabled') || cls.includes('is-disabled')) return true;
                } catch (e) {}
                try {
                  const style = window.getComputedStyle(el);
                  if ((style.pointerEvents || '').toLowerCase() === 'none') return true;
                  if ((style.cursor || '').toLowerCase() === 'not-allowed') return true;
                  const op = parseFloat(style.opacity || '1');
                  if (!isNaN(op) && op < 0.25) return true;
                } catch (e) {}
                return false;
              };

              const els = Array.from(document.querySelectorAll(
                "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']"
              ));

              const avoid = (t) =>
                t.includes('later') || t.includes('cancel') || t.includes('close') || t.includes('back') ||
                t.includes('previous') || t.includes('exit') || t.includes('decline') || t.includes('no thanks');

              const score = (t) => {
                if (!t) return -1e9;
                if (avoid(t)) return -1e9;
                if (t === 'continue') return 100;
                if (t === 'next') return 90;
                if (t === 'review') return 70;
                if (t.includes('continue')) return 40;
                if (t.includes('next')) return 35;
                if (t.includes('review')) return 30;
                return -1e9;
              };

              let best = null;
              let bestScore = -1e9;
              for (const el of els) {
                if (!isVisible(el)) continue;
                if (isDisabled(el)) continue;
                const t = normL(el.innerText || el.textContent || el.value);
                const s = score(t);
                if (s > bestScore) {
                  best = el;
                  bestScore = s;
                }
              }
              if (!best || bestScore < 10) return {clicked: false, text: ''};
              try { best.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
              best.click();
              return {clicked: true, text: norm(best.innerText || best.textContent || best.value).slice(0, 80)};
            }
            """
        )
    except Exception:
        return {"clicked": False, "text": ""}


def _js_click_primary_action(target: Any) -> dict:
    """JS click a likely primary action button in the current document.

    Returns dict: clicked(bool), text(str)
    """
    try:
        return target.evaluate(
            r"""
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const normL = (s) => norm(s).toLowerCase();

              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };

              const els = Array.from(document.querySelectorAll(
                "button, a, [role='button'], input[type='submit'], input[type='button']"
              )).filter(isVisible);

              const score = (el) => {
                const text = normL(el.innerText || el.textContent || el.value);
                const cls = normL(el.className || '');
                const id = normL(el.id || '');
                const type = normL(el.getAttribute('type') || '');
                let s = 0;

                // Hard-avoid destructive / negative actions.
                if (
                  text.includes('cancel') || text.includes('close') || text.includes('decline') ||
                  text.includes('no thanks') || text.includes('not now') || text.includes('exit') ||
                  text.includes('back') || text.includes('previous')
                ) {
                  return -1e9;
                }

                if (type === 'submit') s += 8;
                if (cls.includes('primary') || cls.includes('btn-primary')) s += 6;
                if (id.includes('next') || id.includes('continue') || id.includes('submit')) s += 4;
                if (cls.includes('next') || cls.includes('continue') || cls.includes('submit')) s += 3;

                if (text.includes('submit')) s += 10;
                if (text.includes('finish') || text.includes('complete')) s += 8;
                if (text.includes('next') || text.includes('continue') || text.includes('review')) s += 7;
                if (text.includes('apply')) s += 6;
                if (text.includes('get started') || text.includes('start')) s += 5;
                if (text.includes('later')) s -= 6;
                if (text.includes('save')) s += 2;

                // Prefer larger, likely primary CTAs.
                try {
                  const r = el.getBoundingClientRect();
                  s += Math.min(6, Math.floor((r.width * r.height) / 20000));
                } catch (e) {}

                return s;
              };

              let best = null;
              let bestScore = -1e9;
              for (const el of els) {
                const s = score(el);
                if (s > bestScore) {
                  best = el;
                  bestScore = s;
                }
              }
              if (!best || bestScore < 6) return {clicked: false, text: ''};

              try { best.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
              best.click();
              return {clicked: true, text: norm(best.innerText || best.textContent || best.value).slice(0, 80)};
            }
            """
        )
    except Exception:
        return {"clicked": False, "text": ""}


def _dismiss_common_banners(page: Any) -> None:
    # Best-effort cookie/consent dismissal.
    try:
        _click_by_text(
            page,
            [
                "Accept all",
                "Accept All",
                "Accept",
                "I agree",
                "I Agree",
                "Agree",
                "Got it",
            ],
        )
    except Exception:
        pass


def _visible_clickables_sample(page: Any, limit: int = 24) -> list[str]:
    """Return a small sample of visible clickable labels for debugging."""
    try:
        return page.evaluate(
            """
            (limit) => {
              const els = Array.from(document.querySelectorAll(
                "button, a, [role='button'], [role='link'], input[type='submit'], input[type='button']"
              ));
              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const out = [];
              for (const el of els) {
                if (out.length >= limit) break;
                if (!isVisible(el)) continue;
                const txt = (el.innerText || el.textContent || el.value || '').trim();
                if (!txt) continue;
                out.push(txt.slice(0, 80));
              }
              return out;
            }
            """,
            limit,
        )
    except Exception:
        return []


def _frame_urls(page: Any, limit: int = 8) -> list[str]:
    """Best-effort list of frame URLs (excluding about:blank)."""
    out: list[str] = []
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        try:
            frames = list(page.frames())
        except Exception:
            frames = []

    for fr in frames[: limit * 2]:
        try:
            u = (fr.url or "").strip()
        except Exception:
            u = ""
        if not u or u == "about:blank":
            continue
        out.append(u[:160])
        if len(out) >= limit:
            break
    return out


def _apply_now_targets(page: Any, limit: int = 6) -> list[dict]:
    """Return candidate apply-now elements with href/data attributes."""
    try:
        return page.evaluate(
            r"""
            (limit) => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const els = Array.from(document.querySelectorAll(
                "a, button, [role='button'], [role='link']"
              ));
              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const out = [];
              for (const el of els) {
                if (!isVisible(el)) continue;
                const txt = norm(el.innerText || el.textContent || el.value);
                if (!txt) continue;
                if (!(txt.includes('apply') || txt.includes('start application') || txt.includes('begin application'))) continue;
                const attrs = {};
                for (const a of el.attributes) {
                  if (a.name.startsWith('data-')) attrs[a.name] = a.value;
                }
                out.push({
                  tag: el.tagName.toLowerCase(),
                  text: (el.innerText || el.textContent || el.value || '').trim().slice(0, 80),
                  href: el.getAttribute('href') || '',
                  id: el.id || '',
                  class: el.className || '',
                  aria: el.getAttribute('aria-label') || '',
                  onclick: (el.getAttribute('onclick') || '').slice(0, 120),
                  data: attrs,
                });
                if (out.length >= limit) break;
              }
              return out;
            }
            """,
            limit,
        )
    except Exception:
        return []


def _scroll_probe_for_inputs(page: Any, steps: int = 6) -> None:
    """Scroll down a bit to trigger lazy-loaded application widgets."""
    for _ in range(max(0, int(steps))):
        try:
            if page.locator("input, textarea, select, form").count() > 0:
                return
        except Exception:
            pass
        try:
            page.evaluate("() => window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
        except Exception:
            pass
        try:
            page.wait_for_timeout(350)
        except Exception:
            pass


def _looks_like_listing_page(page: Any) -> bool:
    """Heuristic: job description page with an Apply Now button."""
    # If any frame contains a form, treat this as NOT a listing page.
    try:
        for fr in list(getattr(page, "frames", []) or []):
            try:
                has_form = fr.evaluate(
                    """
                    () => {
                      const el = document.querySelector('form, input, textarea, select');
                      if (!el) return false;
                      const r = el.getBoundingClientRect();
                      const style = window.getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                    }
                    """
                )
                if has_form:
                    return False
            except Exception:
                continue
    except Exception:
        pass

    # If the page hosts an embedded ATS iframe (talemetry/greenhouse/workday/etc),
    # treat it as NOT a listing page even if the outer page still looks like one.
    try:
        for u in _frame_urls(page, limit=12):
            lu = (u or "").lower()
            if any(k in lu for k in ("apply.talemetry.com", "talemetry", "greenhouse", "myworkdayjobs", "workday")):
                return False
    except Exception:
        pass

    try:
        # If there is no visible form at all, it's likely the listing page.
        forms = page.locator("form")
        if forms.count() > 0 and forms.first.is_visible():
            return False
    except Exception:
        pass
    try:
        # Many listing pages have an Apply Now button.
        return bool(page.get_by_text(re.compile(r"apply\s*now", re.I)).first.is_visible())
    except Exception:
        return False


def _page_debug(page: Any) -> str:
    try:
        url = page.url or ""
    except Exception:
        url = ""
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    return f"url={url} title={title}".strip()


def _maybe_switch_to_new_page(page: Any, before_pages: list[Any], timeout_s: float = 4.0) -> Any:
    """If an action opened a new tab/window, switch to it."""
    try:
        ctx = page.context
    except Exception:
        return page

    start = time.time()
    while time.time() - start < timeout_s:
        try:
            cur_pages = list(ctx.pages)
        except Exception:
            return page

        if len(cur_pages) > len(before_pages):
            # Prefer the newest page that isn't the current one.
            for p in reversed(cur_pages):
                if p != page:
                    try:
                        p.wait_for_load_state("domcontentloaded", timeout=20000)
                    except Exception:
                        pass
                    return p
        time.sleep(0.1)

    return page


def _click_locator_expect_popup(page: Any, locator: Any, *, label: str = "") -> Any | None:
    """Click a locator and switch to popup/tab if one opens.

    Returns the popup page if detected, else None.
    """
    try:
        locator.first.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass

    # Playwright can surface popups opened via window.open. Many ATS flows do this.
    try:
        with page.expect_popup(timeout=4000) as pop:
            try:
                locator.first.click(timeout=5000)
            except Exception:
                locator.first.click(timeout=5000, force=True)
        p = pop.value
        try:
            p.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        if label:
            log.info("Entry: popup opened after %s", label)
        return p
    except Exception:
        # No popup detected; best-effort plain click.
        try:
            locator.first.click(timeout=5000)
        except Exception:
            try:
                locator.first.click(timeout=5000, force=True)
            except Exception:
                return None
        return None


def _wait_for_progress(page: Any, old_url: str, timeout_s: float = 6.0) -> None:
    """Wait briefly for navigation/flow to start after a click."""
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if old_url and (page.url or "") != old_url:
                return
        except Exception:
            return

        # Heuristic: application step pages usually contain form fields.
        # Check all frames (many ATS are embedded).
        try:
            frames = list(getattr(page, "frames", []) or [])
        except Exception:
            try:
                frames = list(page.frames())
            except Exception:
                frames = []

        for fr in frames or [page]:
            try:
                if fr.locator('input[type="file"], input[type="email"], input[type="tel"], form').count() > 0:
                    return
            except Exception:
                continue

        time.sleep(0.15)


def _scroll_to_hash_target(page: Any) -> bool:
    """If location.hash points to an element, scroll it into view."""
    try:
        h = page.evaluate("() => window.location.hash || ''")
    except Exception:
        return False
    if not isinstance(h, str) or not h.startswith("#") or len(h) < 2:
        return False
    target = h[1:]
    try:
        return bool(
            page.evaluate(
                """
                (id) => {
                  const el = document.getElementById(id) || document.querySelector(`[name="${CSS.escape(id)}"]`);
                  if (!el) return false;
                  try { el.scrollIntoView({behavior: 'instant', block: 'start'}); } catch (e) { el.scrollIntoView(true); }
                  return true;
                }
                """,
                target,
            )
        )
    except Exception:
        return False


def _enter_application_flow(page: Any) -> Any:
    """Try to move from job listing -> application flow (no final submit)."""
    _dismiss_common_banners(page)
    before_pages: list[Any] = []
    try:
        before_pages = list(getattr(getattr(page, "context", None), "pages", []) or [])
    except Exception:
        before_pages = []

    old_url = ""
    try:
        old_url = page.url or ""
    except Exception:
        old_url = ""

    # Scroll a bit first to trigger lazy-loaded widgets.
    try:
        _scroll_probe_for_inputs(page)
    except Exception:
        pass

    # Prefer known apply button IDs (A&M uses #apply-bottom which triggers JS).
    try:
        loc = page.locator("#apply-bottom")
        if loc.count() and loc.first.is_visible():
            pop = _click_locator_expect_popup(page, loc, label="#apply-bottom")
            if pop is not None:
                return pop
            log.info("Entry: clicked #apply-bottom")
    except Exception:
        pass

    clicked = _click_by_text(
        page,
        [
            "APPLY NOW",
            "Apply now",
            "Apply Now",
            "Start application",
            "Begin application",
            "Apply",
        ],
    )

    if not clicked:
        # Some sites attach JS handlers to anchors that don't expose good roles.
        info = _js_click_best_apply_entry(page)
        if info.get("clicked"):
            clicked = True
            log.info(
                "Entry: JS-clicked apply control text=%s id=%s href=%s",
                (info.get("text") or "")[:60],
                (info.get("id") or "")[:40],
                (info.get("href") or "")[:80],
            )

    if clicked:
        log.info("Entry: clicked apply/start")
    else:
        # Debug what we can see on the page; helps tune selectors for new sites.
        sample = _visible_clickables_sample(page)
        if sample:
            log.info("Entry: no click match; visible clickables sample: %s", " | ".join(sample[:12]))

    # Log targets we found for "apply" elements; many sites attach data-hrefs.
    targets = _apply_now_targets(page)
    if targets:
        try:
            log.info("Entry: apply targets: %s", json.dumps(targets, ensure_ascii=True)[:800])
        except Exception:
            pass

    page = _maybe_switch_to_new_page(page, before_pages)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass

    # Some sites (including A&M) use an on-page anchor like #start.
    try:
        if _scroll_to_hash_target(page):
            page.wait_for_timeout(300)
    except Exception:
        pass

    _wait_for_progress(page, old_url)
    fr_urls = _frame_urls(page)
    if fr_urls:
        log.info("Entry: frames=%s %s", len(fr_urls), " | ".join(fr_urls[:4]))
    return page


def _maybe_submit_application(page: Any, job: dict, personal: dict, resume_pdf: str) -> tuple[bool, Any]:
    """Best-effort: click through and attempt final submit.

    This is NOT guaranteed for complex ATS flows. It returns True only when
    we can detect a likely confirmation page.
    """
    log.info("Submit mode: starting %s", _page_debug(page))

    # Step 0: if we're on a job listing page, try to enter the application flow.
    page = _enter_application_flow(page)
    log.info("Submit mode: after entry %s", _page_debug(page))

    fr_urls = _frame_urls(page)
    if fr_urls:
        log.info("Submit mode: frames=%s %s", len(fr_urls), " | ".join(fr_urls[:4]))

    # Site-specific: A&M careers pages often require creating an account and
    # cannot be submitted without credentials. Detect and exit early.
    try:
        txt = (_page_text(page) or "").lower()
    except Exception:
        txt = ""
    if any(k in txt for k in ("create account", "sign in", "log in", "already have an account")):
        return False, page

    # Iterate through form pages.
    for step in range(12):
        _dismiss_common_banners(page)
        try:
            _scroll_to_hash_target(page)
        except Exception:
            pass
        log.info("Submit mode: step %s %s", step + 1, _page_debug(page))

        # Fail fast if we never left the job listing page.
        if step >= 3 and _looks_like_listing_page(page):
            sample = _visible_clickables_sample(page)
            if sample:
                log.info("Submit mode: still listing; clickables sample: %s", " | ".join(sample[:12]))
            log.info("Submit mode: still on listing page; stopping submit attempt")
            return False, page

        if _looks_submitted(page):
            return True, page

        # If an ATS is embedded in an iframe, aim interactions there.
        target_page: Any = page
        fr = _ats_frame(page)
        if fr is not None:
            target_page = fr

        try:
            tu = getattr(target_page, "url", "") or ""
            if tu:
                log.info("Submit mode: target url=%s", tu[:160])
        except Exception:
            pass

        # Talemetry: privacy notice page often requires scroll/accept before CONTINUE works.
        # Run this before checkbox consent (Talemetry may not use checkboxes).
        try:
            tu = (getattr(target_page, "url", "") or "").lower()
        except Exception:
            tu = ""
        if "talemetry" in tu:
            try:
                snap_before = _frame_snapshot_text(target_page)
                tinfo = _js_talemetry_privacy_unblock(target_page)
                if (
                    isinstance(tinfo, dict)
                    and tinfo.get("matched")
                    and (tinfo.get("did_scroll") or tinfo.get("did_accept") or tinfo.get("did_click_continue"))
                ):
                    log.info(
                        "Submit mode: talemetry privacy unblock=%s",
                        json.dumps(tinfo, ensure_ascii=True)[:200],
                    )
                    # If we clicked continue here, treat as forward action and wait for change.
                    if tinfo.get("did_click_continue"):
                        try:
                            _wait_for_snapshot_change(target_page, snap_before, timeout_s=8.0)
                        except Exception:
                            pass
            except Exception:
                pass

        # ATS privacy/consent step: tick agreement checkboxes if present.
        try:
            if _js_accept_privacy_consent(target_page):
                page.wait_for_timeout(250)
        except Exception:
            pass

        # Debug: clickable sample inside ATS frame.
        try:
            sample = _visible_clickables_sample(target_page)
            if sample:
                log.info("Submit mode: target clickables: %s", " | ".join(sample[:10]))
        except Exception:
            pass

        try:
            st = _ats_step_state(target_page)
            if st:
                log.info("Submit mode: target state: %s", json.dumps(st, ensure_ascii=True)[:400])
        except Exception:
            pass

        # If the user/application flow was canceled (common when mis-clicking), stop.
        try:
            if "application_canceled" in (page.url or ""):
                return False, page
        except Exception:
            pass

        # Re-upload resume if a new file input appears.
        try:
            if resume_pdf and Path(resume_pdf).exists():
                # File inputs often exist in an embedded ATS frame.
                targets: list[Any] = []
                try:
                    targets = list(getattr(page, "frames", []) or []) + [page]
                except Exception:
                    targets = [page]
                for t in targets:
                    try:
                        file_inputs = t.locator('input[type="file"]')
                        if file_inputs.count() > 0 and file_inputs.first.is_visible():
                            file_inputs.first.set_input_files(resume_pdf)
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        # Re-run fill planning on each step.
        try:
            fields = _extract_visible_fields(page, limit=60)
            if fields:
                fills = _llm_plan_fills(job, personal, fields)
                _apply_fills(page, fills)
        except Exception:
            pass

        # If a "Submit"-like button exists, try it first.
        before_pages: list[Any] = []
        try:
            before_pages = list(getattr(getattr(page, "context", None), "pages", []) or [])
        except Exception:
            before_pages = []
        try:
            old_url = getattr(page, "url", "") or ""
        except Exception:
            old_url = ""
        if _click_by_text(
            target_page, ["Submit", "Submit application", "Send application", "Finish", "Complete application"]
        ):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            page = _maybe_switch_to_new_page(page, before_pages)
            _wait_for_progress(page, old_url)
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            if _looks_submitted(page):
                return True, page
            # If not clearly submitted, continue trying next/continue buttons.

        # Some ATS (e.g., Taleo/Talemetry) use all-caps buttons.
        clicked_caps = _click_exact_text(target_page, ["CONTINUE", "NEXT", "SUBMIT", "FINISH", "COMPLETE"])
        if not clicked_caps:
            try:
                clicked_caps = bool(
                    _js_click_exact_text(target_page, ["CONTINUE", "NEXT", "SUBMIT", "FINISH", "COMPLETE"]).get(
                        "clicked"
                    )
                )
            except Exception:
                clicked_caps = False
        if clicked_caps:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            page = _maybe_switch_to_new_page(page, before_pages)
            _wait_for_progress(page, old_url)
            continue

        # Move forward in multi-step flows.
        before_pages = []
        try:
            before_pages = list(getattr(getattr(page, "context", None), "pages", []) or [])
        except Exception:
            before_pages = []
        try:
            old_url = getattr(page, "url", "") or ""
        except Exception:
            old_url = ""

        # Prefer exact matches for forward buttons to avoid clicking
        # "Continue later" when we mean "Continue".
        try:
            turl_before = getattr(target_page, "url", "") or ""
        except Exception:
            turl_before = ""

        snap_before = _frame_snapshot_text(target_page)

        forward_clicked = _click_exact_text(target_page, ["CONTINUE", "NEXT", "REVIEW", "Continue", "Next", "Review"])
        if not forward_clicked:
            try:
                j = _js_click_exact_text(target_page, ["CONTINUE", "NEXT", "REVIEW", "Continue", "Next", "Review"])
                forward_clicked = bool(j.get("clicked"))
                if forward_clicked:
                    log.info("Submit mode: JS exact forward click text=%s", str(j.get("text") or ""))
            except Exception:
                forward_clicked = False

        if (not forward_clicked) and _click_by_text(
            target_page, ["Save and continue", "Continue to next step", "Continue", "Next", "Review"]
        ):
            forward_clicked = True

        if not forward_clicked:
            try:
                j2 = _js_click_forward_prefer_continue(target_page)
                if j2.get("clicked"):
                    forward_clicked = True
                    log.info("Submit mode: JS prefer-forward click text=%s", str(j2.get("text") or ""))
            except Exception:
                pass

        if forward_clicked:
            log.info("Submit mode: forward_clicked=True")
            # If the outer page doesn't navigate, still wait for the embedded ATS
            # document to change.
            try:
                _wait_for_target_progress(target_page, snap_before, timeout_s=10.0)
            except Exception:
                pass
            try:
                turl_after = getattr(target_page, "url", "") or ""
                if turl_before or turl_after:
                    log.info("Submit mode: target url change %s -> %s", turl_before[:120], turl_after[:120])
            except Exception:
                pass
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            page = _maybe_switch_to_new_page(page, before_pages)
            _wait_for_progress(page, old_url)
            continue

        # More aggressive forward-action click for embedded ATS that uses generic button labels.
        try:
            info2 = _js_click_forward_action(target_page)
        except Exception:
            info2 = {"clicked": False}
        if info2.get("clicked"):
            try:
                log.info("Submit mode: JS forward click text=%s", str(info2.get("text") or ""))
            except Exception:
                pass
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            page = _maybe_switch_to_new_page(page, before_pages)
            _wait_for_progress(page, old_url)
            continue

        # Fallback: click the most likely primary action in the current step.
        try:
            info = _js_click_primary_action(target_page)
        except Exception:
            info = {"clicked": False}
        if info.get("clicked"):
            try:
                log.info("Submit mode: JS primary click text=%s", str(info.get("text") or ""))
            except Exception:
                pass
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            page = _maybe_switch_to_new_page(page, before_pages)
            _wait_for_progress(page, old_url)
            try:
                page.wait_for_timeout(900)
            except Exception:
                pass
            if _looks_submitted(page):
                return True, page
            continue

        # If we still see an Apply entry point (common when click was blocked), try again.
        before_pages = []
        try:
            before_pages = list(getattr(getattr(page, "context", None), "pages", []) or [])
        except Exception:
            before_pages = []
        try:
            old_url = getattr(page, "url", "") or ""
        except Exception:
            old_url = ""
        if _click_by_text(target_page, ["Apply now", "Start application", "Begin application", "Apply"]):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            page = _maybe_switch_to_new_page(page, before_pages)
            _wait_for_progress(page, old_url)
            continue

        break

    return _looks_submitted(page), page


def _worker_loop(
    worker_id: int,
    limit: int,
    min_score: int,
    headless: bool,
    keep_open: bool,
    submit: bool,
    dry_run: bool,
    target_url: str | None,
    continuous: bool,
) -> tuple[int, int, int]:
    applied = 0
    prepared = 0
    failed = 0

    while True:
        if not continuous and applied + prepared + failed >= limit:
            break

        job = _acquire_job(
            min_score=min_score,
            worker_id=worker_id,
            target_url=target_url,
            allow_prepared=bool(submit),
            lock=not dry_run,
        )
        if not job:
            break

        res = _assist_one_job(
            job,
            worker_id=worker_id,
            headless=headless,
            keep_open=keep_open,
            submit=submit and (not dry_run),
        )
        if res.status == "applied":
            if not dry_run:
                _mark_applied(job["url"], res.note, duration_ms=res.duration_ms)
            applied += 1
        elif res.status == "prepared":
            if not dry_run:
                _mark_prepared(job["url"], res.note, duration_ms=res.duration_ms)
            prepared += 1
        else:
            if not dry_run:
                _mark_failed(job["url"], res.note or "failed", duration_ms=res.duration_ms)
            failed += 1

        if target_url:
            break

    return applied, prepared, failed


def main(
    limit: int = 1,
    target_url: str | None = None,
    min_score: int = 7,
    headless: bool = False,
    dry_run: bool = True,
    continuous: bool = False,
    workers: int = 1,
    keep_open: bool = True,
    submit: bool = False,
) -> None:
    """Entry point for Playwright-based apply.

    - Default behavior is assisted fill (no final submit)
    - Enable --submit to attempt a best-effort click-through submission
    - Use --dry-run to preview without DB writes and without submission
    """
    config.load_env()
    config.ensure_dirs()

    effective_limit = 0 if continuous else max(1, int(limit))

    if workers <= 1:
        applied, prep, fail = _worker_loop(
            worker_id=0,
            limit=effective_limit,
            min_score=min_score,
            headless=headless,
            keep_open=keep_open,
            submit=submit,
            dry_run=dry_run,
            target_url=target_url,
            continuous=continuous,
        )
        print(f"LLM apply done: {applied} applied, {prep} prepared, {fail} failed")
        return

    # Multi-worker: split limit
    limits: list[int]
    if effective_limit:
        base = effective_limit // workers
        extra = effective_limit % workers
        limits = [base + (1 if i < extra else 0) for i in range(workers)]
    else:
        limits = [0] * workers

    results: list[tuple[int, int, int]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="assist-worker") as ex:
        futs = {
            ex.submit(
                _worker_loop,
                worker_id=i,
                limit=limits[i] or 1,
                min_score=min_score,
                headless=headless,
                keep_open=keep_open,
                submit=submit,
                dry_run=dry_run,
                target_url=target_url,
                continuous=continuous,
            ): i
            for i in range(workers)
        }
        for fut in as_completed(futs):
            results.append(fut.result())

    total_applied = sum(r[0] for r in results)
    total_prep = sum(r[1] for r in results)
    total_fail = sum(r[2] for r in results)
    print(f"LLM apply done: {total_applied} applied, {total_prep} prepared, {total_fail} failed")
