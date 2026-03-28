"""ApplyPilot HTML Dashboard Generator.

Generates a self-contained HTML dashboard with:
  - Summary stats (total, enriched, scored, high-fit)
  - Score distribution bar chart
  - Jobs-by-source breakdown
  - Filterable job cards grouped by score
  - Client-side search and score filtering
"""

from __future__ import annotations

import json
import os
import re
import webbrowser
from html import escape
from pathlib import Path

from rich.console import Console

from applypilot.config import APP_DIR, DB_PATH
from applypilot.database import ensure_columns, get_connection

console = Console()


def generate_dashboard(
    output_path: str | None = None,
    *,
    quiet: bool = False,
    db_path: str | Path | None = None,
    app_dir: Path | None = None,
) -> str:
    """Generate an HTML dashboard of all jobs with fit scores.

    Args:
        output_path: Where to write the HTML file. Defaults to ~/.applypilot/dashboard.html.

    Returns:
        Absolute path to the generated HTML file.
    """
    base_dir = app_dir if app_dir is not None else APP_DIR
    try:
        from applypilot.setup_workspace import get_setup_status

        setup = get_setup_status(base_dir)
    except Exception:
        setup = {
            "app_dir": str(base_dir),
            "has_profile": False,
            "has_resume_txt": False,
            "has_resume_pdf": False,
            "has_searches": False,
        }
    out = Path(output_path) if output_path else Path(base_dir) / "dashboard.html"

    conn = get_connection(db_path)
    try:
        ensure_columns(conn)
    except Exception:
        pass

    # Smart-extract source catalog from package config/sites.yaml.
    smart_site_names: list[str] = []
    try:
        from applypilot.config import load_sites_config

        sc = load_sites_config() or {}
        entries = sc.get("sites") if isinstance(sc, dict) else []
        seen: set[str] = set()
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            lk = name.lower()
            if lk in seen:
                continue
            seen.add(lk)
            smart_site_names.append(name)
        smart_site_names = sorted(smart_site_names, key=lambda s: s.lower())
    except Exception:
        smart_site_names = []

    uk_smart_defaults = [
        n
        for n in [
            "GOV.UK Find a job",
            "Reed",
            "NHS Jobs",
            "Adzuna UK",
            "Guardian Jobs",
            "jobs.ac.uk",
            "Jobs Go Public",
            "LG Jobs",
            "HealthJobsUK",
            "MoJ Jobs",
        ]
        if any(n.lower() == s.lower() for s in smart_site_names)
    ]
    smart_catalog_js = json.dumps(smart_site_names, ensure_ascii=True)
    uk_smart_defaults_js = json.dumps(uk_smart_defaults, ensure_ascii=True)

    # Stats
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE COALESCE(apply_status, '') NOT IN ('applied','failed','skipped') AND applied_at IS NULL"
    ).fetchone()[0]
    # "Ready" in the dashboard means we have the docs needed to apply.
    ready = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE tailored_resume_path IS NOT NULL "
        "AND cover_letter_path IS NOT NULL "
        "AND applied_at IS NULL "
        "AND COALESCE(apply_status, '') != 'skipped'"
    ).fetchone()[0]
    scored = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
    high_fit = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score >= 7").fetchone()[0]

    applied = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_status = 'applied' OR applied_at IS NOT NULL"
    ).fetchone()[0]
    prepared = conn.execute("SELECT COUNT(*) FROM jobs WHERE apply_status = 'prepared'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE apply_status = 'failed'").fetchone()[0]
    skipped = conn.execute("SELECT COUNT(*) FROM jobs WHERE apply_status = 'skipped'").fetchone()[0]
    blocked = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_status = 'skipped' AND apply_error = 'user_deleted'"
    ).fetchone()[0]
    skipped_other = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_status = 'skipped' AND COALESCE(apply_error, '') != 'user_deleted'"
    ).fetchone()[0]
    failed_skipped = int(failed or 0) + int(skipped_other or 0)

    # Score distribution
    score_dist: dict[int, int] = {}
    if scored:
        rows = conn.execute(
            "SELECT fit_score, COUNT(*) FROM jobs "
            "WHERE fit_score IS NOT NULL "
            "GROUP BY fit_score ORDER BY fit_score DESC"
        ).fetchall()
        for r in rows:
            score_dist[r[0]] = r[1]

    # Site stats
    site_stats = conn.execute("""
        SELECT site,
               COUNT(*) as total,
               SUM(CASE WHEN fit_score >= 7 THEN 1 ELSE 0 END) as high_fit,
               SUM(CASE WHEN fit_score BETWEEN 5 AND 6 THEN 1 ELSE 0 END) as mid_fit,
               SUM(CASE WHEN fit_score < 5 AND fit_score IS NOT NULL THEN 1 ELSE 0 END) as low_fit,
               SUM(CASE WHEN fit_score IS NULL THEN 1 ELSE 0 END) as unscored,
               ROUND(AVG(fit_score), 1) as avg_score
        FROM jobs GROUP BY site ORDER BY high_fit DESC, total DESC
    """).fetchall()

    role_stats = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(search_query), ''), '(Unassigned)') AS role_name,
               COUNT(*) AS total
          FROM jobs
         WHERE fit_score >= 5
         GROUP BY role_name
         ORDER BY total DESC, role_name ASC
        """
    ).fetchall()

    # All scored jobs (5+), ordered by score desc
    jobs = conn.execute("""
         SELECT rowid AS id,
                url, title, company, search_query, salary, description, location, site, strategy,
                 full_description, application_url, detail_error,
                sponsorship_explicit, sponsorship_evidence,
                sponsor_licensed, sponsor_match_name, sponsor_match_confidence,
                 fit_score, score_reasoning,
                 tailored_resume_path, supporting_statement_path, cover_letter_path,
                 tailor_status, tailor_failure_detail, tailor_report_path, tailor_requirement_gaps, tailor_responsibility_map,
                 cover_letter_status, cover_letter_failure_detail, cover_letter_report_path, cover_letter_diagnostics,
                 applied_at, apply_status, apply_error
          FROM jobs
          WHERE fit_score >= 5
          ORDER BY fit_score DESC, site, title
      """).fetchall()

    # Blocked URL prefixes (for client-side fast checks / avoiding stale cards)
    try:
        blocked_prefixes = [str(r[0]) for r in conn.execute("SELECT prefix FROM blocked_urls").fetchall()]
    except Exception:
        blocked_prefixes = []

    # Color map per site
    colors = {
        "RemoteOK": "#10b981",
        "WelcomeToTheJungle": "#f59e0b",
        "Job Bank Canada": "#3b82f6",
        "CareerJet Canada": "#8b5cf6",
        "Hacker News Jobs": "#ff6600",
        "BuiltIn Remote": "#ec4899",
        "TD Bank": "#00a651",
        "CIBC": "#c41f3e",
        "RBC": "#003168",
        "indeed": "#2164f3",
        "linkedin": "#0a66c2",
        "Dice": "#eb1c26",
        "Glassdoor": "#0caa41",
        "GOV.UK Find a job": "#1d70b8",
        "NHS Jobs": "#005eb8",
        "Reed": "#d1001c",
        "Adzuna UK": "#111827",
        "Guardian Jobs": "#052962",
        "jobs.ac.uk": "#0f766e",
        "Jobs Go Public": "#6d28d9",
        "LG Jobs": "#7c3aed",
    }

    # Score distribution bar chart
    score_bars = ""
    max_count = max(score_dist.values()) if score_dist else 1
    for s in range(10, 0, -1):
        count = score_dist.get(s, 0)
        pct = (count / max_count * 100) if max_count else 0
        score_color = "#10b981" if s >= 7 else ("#f59e0b" if s >= 5 else "#ef4444")
        score_bars += f"""
        <div class="score-row">
          <span class="score-label">{s}</span>
          <div class="score-bar-track">
            <div class="score-bar-fill" style="width:{pct}%;background:{score_color}"></div>
          </div>
          <span class="score-count">{count}</span>
        </div>"""

    # Site stats rows
    site_rows = ""
    for s in site_stats:
        site = s["site"] or "?"
        color = colors.get(site, "#6b7280")
        avg = s["avg_score"] or 0
        site_rows += f"""
        <div class="site-row">
          <div class="site-name" style="color:{color}">{escape(site)}</div>
          <div class="site-nums">{s["total"]} jobs &middot; {s["high_fit"]} strong fit &middot; avg score {avg}</div>
          <div class="bar-track">
            <div class="bar-fill" style="width:{s["high_fit"] / max(s["total"], 1) * 100}%;background:{color}"></div>
            <div class="bar-fill" style="width:{s["mid_fit"] / max(s["total"], 1) * 100}%;background:{color}66"></div>
          </div>
        </div>"""

    # Job cards grouped by score
    job_sections = ""
    current_score = None
    for j in jobs:
        score = j["fit_score"] or 0
        if score != current_score:
            if current_score is not None:
                job_sections += "</div></details>"
            score_color = "#10b981" if score >= 7 else "#f59e0b"
            score_label = {
                10: "Perfect Match",
                9: "Excellent Fit",
                8: "Strong Fit",
                7: "Good Fit",
                6: "Moderate+",
                5: "Moderate",
            }.get(score, f"Score {score}")
            count_at_score = score_dist.get(score, 0)

            # Default collapsed for moderate sections (5-6) to reduce clutter.
            open_attr = " open" if score >= 7 else ""
            job_sections += f"""
            <details class="score-group" data-score-group="{score}"{open_attr}>
              <summary class="score-header" style="border-color:{score_color}">
                <span class="score-badge" style="background:{score_color}">{score}</span>
                {score_label} ({count_at_score} jobs)
              </summary>
              <div class="job-grid">"""
            current_score = score

        jid = str(j["id"])
        title = escape(j["title"] or "Untitled")
        company = escape(j["company"] or "")
        role_query_raw = str(j["search_query"] or "").strip()
        role_query_label = role_query_raw if role_query_raw else "Unassigned"
        url = escape(j["url"] or "")
        salary = escape(j["salary"] or "")
        location = escape(j["location"] or "")
        site = escape(j["site"] or "")
        site_color = colors.get(j["site"] or "", "#6b7280")
        apply_url = escape(j["application_url"] or j["url"] or "")

        sponsor_policy = str(j["sponsorship_explicit"] or "Unknown").strip() or "Unknown"
        sponsor_licensed = str(j["sponsor_licensed"] or "Unknown").strip() or "Unknown"
        sponsor_evidence = str(j["sponsorship_evidence"] or "").strip()
        sponsor_match_name = str(j["sponsor_match_name"] or "").strip()

        status_raw = (j["apply_status"] or "").strip().lower()
        if not status_raw and j["applied_at"]:
            status_raw = "applied"
        if not status_raw:
            # default: scored but not applied
            status_raw = "ready"
        if status_raw == "skipped" and (j["apply_error"] or "").strip().lower() == "user_deleted":
            status_raw = "blocked"

        status_label = {
            "ready": "Ready",
            "selected": "Selected",
            "prepared": "Prepared",
            "in_progress": "In progress",
            "applied": "Applied",
            "failed": "Failed",
            "skipped": "Skipped",
            "blocked": "Blocked",
            "manual": "Manual",
        }.get(status_raw, status_raw[:20] if status_raw else "Ready")

        # Parse keywords and reasoning from score_reasoning
        reasoning_raw = j["score_reasoning"] or ""
        reasoning_lines = reasoning_raw.split("\n")
        keywords = reasoning_lines[0][:120] if reasoning_lines else ""
        reasoning = reasoning_lines[1][:200] if len(reasoning_lines) > 1 else ""

        tailor_status = str(j["tailor_status"] or "").strip().lower()
        tailor_failure_detail = str(j["tailor_failure_detail"] or "").strip()
        tailor_report_path = str(j["tailor_report_path"] or "").strip()
        raw_gap_json = str(j["tailor_requirement_gaps"] or "").strip()
        raw_resp_map_json = str(j["tailor_responsibility_map"] or "").strip()
        cover_status = str(j["cover_letter_status"] or "").strip().lower()
        cover_failure_detail = str(j["cover_letter_failure_detail"] or "").strip()
        cover_report_path = str(j["cover_letter_report_path"] or "").strip()
        raw_cover_diag_json = str(j["cover_letter_diagnostics"] or "").strip()
        gap_summary = ""
        gap_tags: list[str] = []
        responsibility_details = ""
        cover_diag_details = ""
        if raw_gap_json:
            try:
                gap_obj = json.loads(raw_gap_json)
                if isinstance(gap_obj, dict):
                    hard = [str(x) for x in (gap_obj.get("missing_hard_requirements") or []) if str(x).strip()]
                    must = [str(x) for x in (gap_obj.get("missing_must_have_skills") or []) if str(x).strip()]
                    domain = [str(x) for x in (gap_obj.get("missing_domains") or []) if str(x).strip()]
                    if hard:
                        gap_tags.append(f"Hard gaps: {', '.join(hard[:3])}")
                    if must:
                        gap_tags.append(f"Must-have gaps: {', '.join(must[:4])}")
                    if domain:
                        gap_tags.append(f"Domain gaps: {', '.join(domain[:3])}")
                    gap_summary = " | ".join(gap_tags)
            except Exception:
                gap_summary = ""
        if raw_resp_map_json:
            try:
                resp_map = json.loads(raw_resp_map_json)
                if isinstance(resp_map, list):
                    resp_rows: list[str] = []
                    for item in resp_map[:5]:
                        if not isinstance(item, dict):
                            continue
                        resp = str(item.get("responsibility") or "").strip()
                        evidence = item.get("evidence") or []
                        if not resp:
                            continue
                        ev_text = []
                        if isinstance(evidence, list):
                            for ev in evidence[:2]:
                                if not isinstance(ev, dict):
                                    continue
                                fid = str(ev.get("id") or "").strip()
                                ftxt = str(ev.get("text") or "").strip()
                                if ftxt:
                                    ev_text.append(f"{fid}: {ftxt}" if fid else ftxt)
                        if ev_text:
                            responsibility_details += (
                                f"<div style='margin-bottom:0.65rem'><div><strong>Responsibility:</strong> {escape(resp)}</div>"
                                f"<div style='margin-top:0.2rem;color:#5b6474'><strong>Matched evidence:</strong> {escape(' | '.join(ev_text))}</div></div>"
                            )
                        else:
                            responsibility_details += (
                                f"<div style='margin-bottom:0.65rem'><div><strong>Responsibility:</strong> {escape(resp)}</div>"
                                "<div style='margin-top:0.2rem;color:#5b6474'><strong>Matched evidence:</strong> none found</div></div>"
                            )
            except Exception:
                responsibility_details = ""
        if raw_cover_diag_json:
            try:
                diag = json.loads(raw_cover_diag_json)
                if isinstance(diag, dict):
                    pack = str(diag.get("role_pack") or "").strip()
                    signals = [str(x) for x in (diag.get("job_signals") or []) if str(x).strip()]
                    evidence = [str(x) for x in (diag.get("evidence") or []) if str(x).strip()]
                    responsibilities = [str(x) for x in (diag.get("responsibilities") or []) if str(x).strip()]
                    skills = [str(x) for x in (diag.get("relevant_skills") or []) if str(x).strip()]
                    rows: list[str] = []
                    if pack:
                        rows.append(f"<div><strong>Cover pack:</strong> {escape(pack)}</div>")
                    if responsibilities:
                        rows.append(
                            f"<div style='margin-top:0.35rem'><strong>Responsibilities:</strong> {escape(' | '.join(responsibilities[:4]))}</div>"
                        )
                    if evidence:
                        rows.append(
                            f"<div style='margin-top:0.35rem'><strong>Evidence used:</strong> {escape(' | '.join(evidence[:4]))}</div>"
                        )
                    if signals:
                        rows.append(
                            f"<div style='margin-top:0.35rem'><strong>Job signals:</strong> {escape(' | '.join(signals[:4]))}</div>"
                        )
                    if skills:
                        rows.append(
                            f"<div style='margin-top:0.35rem'><strong>Relevant skills:</strong> {escape(', '.join(skills[:6]))}</div>"
                        )
                    cover_diag_details = "".join(rows)
            except Exception:
                cover_diag_details = ""

        full_desc_raw = str(j["full_description"] or "")
        desc_preview = escape(full_desc_raw[:300])
        desc_len = len(full_desc_raw)

        meta_parts = []
        meta_parts.append(
            f'<span class="meta-tag site-tag" style="background:{site_color}33;color:{site_color}">{site}</span>'
        )
        if company:
            meta_parts.append(f'<span class="meta-tag">{company[:48]}</span>')
        meta_parts.append(
            f'<span class="meta-tag status status-{escape(status_raw)}" data-role="status">{escape(status_label)}</span>'
        )
        if sponsor_licensed.lower() == "yes":
            label = escape(sponsor_match_name or "Licensed sponsor")
            meta_parts.append(
                f'<span class="meta-tag" title="Licensed sponsor (Home Office register)">Sponsor: {label[:48]}</span>'
            )
        if sponsor_policy.lower() == "no":
            meta_parts.append(
                f'<span class="meta-tag" title="{escape(sponsor_evidence) if sponsor_evidence else ""}">Sponsorship: No</span>'
            )
        elif sponsor_policy.lower() == "yes":
            meta_parts.append(
                f'<span class="meta-tag" title="{escape(sponsor_evidence) if sponsor_evidence else ""}">Sponsorship: Yes</span>'
            )
        elif sponsor_policy.lower() == "conditional":
            meta_parts.append(
                f'<span class="meta-tag" title="{escape(sponsor_evidence) if sponsor_evidence else ""}">Sponsorship: Conditional</span>'
            )
        if j["tailored_resume_path"]:
            meta_parts.append('<span class="meta-tag artifact">Tailored</span>')
        elif tailor_status and tailor_status.startswith("failed"):
            meta_parts.append('<span class="meta-tag meta-tag-failed">Tailor failed</span>')
        if j["supporting_statement_path"]:
            meta_parts.append('<span class="meta-tag artifact">Statement</span>')
        if j["cover_letter_path"]:
            meta_parts.append('<span class="meta-tag artifact">Cover</span>')
        elif cover_status == "failed":
            meta_parts.append('<span class="meta-tag meta-tag-failed">Cover failed</span>')
        if salary:
            meta_parts.append(f'<span class="meta-tag salary">{salary}</span>')
        if location:
            meta_parts.append(f'<span class="meta-tag location">{location[:40]}</span>')
        if role_query_raw:
            meta_parts.append(f'<span class="meta-tag">{escape(role_query_raw[:36])}</span>')
        meta_html = " ".join(meta_parts)

        # Precompute a compact searchable blob to avoid expensive `textContent` scans
        # over large hidden sections (full description, diagnostics, etc.).
        search_blob = " ".join(
            [
                str(title or ""),
                str(company or ""),
                str(site or ""),
                str(status_label or ""),
                str(salary or ""),
                str(location or ""),
                str(role_query_label or ""),
            ]
        )
        search_blob = re.sub(r"\s+", " ", search_blob).strip().lower()[:420]

        footer_links: list[str] = []
        if url:
            footer_links.append(f'<a href="{url}" class="apply-link" target="_blank">Listing</a>')
        if apply_url:
            footer_links.append(f'<a href="{apply_url}" class="apply-link primary" target="_blank">Apply</a>')

        # Live actions (work in `applypilot dashboard --serve` mode)
        if status_raw == "selected":
            footer_links.append(
                f'<button class="apply-link primary" data-live="1" onclick="selectJob({jid}, false)">Unpick</button>'
            )
        else:
            footer_links.append(
                f'<button class="apply-link primary" data-live="1" onclick="selectJob({jid}, true, true)">Pick</button>'
            )
        footer_links.append(f'<button class="apply-link" data-live="1" onclick="markApplied({jid})">Applied</button>')
        footer_links.append(f'<button class="apply-link" data-live="1" onclick="markFailed({jid})">Failed</button>')
        footer_links.append(f'<button class="apply-link danger" data-live="1" onclick="blockJob({jid})">Block</button>')
        footer_links.append(
            f'<button class="apply-link danger" data-live="1" onclick="deleteJob({jid})">Delete</button>'
        )
        # Copy-to-clipboard helpers for manual marking
        footer_links.append(
            f'<button class="apply-link copy-btn" onclick="copyCmd(\'applypilot apply --mark-applied {jid}\')">Copy mark applied</button>'
        )
        footer_links.append(
            f'<button class="apply-link copy-btn" onclick="copyCmd(\'applypilot apply --mark-failed {jid} --fail-reason manual\')">Copy mark failed</button>'
        )
        apply_html = "".join(footer_links)

        job_sections += f"""
        <div class="job-card" data-id="{jid}" data-score="{score}" data-group="{score}" data-text="{escape(search_blob)}" data-site="{escape(j["site"] or "")}" data-status="{escape(status_raw)}" data-location="{location.lower()}" data-role="{escape(role_query_label.lower())}" data-company="{company}" data-sponsor-policy="{escape(sponsor_policy.lower())}" data-sponsor-licensed="{escape(sponsor_licensed.lower())}">
          <div class="card-header">
            <span class="score-pill" style="background:{"#10b981" if score >= 7 else "#f59e0b"}">{score}</span>
            <span class="meta-tag" title="Stable job ID for manual marking">#{jid}</span>
            <a href="{url}" class="job-title" target="_blank">{title}</a>
          </div>
          <div class="meta-row">{meta_html}</div>
          {f'<div class="keywords-row">{escape(keywords)}</div>' if keywords else ""}
          {f'<div class="reasoning-row">{escape(reasoning)}</div>' if reasoning else ""}
          {f'<div class="reasoning-row diag-block diag-error">Tailor rejection: {escape(tailor_failure_detail[:420])}</div>' if tailor_failure_detail else ""}
          {f'<div class="keywords-row diag-block diag-warn">Requirement gaps: {escape(gap_summary[:420])}</div>' if gap_summary else ""}
          {f'<div class="reasoning-row diag-block diag-error">Cover rejection: {escape(cover_failure_detail[:420])}</div>' if cover_failure_detail else ""}
          <p class="desc-preview">{desc_preview}...</p>
          {f"<details class='full-desc-details lazy' data-job-id='{jid}' data-field='full_description' data-loaded='0'><summary class='expand-btn'>Full Description ({desc_len:,} chars)</summary><div class='full-desc'>Loading...</div></details>" if full_desc_raw else ""}
          {f"<details class='full-desc-details'><summary class='expand-btn'>Responsibility mapping used for CV</summary><div class='full-desc'>{responsibility_details}</div></details>" if responsibility_details else ""}
          {f"<details class='full-desc-details'><summary class='expand-btn'>Cover letter diagnostics</summary><div class='full-desc'>{cover_diag_details}</div></details>" if cover_diag_details else ""}
          {f"<details class='full-desc-details'><summary class='expand-btn'>Tailor report path</summary><div class='full-desc'>{escape(tailor_report_path)}</div></details>" if tailor_report_path and not j["tailored_resume_path"] else ""}
          {f"<details class='full-desc-details'><summary class='expand-btn'>Cover report path</summary><div class='full-desc'>{escape(cover_report_path)}</div></details>" if cover_report_path and not j["cover_letter_path"] else ""}
          <div class="card-footer">{apply_html}</div>
        </div>"""

    if current_score is not None:
        job_sections += "</div></details>"

    html = f"""<!DOCTYPE html>
 <html lang="en">
 <head>
 <meta charset="UTF-8">
 <meta name="viewport" content="width=device-width, initial-scale=1.0">
 <title>ApplyPilot Dashboard</title>
 <meta name="blocked-prefixes" content="{escape("|".join(blocked_prefixes))}">
 <link rel="preconnect" href="https://fonts.googleapis.com">
 <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
 <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500,650,800&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
  :root {{
    --paper: #f6f2e8;
    --paper-2: #fbf7ee;
    --paper-3: #f0e7d6;
    --ink: #0b0f14;
    --muted: #4b5563;
    --muted-2: #6b7280;
    --line: rgba(15, 23, 42, 0.12);
    --card: rgba(255, 255, 255, 0.72);
    --card-2: rgba(255, 255, 255, 0.55);
    --shadow: 0 18px 50px rgba(2, 6, 23, 0.16);
    --surface: rgba(255,255,255,0.62);
    --surface-strong: rgba(255,255,255,0.78);
    --text-soft: rgba(2,6,23,0.78);
    --text-strong: rgba(2,6,23,0.90);
    --grid-1: rgba(2,6,23,0.05);
    --grid-2: rgba(2,6,23,0.03);
    --hint-bg: rgba(29,78,216,0.08);
    --hint-border: rgba(29,78,216,0.22);
    --hint-title: rgba(29,78,216,0.92);
    --hint-code-bg: rgba(255,255,255,0.65);
    --hint-code-text: rgba(2,6,23,0.86);

    --accent: #e85d2a;
    --accent-2: #0f766e;
    --accent-3: #1d4ed8;

    --good: #0f766e;
    --warn: #b45309;
    --bad: #b91c1c;
  }}

  html[data-theme="dark"] {{
    color-scheme: dark;
    --paper: #08111b;
    --paper-2: #0d1723;
    --paper-3: #111d2c;
    --ink: #edf4ff;
    --muted: #b8c6d9;
    --muted-2: #93a4ba;
    --line: rgba(148, 163, 184, 0.22);
    --card: rgba(10, 18, 30, 0.80);
    --card-2: rgba(15, 23, 42, 0.68);
    --shadow: 0 18px 50px rgba(0, 0, 0, 0.42);
    --surface: rgba(15,23,42,0.72);
    --surface-strong: rgba(30,41,59,0.90);
    --text-soft: rgba(226,232,240,0.82);
    --text-strong: rgba(241,245,249,0.96);
    --grid-1: rgba(148,163,184,0.06);
    --grid-2: rgba(148,163,184,0.04);
    --hint-bg: rgba(30,64,175,0.16);
    --hint-border: rgba(96,165,250,0.26);
    --hint-title: rgba(191,219,254,0.98);
    --hint-code-bg: rgba(15,23,42,0.76);
    --hint-code-text: rgba(226,232,240,0.92);
    --setup-nav-bg: rgba(15, 23, 42, 0.78);
    --setup-nav-btn-bg: rgba(30, 41, 59, 0.88);
    --setup-nav-btn-hover: rgba(51, 65, 85, 0.96);
    --setup-panel-bg: rgba(9, 15, 25, 0.84);
    --setup-card-bg: rgba(17, 24, 39, 0.88);
    --setup-soft-border: rgba(148, 163, 184, 0.20);
    --setup-title: rgba(248, 250, 252, 0.98);
    --setup-desc: rgba(203, 213, 225, 0.82);
    --setup-pill-bg: rgba(30, 41, 59, 0.92);
    --setup-pill-text: rgba(241, 245, 249, 0.88);
    --panel-bg: rgba(10, 18, 30, 0.82);
    --panel-bg-2: rgba(15, 23, 42, 0.78);
    --panel-title: rgba(241, 245, 249, 0.96);
    --panel-text: rgba(226, 232, 240, 0.80);
    --panel-soft: rgba(148, 163, 184, 0.14);
    --meta-pill-bg: rgba(30, 41, 59, 0.90);
    --meta-pill-text: rgba(226, 232, 240, 0.82);
    --job-card-bg: rgba(8, 15, 26, 0.86);
    --job-card-border: rgba(148, 163, 184, 0.18);
    --job-title: rgba(248, 250, 252, 0.98);
    --reasoning-text: rgba(191, 219, 254, 0.82);
    --desc-text: rgba(226, 232, 240, 0.78);
    --button-bg: rgba(30, 41, 59, 0.88);
    --button-hover: rgba(51, 65, 85, 0.96);
    --diag-error-bg: rgba(127, 29, 29, 0.34);
    --diag-error-border: rgba(248, 113, 113, 0.28);
    --diag-error-text: rgba(254, 202, 202, 0.96);
    --diag-warn-bg: rgba(120, 53, 15, 0.34);
    --diag-warn-border: rgba(251, 191, 36, 0.26);
    --diag-warn-text: rgba(254, 240, 138, 0.96);
    --status-failed-bg: rgba(127, 29, 29, 0.28);
    --status-failed-border: rgba(248, 113, 113, 0.26);
    --status-failed-text: rgba(254, 202, 202, 0.96);

    --tag-salary-bg: rgba(16,185,129,0.20);
    --tag-salary-border: rgba(16,185,129,0.34);
    --tag-salary-text: rgba(167,243,208,0.96);
    --tag-location-bg: rgba(59,130,246,0.22);
    --tag-location-border: rgba(96,165,250,0.36);
    --tag-location-text: rgba(191,219,254,0.96);
    --tag-artifact-bg: rgba(232,93,42,0.20);
    --tag-artifact-border: rgba(251,146,60,0.34);
    --tag-artifact-text: rgba(254,215,170,0.96);
    --tag-status-bg: rgba(148,163,184,0.10);
    --tag-status-text: rgba(226,232,240,0.86);
  }}

  html:not([data-theme="dark"]) {{
    --panel-bg: var(--card);
    --panel-bg-2: var(--card-2);
    --panel-title: rgba(2,6,23,0.86);
    --panel-text: rgba(2,6,23,0.62);
    --panel-soft: rgba(2,6,23,0.06);
    --meta-pill-bg: rgba(255,255,255,0.55);
    --meta-pill-text: rgba(2,6,23,0.70);
    --job-card-bg: var(--card);
    --job-card-border: rgba(2,6,23,0.10);
    --job-title: rgba(2,6,23,0.92);
    --reasoning-text: rgba(2,6,23,0.66);
    --desc-text: rgba(2,6,23,0.68);
    --button-bg: rgba(255,255,255,0.55);
    --button-hover: rgba(255,255,255,0.78);
    --diag-error-bg: rgba(254,242,242,0.90);
    --diag-error-border: rgba(239,68,68,0.22);
    --diag-error-text: #991b1b;
    --diag-warn-bg: rgba(255,247,237,0.94);
    --diag-warn-border: rgba(217,119,6,0.18);
    --diag-warn-text: rgba(146,64,14,0.96);
    --status-failed-bg: rgba(239,68,68,0.12);
    --status-failed-border: rgba(239,68,68,0.22);
    --status-failed-text: #b91c1c;

    --tag-salary-bg: rgba(15,118,110,0.10);
    --tag-salary-border: rgba(15,118,110,0.20);
    --tag-salary-text: rgba(2,6,23,0.82);
    --tag-location-bg: rgba(29,78,216,0.08);
    --tag-location-border: rgba(29,78,216,0.18);
    --tag-location-text: rgba(2,6,23,0.82);
    --tag-artifact-bg: rgba(232,93,42,0.10);
    --tag-artifact-border: rgba(232,93,42,0.20);
    --tag-artifact-text: rgba(2,6,23,0.82);
    --tag-status-bg: rgba(2,6,23,0.06);
    --tag-status-text: rgba(2,6,23,0.78);
  }}

  html:not([data-theme="dark"]) {{
    --setup-nav-bg: rgba(255,255,255,0.55);
    --setup-nav-btn-bg: rgba(255,255,255,0.62);
    --setup-nav-btn-hover: rgba(255,255,255,0.82);
    --setup-panel-bg: var(--card);
    --setup-card-bg: rgba(255,255,255,0.52);
    --setup-soft-border: rgba(2,6,23,0.10);
    --setup-title: rgba(2,6,23,0.90);
    --setup-desc: rgba(2,6,23,0.62);
    --setup-pill-bg: rgba(255,255,255,0.60);
    --setup-pill-text: rgba(2,6,23,0.78);
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html {{ color-scheme: light; }}
  body {{
    font-family: 'IBM Plex Sans', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
    background: var(--paper);
    color: var(--ink);
    min-height: 100vh;
  }}

  body::before {{
    content: "";
    position: fixed;
    inset: 0;
    z-index: -2;
    background:
      radial-gradient(1100px 700px at 18% 8%, rgba(29, 78, 216, 0.16), transparent 60%),
      radial-gradient(900px 650px at 88% 14%, rgba(232, 93, 42, 0.13), transparent 58%),
      radial-gradient(900px 700px at 46% 88%, rgba(15, 118, 110, 0.14), transparent 56%),
      linear-gradient(180deg, var(--paper-2), var(--paper) 56%, var(--paper-3));
  }}
  body::after {{
    content: "";
    position: fixed;
    inset: 0;
    z-index: -1;
    pointer-events: none;
    opacity: 0.22;
    background-image:
      repeating-linear-gradient(0deg, var(--grid-1) 0px, var(--grid-1) 1px, transparent 1px, transparent 14px),
      repeating-linear-gradient(90deg, var(--grid-2) 0px, var(--grid-2) 1px, transparent 1px, transparent 18px);
    mix-blend-mode: multiply;
  }}

  a {{ color: inherit; }}
  a:hover {{ color: var(--accent-3); }}
  code {{ font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }}

  .wrap {{ max-width: 1260px; margin: 0 auto; padding: 28px 18px 64px; }}

  .page-head {{
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 1rem;
    padding: 18px 18px 14px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 16px;
    box-shadow: 0 10px 30px rgba(2,6,23,0.08);
    backdrop-filter: blur(10px);
  }}
  h1 {{
    font-family: 'Fraunces', serif;
    font-size: 1.9rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    line-height: 1.05;
  }}
  .subtitle {{ color: var(--muted); margin-top: 0.35rem; font-size: 0.95rem; }}
  .head-meta {{ display: flex; gap: 0.5rem; flex-wrap: wrap; justify-content: flex-end; }}
  .chip {{
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.22rem 0.55rem;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--text-soft);
  }}
  .chip strong {{ font-family: 'IBM Plex Mono', ui-monospace, monospace; font-weight: 600; }}
  .chip.good {{ border-color: rgba(15,118,110,0.25); background: rgba(15,118,110,0.10); }}
  .chip.warn {{ border-color: rgba(180,83,9,0.25); background: rgba(180,83,9,0.10); }}

  /* Summary cards */
  .summary {{ display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 0.75rem; margin: 1rem 0 1rem; }}
  .stat-card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 0.95rem 1rem;
    box-shadow: 0 10px 26px rgba(2,6,23,0.06);
    backdrop-filter: blur(10px);
  }}
  .stat-num {{ font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 1.55rem; font-weight: 600; letter-spacing: -0.02em; }}
  .stat-label {{ color: var(--muted); font-size: 0.78rem; margin-top: 0.15rem; }}
  .stat-ok .stat-num {{ color: var(--accent-2); }}
  .stat-scored .stat-num {{ color: var(--accent-3); }}
  .stat-high .stat-num {{ color: var(--accent); }}
  .stat-total .stat-num {{ color: var(--ink); }}
  .stat-applied .stat-num {{ color: var(--good); }}
  .stat-failed .stat-num {{ color: var(--bad); }}
  .stat-blocked .stat-num {{ color: var(--warn); }}

  /* Filters */
  .filters {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 0.95rem 1rem;
    margin: 0 0 1rem;
    display: flex;
    gap: 0.6rem 0.75rem;
    flex-wrap: wrap;
    align-items: center;
    box-shadow: 0 10px 26px rgba(2,6,23,0.06);
    backdrop-filter: blur(10px);
  }}
  .filter-label {{ color: var(--text-soft); font-size: 0.78rem; font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase; }}
  .filter-btn {{
    background: var(--surface);
    border: 1px solid var(--line);
    color: var(--text-soft);
    padding: 0.42rem 0.75rem;
    border-radius: 999px;
    cursor: pointer;
    font-size: 0.8rem;
    transition: transform 0.12s, background 0.12s, border-color 0.12s;
  }}
  .filter-btn:hover {{ transform: translateY(-1px); border-color: rgba(2,6,23,0.20); background: var(--surface-strong); }}
  .filter-btn.active {{ background: rgba(29,78,216,0.14); border-color: rgba(29,78,216,0.25); color: var(--text-strong); }}
  .filter-btn:disabled {{ opacity: 0.45; cursor: not-allowed; transform: none; }}
  .search-input, .select-input {{
    background: var(--surface);
    border: 1px solid var(--line);
    color: var(--text-strong);
    padding: 0.42rem 0.65rem;
    border-radius: 12px;
    font-size: 0.82rem;
  }}
  .search-input {{ width: 220px; }}
  .search-input::placeholder {{ color: var(--muted); }}
  .search-input:focus, .select-input:focus {{ outline: none; border-color: rgba(29,78,216,0.35); box-shadow: 0 0 0 3px rgba(29,78,216,0.10); }}
  .search-input.input-error, .select-input.input-error, .full-desc.input-error {{
    border-color: rgba(185, 28, 28, 0.55) !important;
    box-shadow: 0 0 0 3px rgba(185, 28, 28, 0.14) !important;
    background: rgba(185, 28, 28, 0.06) !important;
  }}
  .field-error-msg {{
    color: rgba(185, 28, 28, 0.95);
    font-size: 0.76rem;
    font-weight: 700;
    margin-top: 0.28rem;
    line-height: 1.3;
  }}

  .toggle {{ display: inline-flex; align-items: center; gap: 0.45rem; color: var(--text-soft); font-size: 0.82rem; user-select: none; }}
  .toggle input {{ accent-color: var(--accent-3); }}

  .theme-toggle {{ min-width: 124px; text-align: center; }}

  /* Live-mode hint (file:// dashboards can't write to SQLite) */
  .live-hint {{
    background: var(--hint-bg);
    border: 1px solid var(--hint-border);
    border-radius: 16px;
    padding: 0.9rem 1rem;
    margin: 1rem 0;
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: center;
    flex-wrap: wrap;
  }}
  .live-hint-title {{ color: var(--hint-title); font-weight: 800; letter-spacing: 0.01em; margin-bottom: 0.25rem; }}
  .live-hint-body {{ color: var(--text-soft); font-size: 0.92rem; line-height: 1.4; }}
  .live-hint code {{ background: var(--hint-code-bg); border: 1px solid var(--line); padding: 0.08rem 0.35rem; border-radius: 10px; color: var(--hint-code-text); }}
  .live-hint-actions {{ display: flex; gap: 0.5rem; align-items: center; }}

  /* Score distribution */
  .score-section {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0 1.25rem; }}
  .score-dist, .sites-section {{ background: var(--panel-bg); border: 1px solid var(--line); border-radius: 16px; padding: 1rem; box-shadow: var(--shadow); backdrop-filter: blur(10px); }}
  .score-dist h3, .sites-section h3 {{ font-family: 'Fraunces', serif; font-size: 1.08rem; margin-bottom: 0.75rem; color: var(--panel-title); }}

  .panel {{ border: none; }}
  .panel > summary {{
    font-family: 'Fraunces', serif;
    font-size: 1.08rem;
    font-weight: 800;
    color: var(--panel-title);
    list-style: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 0.6rem;
    user-select: none;
  }}
  .panel > summary::-webkit-details-marker {{ display: none; }}
  .panel > summary::marker {{ content: ""; }}
  .panel > summary::after {{ content: "+"; margin-left: auto; color: var(--panel-text); font-weight: 900; }}
  .panel[open] > summary::after {{ content: "-"; }}
  .panel-body {{
    margin-top: 0.75rem;
    max-height: 380px;
    overflow: auto;
    padding-right: 6px;
    scrollbar-gutter: stable;
    overscroll-behavior: contain;
  }}

  /* Subtle scrollbars */
  .panel-body::-webkit-scrollbar {{ width: 10px; }}
  .panel-body::-webkit-scrollbar-track {{ background: var(--panel-soft); border-radius: 999px; }}
  .panel-body::-webkit-scrollbar-thumb {{ background: rgba(148,163,184,0.26); border-radius: 999px; border: 2px solid var(--panel-soft); }}
  .panel-body::-webkit-scrollbar-thumb:hover {{ background: rgba(148,163,184,0.38); }}
  .score-row {{ display: flex; align-items: center; gap: 0.55rem; margin-bottom: 0.45rem; }}
  .score-label {{ width: 1.6rem; text-align: right; font-size: 0.85rem; font-weight: 800; color: var(--panel-text); }}
  .score-bar-track {{ flex: 1; height: 12px; background: var(--panel-soft); border-radius: 999px; overflow: hidden; border: 1px solid var(--panel-soft); }}
  .score-bar-fill {{ height: 100%; border-radius: 999px; transition: width 0.3s; }}
  .score-count {{ width: 2.7rem; font-size: 0.8rem; color: var(--panel-text); font-family: 'IBM Plex Mono', ui-monospace, monospace; }}

  /* Site bars */
  .site-row {{ margin-bottom: 0.85rem; }}
  .site-name {{ font-weight: 800; font-size: 0.92rem; letter-spacing: -0.01em; }}
  .site-nums {{ color: var(--panel-text); font-size: 0.78rem; margin: 0.15rem 0 0.35rem; }}
  .bar-track {{ height: 10px; background: var(--panel-soft); border-radius: 999px; display: flex; overflow: hidden; border: 1px solid var(--panel-soft); }}
  .bar-fill {{ height: 100%; transition: width 0.3s; }}

  /* Score group headers */
  .score-group {{ margin: 1.25rem 0 0; }}
  .score-group {{ content-visibility: auto; contain-intrinsic-size: 900px; }}
  .score-header {{
    font-family: 'Fraunces', serif;
    font-size: 1.18rem;
    font-weight: 800;
    margin: 0 0 0.85rem;
    padding: 0.55rem 0.65rem 0.55rem;
    border: 1px solid var(--job-card-border);
    border-left: 6px solid;
    border-radius: 14px;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    cursor: pointer;
    background: var(--panel-bg-2);
    box-shadow: var(--shadow);
  }}
  .score-header::-webkit-details-marker {{ display: none; }}
  .score-header::marker {{ content: ""; }}
  .score-header::after {{ content: "+"; margin-left: auto; color: var(--panel-text); font-weight: 900; }}
  .score-group[open] .score-header::after {{ content: "-"; }}
  .score-badge {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border-radius: 12px;
    color: var(--panel-title);
    font-weight: 900;
    font-size: 1rem;
    border: 1px solid var(--job-card-border);
  }}

  /* Job grid */
  .job-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 0.9rem; }}

  .job-card {{
    background: var(--job-card-bg);
    border-radius: 16px;
    padding: 0.95rem 0.95rem 0.85rem;
    border: 1px solid var(--job-card-border);
    border-left: 6px solid rgba(2,6,23,0.15);
    transition: transform 0.14s, box-shadow 0.14s, border-color 0.14s;
    box-shadow: var(--shadow);
    backdrop-filter: blur(10px);
    content-visibility: auto;
    contain: layout paint style;
    contain-intrinsic-size: 520px;
  }}
  .job-card:hover {{ transform: translateY(-2px); box-shadow: var(--shadow); border-color: rgba(2,6,23,0.16); }}
  .job-card[data-score="9"], .job-card[data-score="10"] {{ border-left-color: rgba(15,118,110,0.92); }}
  .job-card[data-score="8"] {{ border-left-color: rgba(15,118,110,0.70); }}
  .job-card[data-score="7"] {{ border-left-color: rgba(29,78,216,0.82); }}
  .job-card[data-score="6"] {{ border-left-color: rgba(180,83,9,0.78); }}
  .job-card[data-score="5"] {{ border-left-color: rgba(180,83,9,0.45); }}

  .card-header {{ display: flex; align-items: center; gap: 0.55rem; margin-bottom: 0.55rem; }}
  .score-pill {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.7rem;
    height: 1.7rem;
    border-radius: 10px;
    color: var(--panel-title);
    font-weight: 900;
    font-size: 0.82rem;
    flex-shrink: 0;
    border: 1px solid rgba(2,6,23,0.10);
  }}

  .job-title {{ color: var(--job-title); text-decoration: none; font-weight: 800; font-size: 0.98rem; letter-spacing: -0.01em; line-height: 1.15; }}
  .job-title:hover {{ color: var(--accent-3); }}

  .meta-row {{ display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.45rem; }}
  .meta-tag {{
    font-size: 0.72rem;
    padding: 0.16rem 0.5rem;
    border-radius: 999px;
    border: 1px solid var(--job-card-border);
    background: var(--meta-pill-bg);
    color: var(--meta-pill-text);
    font-weight: 600;
  }}
  .meta-tag.salary {{ background: var(--tag-salary-bg); color: var(--tag-salary-text); border-color: var(--tag-salary-border); }}
  .meta-tag.location {{ background: var(--tag-location-bg); color: var(--tag-location-text); border-color: var(--tag-location-border); }}
  .meta-tag.artifact {{ background: var(--tag-artifact-bg); color: var(--tag-artifact-text); border-color: var(--tag-artifact-border); }}
  .meta-tag.status {{ background: var(--tag-status-bg); color: var(--tag-status-text); }}
  .meta-tag.status-ready {{ background: rgba(29,78,216,0.10); border-color: rgba(29,78,216,0.22); }}
  .meta-tag.status-selected {{ background: rgba(180,83,9,0.12); border-color: rgba(180,83,9,0.28); }}
  .meta-tag.status-prepared {{ background: rgba(180,83,9,0.10); border-color: rgba(180,83,9,0.22); }}
  .meta-tag.status-in_progress {{ background: rgba(29,78,216,0.08); border-color: rgba(29,78,216,0.18); }}
  .meta-tag.status-applied {{ background: rgba(15,118,110,0.10); border-color: rgba(15,118,110,0.22); }}
  .meta-tag.status-failed {{ background: rgba(185,28,28,0.10); border-color: rgba(185,28,28,0.22); }}
  .meta-tag.status-skipped {{ background: rgba(75,85,99,0.10); border-color: rgba(75,85,99,0.18); }}
  .meta-tag.status-blocked {{ background: rgba(180,83,9,0.10); border-color: rgba(180,83,9,0.22); }}
  .meta-tag-failed {{ background: var(--status-failed-bg); color: var(--status-failed-text); border-color: var(--status-failed-border); }}

  .keywords-row {{ font-size: 0.76rem; color: rgba(52,211,153,0.96); margin-bottom: 0.25rem; line-height: 1.35; font-weight: 600; }}
  .reasoning-row {{ font-size: 0.78rem; color: var(--reasoning-text); margin-bottom: 0.55rem; font-style: italic; line-height: 1.35; }}
  .diag-block {{
    border: 1px solid transparent;
    border-radius: 12px;
    padding: 0.55rem 0.65rem;
  }}
  .diag-error {{
    background: var(--diag-error-bg);
    border-color: var(--diag-error-border);
    color: var(--diag-error-text);
  }}
  .diag-warn {{
    background: var(--diag-warn-bg);
    border-color: var(--diag-warn-border);
    color: var(--diag-warn-text);
  }}

  .desc-preview {{ font-size: 0.84rem; color: var(--desc-text); line-height: 1.45; margin-bottom: 0.7rem; max-height: 3.8em; overflow: hidden; }}

  .card-footer {{ display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 0.4rem; }}
  .apply-link {{
    font-size: 0.82rem;
    color: var(--text-strong);
    text-decoration: none;
    padding: 0.32rem 0.78rem;
    border: 1px solid var(--job-card-border);
    border-radius: 999px;
    font-weight: 700;
    background: var(--button-bg);
    cursor: pointer;
  }}
  .apply-link:hover {{ background: var(--button-hover); transform: translateY(-1px); }}
  .apply-link.primary {{ background: rgba(29,78,216,0.14); border-color: rgba(29,78,216,0.25); }}
  .apply-link.primary:hover {{ background: rgba(29,78,216,0.20); }}
  .copy-btn {{ background: var(--button-bg); }}
  .copy-btn:active {{ transform: translateY(0); }}
  .apply-link.danger {{ background: rgba(185,28,28,0.08); border-color: rgba(185,28,28,0.22); }}
  .apply-link.danger:hover {{ background: rgba(185,28,28,0.12); }}

  .toast {{
    position: fixed;
    bottom: 18px;
    left: 18px;
    background: rgba(2,6,23,0.92);
    color: rgba(255,255,255,0.92);
    border: 1px solid rgba(255,255,255,0.14);
    padding: 0.65rem 0.85rem;
    border-radius: 14px;
    opacity: 0;
    transform: translateY(8px);
    transition: opacity 0.2s, transform 0.2s;
    z-index: 50;
    max-width: 360px;
    box-shadow: 0 18px 45px rgba(2,6,23,0.32);
  }}
  .toast[data-kind="success"] {{
    background: rgba(15, 118, 110, 0.92);
    border-color: rgba(255,255,255,0.16);
  }}
  .toast[data-kind="error"] {{
    background: rgba(185, 28, 28, 0.92);
    border-color: rgba(255,255,255,0.16);
  }}
  .toast[data-kind="warn"] {{
    background: rgba(180, 83, 9, 0.92);
    border-color: rgba(255,255,255,0.16);
  }}
    .toast.show {{ opacity: 1; transform: translateY(0); }}

  .meta {{
    color: var(--panel-text);
    font-size: 0.78rem;
    line-height: 1.25;
  }}

  /* Expandable full description */
  .full-desc-details {{ margin-bottom: 0.75rem; }}
  .expand-btn {{ font-size: 0.82rem; color: rgba(29,78,216,0.92); cursor: pointer; list-style: none; padding: 0.25rem 0; font-weight: 700; }}
  .expand-btn::-webkit-details-marker {{ display: none; }}
  .expand-btn:hover {{ color: rgba(232,93,42,0.95); }}
      .full-desc {{
        font-size: 0.82rem;
        color: rgba(226, 232, 240, 0.92);
        line-height: 1.55;
        margin-top: 0.55rem;
        padding: 0.75rem;
        background: rgba(2, 6, 23, 0.92);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 14px;
        max-height: 420px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-word;
      }}

      /* Setup workbench */
      .setup-body {{
        max-height: none;
        overflow: visible;
      }}
      .setup-shell {{
        display: grid;
        grid-template-columns: 230px minmax(0, 1fr);
        gap: 0.95rem;
        align-items: start;
      }}
      .setup-nav {{
        position: sticky;
        top: 14px;
        align-self: start;
        background: var(--setup-nav-bg);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 0.75rem;
        box-shadow: var(--shadow);
        backdrop-filter: blur(10px);
      }}
      .setup-nav h3 {{
        font-family: 'Fraunces', serif;
        font-size: 1.02rem;
        font-weight: 800;
        margin: 0 0 0.55rem 0;
        color: var(--setup-title);
      }}
      .setup-nav .nav-help {{
        color: var(--setup-desc);
        font-size: 0.82rem;
        line-height: 1.35;
        margin: 0 0 0.55rem 0;
      }}
      .setup-nav button {{
        width: 100%;
        text-align: left;
        border-radius: 12px;
        padding: 0.45rem 0.6rem;
        border: 1px solid var(--setup-soft-border);
        background: var(--setup-nav-btn-bg);
        color: var(--text-strong);
        cursor: pointer;
        font-size: 0.84rem;
        font-weight: 700;
        margin: 0 0 0.4rem 0;
        transition: transform 0.12s, background 0.12s, border-color 0.12s;
      }}
      .setup-nav button:hover {{
        transform: translateY(-1px);
        border-color: rgba(2,6,23,0.20);
        background: var(--setup-nav-btn-hover);
      }}
      .setup-nav button.active {{
        background: rgba(29,78,216,0.12);
        border-color: rgba(29,78,216,0.25);
        box-shadow: 0 0 0 3px rgba(29,78,216,0.08);
      }}
      .setup-main {{
        display: flex;
        flex-direction: column;
        gap: 0.85rem;
        min-width: 0;
      }}
      .setup-section {{
        background: var(--setup-panel-bg);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 0.95rem;
        box-shadow: var(--shadow);
        backdrop-filter: blur(10px);
      }}
      .setup-section .section-head {{
        display: flex;
        gap: 0.6rem;
        align-items: baseline;
        flex-wrap: wrap;
        margin-bottom: 0.55rem;
      }}
      .setup-section .section-title {{
        font-family: 'Fraunces', serif;
        font-size: 1.12rem;
        font-weight: 800;
        color: var(--setup-title);
      }}
      .setup-section .section-desc {{
        color: var(--setup-desc);
        font-size: 0.86rem;
        line-height: 1.35;
      }}
      .setup-section .fields-2 {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.5rem;
      }}
      .setup-section .fields-3 {{
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 0.5rem;
      }}
      .setup-actions {{
        margin-top: 0.65rem;
        display: flex;
        gap: 0.45rem;
        flex-wrap: wrap;
        align-items: center;
      }}

      /* Setup textarea/input overrides for readability */
      .setup-body .full-desc {{
        background: var(--surface);
        color: var(--text-strong);
        border-color: var(--setup-soft-border);
        box-shadow: 0 8px 18px rgba(2,6,23,0.06);
      }}
      .setup-body .full-desc::placeholder {{ color: var(--muted); }}
      .setup-body .search-input, .setup-body .select-input {{
        width: 100%;
      }}
      .setup-body .panel-body {{
        max-height: none;
        overflow: visible;
        padding-right: 0;
      }}

      .setup-card {{
        background: var(--setup-card-bg);
        border: 1px solid var(--setup-soft-border);
        border-radius: 16px;
        padding: 0.85rem;
      }}

      .variants-head {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 0.6rem;
        flex-wrap: wrap;
        margin-bottom: 0.45rem;
      }}
      .variants-tabs {{
        display: inline-flex;
        gap: 0.35rem;
        flex-wrap: wrap;
      }}
      .tab-btn {{
        border: 1px solid var(--setup-soft-border);
        background: var(--setup-nav-btn-bg);
        color: var(--text-strong);
        border-radius: 999px;
        padding: 0.32rem 0.6rem;
        font-weight: 800;
        font-size: 0.78rem;
        cursor: pointer;
      }}
      .tab-btn.active {{
        background: rgba(15,118,110,0.10);
        border-color: rgba(15,118,110,0.22);
      }}
      .tab-btn.has::after {{
        content: '';
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 999px;
        margin-left: 0.45rem;
        background: rgba(15,118,110,0.70);
        box-shadow: 0 0 0 2px rgba(15,118,110,0.10);
        vertical-align: middle;
      }}

      .setup-statusbar {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 0.55rem;
        flex-wrap: wrap;
        padding: 0.7rem 0.75rem;
        background: var(--setup-card-bg);
        border: 1px solid var(--setup-soft-border);
        border-radius: 16px;
      }}
      .status-group {{
        display: inline-flex;
        gap: 0.4rem;
        align-items: center;
        flex-wrap: wrap;
      }}
      .status-pill {{
        font-size: 0.74rem;
        font-weight: 800;
        padding: 0.16rem 0.5rem;
        border-radius: 999px;
        border: 1px solid var(--setup-soft-border);
        background: var(--setup-pill-bg);
        color: var(--setup-pill-text);
      }}
      .status-pill.ok {{
        border-color: rgba(15,118,110,0.22);
        background: rgba(15,118,110,0.10);
      }}
      .status-pill.warn {{
        border-color: rgba(180,83,9,0.22);
        background: rgba(180,83,9,0.10);
      }}
      .status-pill.bad {{
        border-color: rgba(185,28,28,0.22);
        background: rgba(185,28,28,0.08);
      }}

      /* Statement Studio */
      .studio-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.75rem;
        align-items: start;
      }}
      .studio-pane {{
        background: var(--setup-card-bg);
        border: 1px solid var(--setup-soft-border);
        border-radius: 16px;
        padding: 0.85rem;
      }}
      .studio-pane .meta {{ margin: 0 0 0.35rem 0; }}
      .studio-output {{
        min-height: 320px;
        max-height: 520px;
      }}

      @media (max-width: 980px) {{
        .setup-shell {{ grid-template-columns: 1fr; }}
        .setup-nav {{ position: relative; top: 0; }}
        .studio-grid {{ grid-template-columns: 1fr; }}
      }}

  .hidden {{ display: none !important; }}
  .job-count {{ color: rgba(2,6,23,0.70); font-size: 0.9rem; margin: 0.35rem 0 0.85rem; font-weight: 700; }}

  button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible {{
    outline: 3px solid rgba(232,93,42,0.30);
    outline-offset: 2px;
    border-radius: 12px;
  }}

  /* Motion */
  @keyframes rise {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  .motion-1 {{ animation: rise 500ms cubic-bezier(.2,.8,.2,1) both; }}
  .motion-2 {{ animation: rise 650ms cubic-bezier(.2,.8,.2,1) both; animation-delay: 80ms; }}
  .motion-3 {{ animation: rise 650ms cubic-bezier(.2,.8,.2,1) both; animation-delay: 140ms; }}
  @media (prefers-reduced-motion: reduce) {{
    .motion-1, .motion-2, .motion-3 {{ animation: none !important; }}
    .job-card:hover, .filter-btn:hover, .apply-link:hover {{ transform: none; }}
  }}

  @media (max-width: 980px) {{
    .summary {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .score-section {{ grid-template-columns: 1fr; }}
    .panel-body {{ max-height: 320px; }}
  }}
  @media (max-width: 768px) {{
    .wrap {{ padding: 18px 12px 52px; }}
    .page-head {{ flex-direction: column; align-items: flex-start; }}
    .head-meta {{ justify-content: flex-start; }}
    .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .job-grid {{ grid-template-columns: 1fr; }}
    .setup-grid {{ grid-template-columns: 1fr !important; }}
    .search-input {{ width: 100%; }}
  }}
 </style>
 </head>
<body>

 <div class="wrap">

  <header class="page-head motion-1">
   <div>
     <h1>ApplyPilot Dashboard</h1>
     <p class="subtitle">{active} active jobs &middot; {scored} scored &middot; {high_fit} strong matches (7+)</p>
   </div>
   <div class="head-meta" aria-label="Key metrics">
      <button type="button" class="filter-btn theme-toggle" id="theme-toggle" onclick="toggleTheme()">Dark mode</button>
      <span class="chip"><strong>{active}</strong> active</span>
      <span class="chip"><strong>{ready}</strong> ready</span>
      <span class="chip"><strong>{scored}</strong> scored</span>
      <span class="chip good"><strong>{high_fit}</strong> 7+</span>
      <span class="chip warn"><strong>{blocked}</strong> blocked</span>
   </div>
 </header>

<div id="live-hint" class="live-hint hidden">
  <div>
    <div class="live-hint-title">Live mode disabled</div>
    <div class="live-hint-body">
      This dashboard is opened as a local file, so buttons cannot update the SQLite DB.
      Run <code>applypilot dashboard-serve</code> and use the dashboard at <code>http://127.0.0.1:8765/</code>.
    </div>
  </div>
  <div class="live-hint-actions">
    <button class="apply-link copy-btn" onclick="copyCmd('applypilot dashboard-serve')">Copy command</button>
  </div>
</div>

 <div class="summary motion-2">
   <div class="stat-card stat-total"><div class="stat-num">{active}</div><div class="stat-label">Active Jobs</div></div>
   <div class="stat-card stat-ok"><div class="stat-num">{
        ready
    }</div><div class="stat-label">Ready (tailor + cover)</div></div>
  <div class="stat-card stat-scored"><div class="stat-num">{
        scored
    }</div><div class="stat-label">Scored by LLM</div></div>
  <div class="stat-card stat-high"><div class="stat-num">{
        high_fit
    }</div><div class="stat-label">Strong Fit (7+)</div></div>
  <div class="stat-card stat-applied"><div class="stat-num" id="stat-applied">{
        applied
    }</div><div class="stat-label">Applied</div></div>
  <div class="stat-card stat-failed"><div class="stat-num" id="stat-failed">{
        failed_skipped
    }</div><div class="stat-label">Failed / Skipped</div></div>
  <div class="stat-card stat-blocked"><div class="stat-num" id="stat-blocked">{
        blocked
    }</div><div class="stat-label">Blocked</div></div>
</div>

 <div class="filters motion-2">
  <span class="filter-label">Score:</span>
   <button type="button" class="filter-btn" id="btn-score-all" onclick="filterScore(0, this)" disabled>All 5+</button>
   <button type="button" class="filter-btn active" onclick="filterScore(7, this)">7+ Strong</button>
  <button type="button" class="filter-btn" onclick="filterScore(8, this)">8+ Excellent</button>
  <button type="button" class="filter-btn" onclick="filterScore(9, this)">9+ Perfect</button>

  <label class="toggle" title="Hide the Moderate (5-6) sections">
    <input id="toggle-hide-moderate" type="checkbox" checked onchange="toggleHideModerate(this.checked)">
    Hide 5-6
  </label>

  <span class="filter-label" style="margin-left:1rem">Status:</span>
   <select class="select-input" onchange="filterStatus(this.value)">
      <option value="active" selected>Active</option>
      <option value="">All</option>
      <option value="ready">Ready</option>
      <option value="selected">Selected</option>
      <option value="prepared">Prepared</option>
      <option value="in_progress">In progress</option>
      <option value="applied">Applied</option>
      <option value="failed">Failed</option>
      <option value="skipped">Skipped</option>
      <option value="blocked">Blocked</option>
      <option value="manual">Manual</option>
    </select>

  <span class="filter-label">Role:</span>
  <select class="select-input" onchange="filterRole(this.value)">
    <option value="">All</option>
    {
        "".join(
            f'<option value="{escape((str(r["role_name"] or "").replace("(Unassigned)", "Unassigned").lower()))}">{escape(str(r["role_name"] or "Unassigned"))}</option>'
            for r in role_stats
        )
    }
  </select>

  <span class="filter-label">Site:</span>
  <select class="select-input" onchange="filterSite(this.value)">
    <option value="">All</option>
    {
        "".join(
            f'<option value="{escape(s["site"] or "")}">{escape(s["site"] or "Unknown")}</option>' for s in site_stats
        )
    }
  </select>

  <span class="filter-label">Sponsorship:</span>
  <select class="select-input" onchange="filterSponsorship(this.value)">
    <option value="">Any</option>
    <option value="licensed_yes">Licensed sponsor</option>
    <option value="policy_yes">Sponsorship: Yes</option>
    <option value="policy_conditional">Sponsorship: Conditional</option>
    <option value="policy_no">Sponsorship: No</option>
    <option value="not_no">Exclude "No"</option>
    <option value="unknown">Unknown</option>
  </select>

  <span class="filter-label" style="margin-left:1rem">Search:</span>
  <input type="text" class="search-input" placeholder="Filter by title, company, tags..." oninput="filterText(this.value)">
  <button type="button" class="filter-btn" data-live="1" onclick="deleteVisibleJobs()">Delete shown</button>
  <button type="button" class="filter-btn" data-live="1" onclick="deleteRoleJobs()">Delete role</button>
</div>

 <div class="filters motion-2" id="pipeline-controls" style="margin-top:-0.75rem">
  <span class="filter-label">Pipeline:</span>
  <span class="filter-label" style="opacity:0.85">Presets:</span>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['discover','enrich','score','tailor','statement','cover','pdf'])">All</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['discover','enrich','score'])">Prep-only</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['tailor','statement','cover','pdf'])">Apply-only</button>

  <span class="filter-label" style="margin-left:0.5rem;opacity:0.85">Stages:</span>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['discover'])">Discover</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['enrich'])">Enrich</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['score'])">Score</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineScoreRepair()">Score repair</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['tailor'])">Tailor</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['statement'])">Statement</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['cover'])">Cover</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-run="1" onclick="pipelineRun(['pdf'])">PDF</button>
  <span class="filter-label" id="pipe-status" style="margin-left:0.5rem;opacity:0.9">Idle</span>
  <span class="filter-label" style="margin-left:0.5rem">Min score:</span>
  <input id="pipe-min-score" class="search-input" style="width:64px" value="7" inputmode="numeric" pattern="[0-9]*">
  <span class="filter-label">Workers:</span>
  <input id="pipe-workers" class="search-input" style="width:64px" value="1" inputmode="numeric" pattern="[0-9]*">
  <label class="toggle" title="Run stages concurrently">
    <input id="pipe-stream" type="checkbox"> Stream
  </label>
  <label class="toggle" title="Preview stages without executing">
    <input id="pipe-dry-run" type="checkbox"> Dry run
  </label>
  <label class="toggle" title="Tailor/Cover/Apply only process picked jobs">
    <input id="pipe-selected-only" type="checkbox"> Selected only
  </label>
  <label class="toggle" title="Reduce strict tailor validation blockers for faster approvals">
    <input id="pipe-tailor-lenient" type="checkbox"> Lenient tailor mode
  </label>
  <span class="filter-label" style="margin-left:0.5rem;opacity:0.85">Discover config:</span>
  <input id="pipe-search-query" class="search-input" style="width:220px" placeholder="Role/query override (optional)">
  <input id="pipe-jobspy-sites" class="search-input" style="width:160px" placeholder="JobSpy sites (csv)">
  <input id="pipe-smarte-sites" class="search-input" style="width:180px" placeholder="Smart sites (csv names)">
  <input id="pipe-results-per-site" class="search-input" style="width:86px" inputmode="numeric" pattern="[0-9]*" placeholder="Results">
  <input id="pipe-hours-old" class="search-input" style="width:86px" inputmode="numeric" pattern="[0-9]*" placeholder="Hours">
  <label class="toggle" title="Skip JobSpy discovery">
    <input id="pipe-skip-jobspy" type="checkbox"> No JobSpy
  </label>
  <label class="toggle" title="Skip Workday discovery">
    <input id="pipe-skip-workday" type="checkbox"> No Workday
  </label>
  <label class="toggle" title="Skip smart-extract discovery">
    <input id="pipe-skip-smarte" type="checkbox"> No Smart
  </label>
  <button type="button" class="filter-btn" data-live="1" data-pipe-stop="1" onclick="pipelineStop()">Stop</button>
  <button type="button" class="filter-btn" data-live="1" data-pipe-clear="1" onclick="pipelineClear()">Clear</button>
</div>

 <div class="score-section motion-3">
  <div class="score-dist">
    <details class="panel" open>
      <summary>Score Distribution</summary>
      <div class="panel-body">{score_bars}</div>
    </details>
  </div>
  <div class="sites-section">
    <details class="panel" open>
      <summary>By Source</summary>
      <div class="panel-body">{site_rows}</div>
    </details>
  </div>
 </div>

  <details class="panel motion-3" id="setup-panel" open>
    <summary>Setup</summary>
    <div class="panel-body setup-body">
      <div class="meta" style="margin:0 0 0.65rem 0">
        <span class="meta-tag">Workspace</span>
        <span class="meta-tag" title="ApplyPilot workspace">{escape(str(setup.get("app_dir") or ""))}</span>
        <span class="meta-tag" id="setup-status-tag">checking...</span>
        <span class="meta-tag" id="setup-mode-tag" title="How this page is opened">mode: ?</span>
      </div>

      <div id="setup-api-hint" class="job-desc hidden" style="margin:0 0 0.85rem 0">
        Setup API unavailable. Open this dashboard via <code>applypilot dashboard-serve</code>.
      </div>

      <div class="setup-shell">
        <div class="setup-nav" aria-label="Setup navigation">
          <h3>Workbench</h3>
          <div class="nav-help">Set up your profile, resume, searches, and generation tools.</div>
          <button type="button" data-target="setup-sec-diagnostics" onclick="setupJump('setup-sec-diagnostics')">Diagnostics</button>
          <button type="button" data-target="setup-sec-studio" onclick="setupJump('setup-sec-studio')">Statement Studio</button>
          <button type="button" data-target="setup-sec-profile" onclick="setupJump('setup-sec-profile')">Quick Profile</button>
          <button type="button" data-target="setup-sec-full-profile" onclick="setupJump('setup-sec-full-profile')">Full Profile</button>
          <button type="button" data-target="setup-sec-resume" onclick="setupJump('setup-sec-resume')">Resume</button>
          <button type="button" data-target="setup-sec-tailoring" onclick="setupJump('setup-sec-tailoring')">Tailoring</button>
          <button type="button" data-target="setup-sec-searches" onclick="setupJump('setup-sec-searches')">Job Search</button>
        </div>

        <div class="setup-main">

          <div class="setup-statusbar" id="setup-sec-top">
            <div class="status-group">
              <span id="setup-pill-profile" class="status-pill {
        ("ok" if setup.get("has_profile") else "warn")
    }">Profile: {("ready" if setup.get("has_profile") else "missing")}</span>
              <span id="setup-pill-resume" class="status-pill {
        ("ok" if setup.get("has_resume_txt") else "warn")
    }">Resume: {("ready" if setup.get("has_resume_txt") else "missing")}</span>
              <span id="setup-pill-search" class="status-pill {
        ("ok" if setup.get("has_searches") else "warn")
    }">Search: {("ready" if setup.get("has_searches") else "missing")}</span>
            </div>
            <div class="status-group">
              <span class="status-pill" id="setup-tier">Tier: {escape(str(setup.get("tier") or "?"))}</span>
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupRefresh(this)">Refresh status</button>
            </div>
          </div>

      <section class="setup-section" id="setup-sec-diagnostics">
        <div class="section-head">
          <div class="section-title">Diagnostics</div>
          <div class="section-desc">Confirm local APIs are reachable and capture a clean debug bundle.</div>
        </div>
        <div class="panel-body" style="margin-top:0">
          <div class="meta" style="margin:0 0 0.5rem 0">
            <span class="meta-tag">health</span>
            <span class="meta-tag" id="diag-health">?</span>
            <span class="meta-tag">setup api</span>
            <span class="meta-tag" id="diag-setup">?</span>
            <span class="meta-tag">pipeline api</span>
            <span class="meta-tag" id="diag-pipe">?</span>
          </div>
          <div class="job-desc" id="diag-last-error" style="margin:0 0 0.55rem 0">No errors captured.</div>
          <div class="setup-actions">
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="diagPing()">Run checks</button>
            <button type="button" class="apply-link copy-btn" onclick="diagCopy()">Copy debug</button>
          </div>
        </div>
      </section>

      <section class="setup-section" id="setup-sec-studio">
        <div class="section-head">
          <div class="section-title">Statement Studio</div>
          <div class="section-desc">Paste job + resume, generate an NHS-ready supporting statement under the word cap.</div>
        </div>

        <div class="studio-grid">
          <div class="studio-pane">
            <div class="meta"><span class="meta-tag">Title</span></div>
            <input id="studio-title" class="search-input" placeholder="e.g. Data Analyst / IT Support">

            <div class="meta" style="margin-top:0.45rem"><span class="meta-tag">Organisation</span></div>
            <input id="studio-org" class="search-input" placeholder="e.g. NHS England">

            <div class="meta" style="margin-top:0.45rem;display:flex;gap:0.35rem;align-items:center;flex-wrap:wrap">
              <span class="meta-tag">Max words</span>
              <input id="studio-max-words" class="search-input" style="width:120px" value="1500" inputmode="numeric" pattern="[0-9]*">
              <span class="meta-tag" id="studio-count">0 words</span>
            </div>

            <textarea id="studio-resume" class="full-desc" style="min-height:200px" placeholder="Paste resume text here..."></textarea>
            <div class="setup-actions">
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="studioUseSavedResume()">Use saved resume.txt</button>
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="studioClear('studio-resume')">Clear resume</button>
            </div>
          </div>

          <div class="studio-pane">
            <div class="meta"><span class="meta-tag">Job description + person spec</span></div>
            <textarea id="studio-job" class="full-desc" style="min-height:260px" placeholder="Paste job description + person specification here..."></textarea>
            <div class="setup-actions">
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="studioClear('studio-job')">Clear job</button>
            </div>

            <div class="setup-actions" style="margin-top:0.2rem">
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="studioGenerate(this)">Generate statement</button>
              <button type="button" class="apply-link copy-btn" onclick="studioCopy()">Copy statement</button>
              <span class="job-desc" id="studio-status" style="margin-left:0.25rem">Idle.</span>
            </div>

            <textarea id="studio-output" class="full-desc studio-output" placeholder="Generated statement appears here..."></textarea>
          </div>
        </div>
      </section>

      <section class="setup-section" id="setup-sec-profile">
        <div class="section-head">
          <div class="section-title">Quick Profile</div>
          <div class="section-desc">Minimum profile fields used by scoring, tailoring and apply.</div>
        </div>
        <div>
          <div class="job-meta" style="margin-bottom:0.55rem">
            <span class="meta-tag">profile.json</span>
            <span class="meta-tag" id="has-profile">{("yes" if setup.get("has_profile") else "no")}</span>
          </div>
          <div class="job-meta" style="margin-bottom:0.55rem">
          </div>
          <div class="fields-2">
            <input id="setup-name" class="search-input" placeholder="Full name">
            <input id="setup-email" class="search-input" placeholder="Email">
            <input id="setup-phone" class="search-input" placeholder="Phone (optional)">
            <input id="setup-linkedin" class="search-input" placeholder="LinkedIn URL (optional)">
            <input id="setup-city" class="search-input" placeholder="City">
            <input id="setup-country" class="search-input" placeholder="Country">
            <input id="setup-target-role" class="search-input" placeholder="Target role (optional)">
            <input id="setup-years" class="search-input" placeholder="Years exp (optional)" inputmode="decimal">
          </div>
          <div class="setup-actions">
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupSaveProfile(this)">Save profile</button>
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupLoadWorkspace(false, this)">Load current</button>
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupRefresh(this)">Refresh status</button>
          </div>
          <div class="job-desc" style="margin-top:0.5rem">Saved into `profile.json` and used by scoring/tailoring/apply. Passwords/API keys stay in `.env` (not here).</div>
        </div>
      </section>

      <section class="setup-section" id="setup-sec-full-profile">
        <div class="section-head">
          <div class="section-title">Full Profile Editor</div>
          <div class="section-desc">Detailed `profile.json` control for accuracy and fewer auto-fill errors.</div>
        </div>
          <div class="job-meta" style="margin-bottom:0.55rem">
            <span class="meta-tag">profile.json</span>
            <span class="meta-tag">personal</span>
            <span class="meta-tag">work_authorization</span>
            <span class="meta-tag">availability</span>
            <span class="meta-tag">compensation</span>
            <span class="meta-tag">experience</span>
            <span class="meta-tag">eeo_voluntary</span>
          </div>

          <details class="panel" open style="margin:0 0 0.55rem 0">
            <summary>Identity & Contact</summary>
             <div class="panel-body fields-2">
              <input id="setup-full-name" class="search-input" placeholder="Full name">
              <input id="setup-pref-name" class="search-input" placeholder="Preferred name">
              <input id="setup-full-email" class="search-input" placeholder="Email">
              <input id="setup-full-phone" class="search-input" placeholder="Phone">
              <input id="setup-address" class="search-input" placeholder="Address">
              <input id="setup-postal" class="search-input" placeholder="Postal code">
              <input id="setup-city2" class="search-input" placeholder="City">
              <input id="setup-state" class="search-input" placeholder="Province / State">
              <input id="setup-country2" class="search-input" placeholder="Country">
              <input id="setup-linkedin2" class="search-input" placeholder="LinkedIn URL">
              <input id="setup-github" class="search-input" placeholder="GitHub URL">
              <input id="setup-portfolio" class="search-input" placeholder="Portfolio URL">
              <input id="setup-website" class="search-input" style="grid-column:1 / -1" placeholder="Website URL">
            </div>
          </details>

          <details class="panel" style="margin:0 0 0.55rem 0">
            <summary>Work Authorization & Availability</summary>
             <div class="panel-body fields-2">
              <label class="job-desc" style="margin:0">Legally authorized to work
                <select id="setup-auth-legal" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="Yes">Yes</option>
                  <option value="No">No</option>
                  <option value="Decline to answer">Decline to answer</option>
                </select>
              </label>
              <label class="job-desc" style="margin:0">Require sponsorship
                <select id="setup-auth-sponsor" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="Yes">Yes</option>
                  <option value="No">No</option>
                  <option value="Unknown">Unknown</option>
                </select>
              </label>
              <input id="setup-auth-permit" class="search-input" placeholder="Work permit type" style="grid-column:1 / -1">
              <input id="setup-availability-start" class="search-input" placeholder="Earliest start date (e.g. Immediately)" style="grid-column:1 / -1">
              <label class="toggle" style="grid-column:1 / 2"><input id="setup-avail-fulltime" type="checkbox"> Available for full-time</label>
              <label class="toggle" style="grid-column:2 / 3"><input id="setup-avail-contract" type="checkbox"> Available for contract</label>
            </div>
          </details>

          <details class="panel" style="margin:0 0 0.55rem 0">
            <summary>Compensation & Experience</summary>
             <div class="panel-body fields-2">
              <input id="setup-comp-salary" class="search-input" placeholder="Salary expectation (annual)">
              <label class="job-desc" style="margin:0">Salary currency
                <select id="setup-comp-currency" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="GBP">GBP</option>
                  <option value="USD">USD</option>
                  <option value="CAD">CAD</option>
                  <option value="EUR">EUR</option>
                </select>
              </label>
              <input id="setup-comp-min" class="search-input" placeholder="Salary range min">
              <input id="setup-comp-max" class="search-input" placeholder="Salary range max">
              <textarea id="setup-comp-note" class="full-desc" style="min-height:70px;max-height:120px;grid-column:1 / -1" placeholder="Currency conversion note (optional)"></textarea>

              <input id="setup-exp-years" class="search-input" placeholder="Years of experience" inputmode="decimal">
              <label class="job-desc" style="margin:0">Education level
                <select id="setup-exp-edu-level" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="High School">High School</option>
                  <option value="Associate Degree">Associate Degree</option>
                  <option value="Bachelor's Degree">Bachelor's Degree</option>
                  <option value="Master's Degree">Master's Degree</option>
                  <option value="PhD">PhD</option>
                </select>
              </label>
              <input id="setup-exp-title" class="search-input" placeholder="Current job title">
              <input id="setup-exp-company" class="search-input" placeholder="Current company">
              <input id="setup-exp-target-role" class="search-input" style="grid-column:1 / -1" placeholder="Target role">
            </div>
          </details>

          <details class="panel" style="margin:0 0 0.55rem 0">
            <summary>EEO Voluntary</summary>
             <div class="panel-body fields-2">
              <label class="job-desc" style="margin:0">Gender
                <select id="setup-eeo-gender" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="Female">Female</option>
                  <option value="Male">Male</option>
                  <option value="Non-binary">Non-binary</option>
                  <option value="Decline to self-identify">Decline to self-identify</option>
                </select>
              </label>
              <label class="job-desc" style="margin:0">Race/Ethnicity
                <select id="setup-eeo-race" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="White">White</option>
                  <option value="Black or African American">Black or African American</option>
                  <option value="Hispanic or Latino">Hispanic or Latino</option>
                  <option value="Asian">Asian</option>
                  <option value="Two or more races">Two or more races</option>
                  <option value="Decline to self-identify">Decline to self-identify</option>
                </select>
              </label>
              <label class="job-desc" style="margin:0">Veteran status
                <select id="setup-eeo-veteran" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="I am not a protected veteran">I am not a protected veteran</option>
                  <option value="I am a protected veteran">I am a protected veteran</option>
                  <option value="Decline to self-identify">Decline to self-identify</option>
                </select>
              </label>
              <label class="job-desc" style="margin:0">Disability status
                <select id="setup-eeo-disability" class="select-input" style="width:100%;margin-top:0.3rem">
                  <option value="">Select</option>
                  <option value="No, I do not have a disability">No, I do not have a disability</option>
                  <option value="Yes, I have a disability">Yes, I have a disability</option>
                  <option value="Decline to self-identify">Decline to self-identify</option>
                </select>
              </label>
            </div>
          </details>

          <label class="job-desc" style="margin:0">Full profile JSON (advanced)
            <textarea id="setup-full-profile-json" class="full-desc" style="min-height:140px;max-height:220px;margin-top:0.35rem" placeholder="Paste profile.json here, then click Apply JSON to form"></textarea>
          </label>

          <div style="margin-top:0.55rem;display:flex;gap:0.45rem;flex-wrap:wrap;align-items:center">
            <input id="setup-profile-json-file" type="file" accept="application/json,.json" style="max-width:100%">
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupImportProfileJsonFile(this)">Import + save profile.json</button>
          </div>

          <div style="margin-top:0.65rem;display:flex;gap:0.45rem;flex-wrap:wrap">
            <button type="button" class="apply-link copy-btn" onclick="setupFullProfileToJson(false)">Apply form to JSON</button>
            <button type="button" class="apply-link copy-btn" onclick="setupFullProfileFromJson(false)">Apply JSON to form</button>
            <button type="button" class="apply-link copy-btn" onclick="setupFullProfileFromLoaded(false)">Sync from loaded profile</button>
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupSaveFullProfile(this)">Save full profile</button>
          </div>
          <div class="job-desc" style="margin-top:0.5rem">Use this for complete profile control across all sections. Quick Profile is still available for fast edits.</div>
      </section>

      <section class="setup-section" id="setup-sec-resume">
        <div class="section-head">
          <div class="section-title">Resume</div>
          <div class="section-desc">`resume.txt` powers scoring/tailoring. PDF is optional reference.</div>
        </div>
          <div class="job-meta" style="margin-bottom:0.55rem">
            <span class="meta-tag">resume.txt</span>
            <span class="meta-tag" id="has-resume-txt">{("yes" if setup.get("has_resume_txt") else "no")}</span>
            <span class="meta-tag">resume.pdf</span>
            <span class="meta-tag" id="has-resume-pdf">{("yes" if setup.get("has_resume_pdf") else "no")}</span>
          </div>
          <textarea id="setup-resume-text" class="full-desc" style="min-height:220px" placeholder="Paste plain-text resume here..."></textarea>
          <div style="margin-top:0.55rem;display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
            <input id="setup-resume-pdf" type="file" accept="application/pdf,.pdf" style="max-width:100%">
            <span class="meta-tag" id="setup-resume-pdf-name">no file</span>
          </div>
          <div class="setup-actions">
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupSaveResumeText(this)">Save resume.txt</button>
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupUploadResumePdf(this)">Upload resume.pdf</button>
          </div>
          <div class="job-desc" style="margin-top:0.5rem">PDF upload is optional (for reference); scoring/tailoring reads `resume.txt`.</div>

          <div class="setup-card" style="margin-top:0.85rem">
            <div class="variants-head">
              <div>
                <div class="section-title" style="font-size:1.02rem">Resume Variants</div>
                <div class="section-desc">Keep separate base resumes for role families and sector mains, then route jobs deterministically.</div>
              </div>
              <div class="variants-tabs" role="tablist" aria-label="Resume variants">
                <button type="button" class="tab-btn active" data-variant-key="it_support_analyst" onclick="variantsSelect('it_support_analyst', this)">IT Support</button>
                <button type="button" class="tab-btn" data-variant-key="application_support_engineer" onclick="variantsSelect('application_support_engineer', this)">App Support</button>
                <button type="button" class="tab-btn" data-variant-key="technical_systems_analyst" onclick="variantsSelect('technical_systems_analyst', this)">Tech Systems</button>
                <button type="button" class="tab-btn" data-variant-key="public_sector_main" onclick="variantsSelect('public_sector_main', this)">Public</button>
                <button type="button" class="tab-btn" data-variant-key="commercial_main" onclick="variantsSelect('commercial_main', this)">Commercial</button>
                <button type="button" class="tab-btn" data-variant-key="public_it_support" onclick="variantsSelect('public_it_support', this)">Public IT</button>
                <button type="button" class="tab-btn" data-variant-key="public_application_support" onclick="variantsSelect('public_application_support', this)">Public App</button>
                <button type="button" class="tab-btn" data-variant-key="commercial_technical_support" onclick="variantsSelect('commercial_technical_support', this)">Commercial Tech</button>
                <button type="button" class="tab-btn" data-variant-key="data_analyst" onclick="variantsSelect('data_analyst', this)">Data</button>
                <button type="button" class="tab-btn" data-variant-key="software_testing" onclick="variantsSelect('software_testing', this)">Testing</button>
              </div>
            </div>

            <textarea id="variants-text" class="full-desc" style="min-height:200px" placeholder="Paste the resume variant text for the selected role family..."></textarea>
            <div class="setup-actions">
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="variantsLoad()">Load saved</button>
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="variantsUseMainResume()">Copy from resume.txt</button>
              <button type="button" class="apply-link copy-btn" data-live="1" onclick="variantsSave(this)">Save variant</button>
              <span class="meta-tag" id="variants-status">active: it_support_analyst</span>
            </div>
          </div>
      </section>

      <section class="setup-section" id="setup-sec-tailoring">
        <div class="section-head">
          <div class="section-title">Tailoring Intelligence</div>
          <div class="section-desc">Controls prompt packs, evidence grounding, and resume structure validation.</div>
        </div>
          <div class="job-meta" style="margin-bottom:0.55rem">
            <span class="meta-tag">profile.json</span>
            <span class="meta-tag">skills_boundary</span>
            <span class="meta-tag">resume_facts</span>
            <span class="meta-tag">resume_sections</span>
            <span class="meta-tag">resume_validation</span>
            <span class="meta-tag">tailoring</span>
          </div>
          <div class="fields-2">
            <label class="job-desc" style="margin:0">Role pack
              <select id="setup-role-pack" class="select-input" style="width:100%;margin-top:0.3rem">
                <option value="auto">Auto (recommended)</option>
                <option value="data_bi">Data / BI</option>
                <option value="engineering">Engineering</option>
                <option value="support">Support / IT Ops</option>
                <option value="application_support">Application Support</option>
                <option value="qa_testing">QA / Testing</option>
                <option value="cloud_platform">Cloud / Platform</option>
                <option value="business_analysis">Business Analysis</option>
              </select>
            </label>
            <label class="job-desc" style="margin:0">Draft candidates (2-3)
              <input id="setup-draft-count" class="search-input" inputmode="numeric" pattern="[0-9]*" placeholder="3" style="margin-top:0.3rem">
            </label>

            <div class="setup-card" style="grid-column:1 / -1">
              <div class="section-head" style="margin-bottom:0.45rem">
                <div class="section-title" style="font-size:1.02rem">Resume Template Builder (No JSON Needed)</div>
                <div class="section-desc">No JSON needed. Update skills/education/certs and preserved facts.</div>
              </div>
              <div class="job-desc" style="margin:0 0 0.55rem 0">Add/update skills, education, certifications, and preserved resume facts here. Use comma or newline separators. Legacy keys are auto-mapped (for example: bi_tools, spreadsheets, data_practices, capabilities).</div>
              <div class="fields-2" style="gap:0.45rem">
                <label class="job-desc" style="margin:0">Skills: Languages / Programming
                  <textarea id="setup-tpl-languages" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="SQL, Python, TypeScript"></textarea>
                </label>
                <label class="job-desc" style="margin:0">Skills: Frameworks
                  <textarea id="setup-tpl-frameworks" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="React, FastAPI, Django"></textarea>
                </label>
                <label class="job-desc" style="margin:0">Skills: Analytics
                  <textarea id="setup-tpl-analytics" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="Power BI, Tableau"></textarea>
                </label>
                <label class="job-desc" style="margin:0">Skills: Data / Databases
                  <textarea id="setup-tpl-data" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="ETL, Data Quality, PostgreSQL"></textarea>
                </label>
                <label class="job-desc" style="margin:0">Skills: Tools / Platforms
                  <textarea id="setup-tpl-tools" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="Git, Docker, AWS"></textarea>
                </label>
                <label class="job-desc" style="margin:0">Skills: Governance
                  <textarea id="setup-tpl-governance" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="GDPR, ISO 27001"></textarea>
                </label>

                <label class="job-desc" style="margin:0;grid-column:1 / -1">Education lines
                  <textarea id="setup-tpl-education" class="full-desc" style="min-height:82px;max-height:130px;margin-top:0.3rem" placeholder="MSc Data Science - University X"></textarea>
                </label>
                <label class="job-desc" style="margin:0;grid-column:1 / -1">Certifications
                  <textarea id="setup-tpl-certifications" class="full-desc" style="min-height:82px;max-height:130px;margin-top:0.3rem" placeholder="PL-300 Data Analyst Associate"></textarea>
                </label>
                <label class="job-desc" style="margin:0;grid-column:1 / -1">Technical environment
                  <textarea id="setup-tpl-techenv" class="full-desc" style="min-height:82px;max-height:130px;margin-top:0.3rem" placeholder="Tools: Power BI, Excel, SQL Server"></textarea>
                </label>

                <label class="job-desc" style="margin:0">Preserved projects
                  <textarea id="setup-tpl-preserved-projects" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="Supply Chain Dashboard"></textarea>
                </label>
                <label class="job-desc" style="margin:0">Preserved companies
                  <textarea id="setup-tpl-preserved-companies" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="Company A, Company B"></textarea>
                </label>
                <label class="job-desc" style="margin:0">Preserved school
                  <input id="setup-tpl-preserved-school" class="search-input" style="margin-top:0.3rem" placeholder="University of Derby">
                </label>
                <label class="job-desc" style="margin:0">Real metrics
                  <textarea id="setup-tpl-real-metrics" class="full-desc" style="min-height:74px;max-height:120px;margin-top:0.3rem" placeholder="40%, 25%, 2h -> 20m"></textarea>
                </label>
              </div>
              <div class="setup-actions" style="margin-top:0.55rem">
                <button type="button" class="apply-link copy-btn" onclick="setupTemplateFromJson(false)">Sync builder from JSON</button>
                <button type="button" class="apply-link copy-btn" onclick="setupTemplateToJson(false)">Apply builder to JSON</button>
                <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupRegenerateTailoredResumes(this)">Regenerate tailored resumes now</button>
                <span class="meta-tag">Auto-applied on Validate/Save</span>
                <span class="meta-tag" id="setup-template-summary">changes: none</span>
              </div>
            </div>

            <label class="job-desc" style="margin:0;grid-column:1 / -1">skills_boundary (JSON object)
              <textarea id="setup-skills-boundary" class="full-desc" style="min-height:110px;max-height:180px;margin-top:0.35rem" placeholder="JSON object (languages, analytics, tools)"></textarea>
            </label>

            <label class="job-desc" style="margin:0;grid-column:1 / -1">resume_facts (JSON object)
              <textarea id="setup-resume-facts" class="full-desc" style="min-height:110px;max-height:180px;margin-top:0.35rem" placeholder="JSON object (companies, projects, school, metrics)"></textarea>
            </label>

            <label class="job-desc" style="margin:0;grid-column:1 / -1">resume_sections (JSON object)
              <textarea id="setup-resume-sections" class="full-desc" style="min-height:110px;max-height:180px;margin-top:0.35rem" placeholder="JSON object (education, certifications, technical_environment)"></textarea>
            </label>

            <label class="job-desc" style="margin:0;grid-column:1 / -1">resume_validation (JSON object)
              <textarea id="setup-resume-validation" class="full-desc" style="min-height:110px;max-height:180px;margin-top:0.35rem" placeholder="JSON object (experience_bullets, project_bullets, required_sections)"></textarea>
            </label>

            <label class="job-desc" style="margin:0;grid-column:1 / -1">safe_synonyms (JSON object, optional)
              <textarea id="setup-safe-synonyms" class="full-desc" style="min-height:90px;max-height:160px;margin-top:0.35rem" placeholder="JSON object mapping canonical skill to safe synonyms"></textarea>
            </label>
          </div>
          <div class="setup-actions">
            <button type="button" class="apply-link copy-btn" onclick="setupValidateTailoring(this)">Validate JSON</button>
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupSaveTailoring(this)">Save tailoring config</button>
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupLoadWorkspace(false, this)">Reload from profile.json</button>
          </div>
          <div class="job-desc" style="margin-top:0.5rem">These fields power role prompt packs, evidence-grounded bullets, draft ranking, JD coverage optimization, quant checks, and adaptive section budgets.</div>
      </section>

      <section class="setup-section" id="setup-sec-searches">
        <div class="section-head">
          <div class="section-title">Job Search Config</div>
          <div class="section-desc">Build or paste `searches.yaml` with guardrails and UK smart sites.</div>
        </div>
          <div class="job-meta" style="margin-bottom:0.55rem">
            <span class="meta-tag">searches.yaml</span>
            <span class="meta-tag" id="has-searches">{("yes" if setup.get("has_searches") else "no")}</span>
            <span class="meta-tag">Tip</span>
            <span class="meta-tag">start from example</span>
          </div>
          <div class="job-desc" style="margin:0 0 0.65rem 0">Use the builder for safe defaults, or switch to Advanced YAML to paste/edit directly.</div>

          <div class="setup-actions" style="margin-top:0">
            <label class="toggle" title="Edit YAML directly">
              <input id="setup-adv-yaml" type="checkbox" onchange="setupToggleAdvancedYaml(this.checked)"> Advanced YAML
            </label>
            <button type="button" class="apply-link copy-btn" onclick="setupInsertSearchExample()">Insert example</button>
            <button type="button" class="apply-link copy-btn" data-live="1" onclick="setupSaveSearches(this)">Save searches.yaml</button>
          </div>

          <div id="setup-search-builder" style="margin-top:0.65rem">
            <div class="fields-2">
              <input id="search-country" class="search-input" placeholder="Country (e.g. USA, Canada, UK)">
              <input id="search-location" class="search-input" placeholder="Primary location text (e.g. Remote, New York, NY)">
              <label class="toggle" style="grid-column:1 / -1">
                <input id="search-remote" type="checkbox" checked onchange="setupSearchRemoteChanged(this.checked)"> Remote allowed
              </label>
              <input id="search-cities" class="search-input" style="grid-column:1 / -1" placeholder="Cities/regions (comma-separated, optional)">
              <input id="search-roles" class="search-input" style="grid-column:1 / -1" placeholder="Roles (comma-separated, e.g. Backend Engineer, Full Stack Developer)">
              <input id="search-hours-old" class="search-input" placeholder="Hours old (e.g. 72)" inputmode="numeric">
              <input id="search-results" class="search-input" placeholder="Results per site (e.g. 50)" inputmode="numeric">
              <input id="search-boards" class="search-input" style="grid-column:1 / -1" placeholder="JobSpy boards (csv: indeed,linkedin,glassdoor,zip_recruiter,google)">
              <input id="search-smart-sites" list="smart-site-options" class="search-input" style="grid-column:1 / -1" placeholder="Smart sites (csv by name, optional; empty = all)">
              <datalist id="smart-site-options">
                {"".join(f'<option value="{escape(n)}"></option>' for n in smart_site_names)}
              </datalist>
              <div class="setup-actions" style="grid-column:1 / -1;margin-top:0">
                <button type="button" class="apply-link copy-btn" onclick="setupUseUkSmartSites()">Use UK smart sites</button>
                <button type="button" class="apply-link copy-btn" onclick="setupClearSmartSites()">Clear smart filter</button>
                <span class="meta-tag">Pick 1 or many sites; empty uses all smart sites</span>
              </div>
              <textarea id="search-exclude" class="full-desc" style="min-height:88px;max-height:140px;grid-column:1 / -1" placeholder="Exclude title keywords (one per line, optional)"></textarea>
            </div>
            <div class="setup-actions">
              <button type="button" class="apply-link copy-btn" onclick="setupGenerateSearchesYaml()">Generate YAML</button>
              <span class="meta-tag" id="search-guardrails">guardrails: on</span>
            </div>
          </div>

          <textarea id="setup-searches" class="full-desc" style="display:none;min-height:160px;max-height:260px;margin-top:0.65rem" placeholder="searches.yaml (advanced mode)"></textarea>
          <div class="job-desc" style="margin-top:0.5rem">Guardrails: JobSpy boards are validated; smart site names support 1..N selection; hours_old capped to 720; results_per_site capped to 300; empty roles default to Software Engineer.</div>
      </section>

        </div>
      </div>
    </div>
  </details>

 <div id="job-count" class="job-count"></div>

 <div id="toast" class="toast" role="status" aria-live="polite"></div>

<details class="score-group" id="pipeline-console" open>
  <summary class="score-header" style="border-color:rgba(2,6,23,0.18)">
    <span class="score-badge" style="background:rgba(2,6,23,0.08);color:rgba(2,6,23,0.82)">*</span>
    Pipeline Console
  </summary>
  <div class="job-grid" style="grid-template-columns:1fr">
    <div class="job-card" style="padding:0.85rem">
      <div style="display:flex;gap:0.5rem;align-items:center;justify-content:space-between;flex-wrap:wrap">
        <div class="meta" style="margin:0">
          <span class="meta-tag">Recent runs</span>
          <span class="meta-tag" id="pipe-recent-hint">Click a run to load its log</span>
        </div>
        <div style="display:flex;gap:0.35rem;align-items:center;flex-wrap:wrap">
          <button type="button" class="apply-link copy-btn" data-live="1" onclick="pipelineRefreshRecent()">Refresh</button>
          <button type="button" class="apply-link copy-btn" onclick="pipelineJumpToBottom()">Bottom</button>
          <button type="button" class="apply-link copy-btn" data-live="1" onclick="pipelinePollOnce()">Poll</button>
        </div>
      </div>
      <div id="pipe-recent" style="margin-top:0.6rem; display:flex; flex-direction:column; gap:0.35rem"></div>
    </div>
    <pre id="pipeline-log" class="full-desc" style="max-height:260px"></pre>
  </div>
</details>

{job_sections}

 <script>
let minScore = 7;
let searchText = '';
let siteText = '';
let statusText = 'active';
let roleText = '';
let sponsorFilter = '';
let hideModerate = true;
const SMART_SITE_CATALOG = {smart_catalog_js};
const SMART_UK_DEFAULTS = {uk_smart_defaults_js};

// Performance: cache DOM lists once; avoid repeated expensive queries.
const _jobCards = Array.from(document.querySelectorAll('.job-card[data-id]'));
const _scoreGroups = Array.from(document.querySelectorAll('.score-group[data-score-group]'));
const _jobCountEl = document.getElementById('job-count');
let _filterTimer = null;

let _pipeSince = 0;
let _pipeTimer = null;
let _pipeStatusTimer = null;
let _pipeRunning = false;
let _pipeApiOk = true;
let _pipePollWarned = false;
let _setupLoadedProfile = {{}};

// Setup action UX helpers
function _btnBusy(btn, on, label) {{
  if (!btn) return;
  try {{
    if (on) {{
      const orig = btn.getAttribute('data-orig-text') || '';
      if (!orig) btn.setAttribute('data-orig-text', (btn.textContent || '').trim());
      btn.disabled = true;
      btn.textContent = label || 'Working...';
    }} else {{
      const orig = btn.getAttribute('data-orig-text') || '';
      if (orig) btn.textContent = orig;
      btn.disabled = false;
    }}
  }} catch (e) {{}}
}}

function setupJump(id) {{
  try {{
    const el = document.getElementById(id);
    if (!el) return;
    try {{ _setupNavSetActive(id); }} catch (e) {{}}
    el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }} catch (e) {{}}
}}

// Setup nav: highlight active section
function _setupNavSetActive(id) {{
  try {{
    const btns = document.querySelectorAll('.setup-nav button[data-target]');
    btns.forEach(b => b.classList.remove('active'));
    const b = document.querySelector(`.setup-nav button[data-target="${id}"]`);
    if (b) b.classList.add('active');
  }} catch (e) {{}}
}}

function setupInitNavObserver() {{
  try {{
    const sections = Array.from(document.querySelectorAll('.setup-section[id]'));
    if (!sections.length) return;
    const obs = new IntersectionObserver((entries) => {{
      try {{
        const vis = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => (b.intersectionRatio || 0) - (a.intersectionRatio || 0));
        if (vis && vis[0] && vis[0].target && vis[0].target.id) _setupNavSetActive(vis[0].target.id);
      }} catch (e) {{}}
    }}, {{ root: null, threshold: [0.15, 0.25, 0.35] }});
    sections.forEach(s => obs.observe(s));
  }} catch (e) {{}}
}}

// Resume variants manager
let _variantKey = 'it_support_analyst';
let _variantCache = {{}};
let _variantLoaded = {{}};

function variantsSelect(key, btn) {{
  _variantKey = String(key || '').trim().toLowerCase() || 'it_support_analyst';
  const tabs = document.querySelectorAll('.tab-btn[data-variant-key]');
  tabs.forEach(t => t.classList.remove('active'));
  if (btn) btn.classList.add('active');
  const st = document.getElementById('variants-status');
  if (st) st.textContent = 'active: ' + _variantKey;
  const ta = document.getElementById('variants-text');
  if (ta) ta.value = String(_variantCache[_variantKey] || '').trim();

  try {{
    if (window.location.protocol !== 'file:' && !(_variantLoaded[_variantKey])) {{
      const cur = ((ta || {{}}).value || '').trim();
      if (!cur) variantsLoad();
    }}
  }} catch (e) {{}}
}}

async function variantsLoad() {{
  if (window.location.protocol === 'file:') return;
  const st = document.getElementById('variants-status');
  if (st) st.textContent = 'loading ' + _variantKey + '...';
  try {{
    const res = await fetch('/api/setup/resume-variant?key=' + encodeURIComponent(_variantKey));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (!data || !data.ok) throw new Error((data && (data.detail || data.error)) || 'read_failed');
    const txt = String(data.text || '').trim();
    _variantCache[_variantKey] = txt;
    _variantLoaded[_variantKey] = true;
    const ta = document.getElementById('variants-text');
    if (ta) ta.value = txt;
    if (st) st.textContent = 'active: ' + _variantKey + (data.truncated ? ' (loaded, truncated)' : (txt ? ' (loaded)' : ' (no saved text)'));
    if (txt) showToast('Loaded ' + _variantKey + ' variant.', 'success', 2200);
    else showToast('No saved ' + _variantKey + ' variant yet.', 'warn', 2600);
  }} catch (e) {{
    if (st) st.textContent = 'load failed';
    showToast('Failed to load variants: ' + _errMsg(e), 'error', 3600);
  }}
}}

function variantsUseMainResume() {{
  const src = document.getElementById('setup-resume-text');
  const dst = document.getElementById('variants-text');
  if (!src || !dst) return;
  const t = (src.value || '').trim();
  if (!t) return;
  dst.value = t;
}}

async function variantsSave(btn) {{
  if (window.location.protocol === 'file:') return;
  const ta = document.getElementById('variants-text');
  const text = ((ta || {{}}).value || '').trim();
  if (!text) {{ showToast('Variant text is empty.', 'warn', 2600); return; }}
  return await _withAction(btn, {{ working: 'Saving...', success: 'variant saved', fail: 'save failed' }}, async () => {{
    await _apiJson('/api/setup/resume-variant', {{ key: _variantKey, text: text }});
    _variantCache[_variantKey] = text;
    _variantLoaded[_variantKey] = true;
    const st = document.getElementById('variants-status');
    if (st) st.textContent = 'active: ' + _variantKey + ' (saved)';
  }});
}}

function _errMsg(e) {{
  try {{
    if (!e) return '';
    if (typeof e === 'string') return e;
    if (e.message) return e.message;
    return '' + e;
  }} catch (ex) {{
    return '';
  }}
 }}

async function _apiJson(path, payload) {{
  const headers = {{ 'Content-Type': 'application/json' }};
  try {{
    const parts = (document.cookie || '').split(';');
    for (const p of parts) {{
      const s = (p || '').trim();
      if (!s) continue;
      if (s.startsWith('ap_csrf=')) {{
        const tok = decodeURIComponent(s.slice('ap_csrf='.length));
        if (tok) headers['X-CSRF-Token'] = tok;
      }}
    }}
  }} catch (e) {{}}
  const res = await fetch(path, {{
    method: 'POST',
    headers: headers,
    body: JSON.stringify(payload || {{}})
  }});
  if (!res.ok) {{
    const t = await res.text();
    throw new Error(t || ('HTTP ' + res.status));
  }}
  try {{
    return await res.json();
  }} catch (e) {{
    return {{ ok: true }};
  }}
 }}

async function _withAction(btn, opts, fn) {{
  const o = opts || {{}};
  _btnBusy(btn, true, o.working || 'Saving...');
  if (o.start) toast(o.start, 'info');
  try {{
    const out = await fn();
    if (o.success) toast(o.success, 'success');
    return out;
  }} catch (e) {{
    const msg = _errMsg(e);
    const shown = (o.fail || 'Action failed') + (msg ? (': ' + msg) : '');
    toast(shown, 'error', 4200);
    try {{ _diagSetErr(shown); }} catch (e2) {{}}
    throw e;
  }} finally {{
    _btnBusy(btn, false);
  }}
 }}

function filterScore(min, btn) {{
  minScore = min;
  if (hideModerate && minScore < 7) minScore = 7;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  applyFilters();
 }}

function toggleHideModerate(hide) {{
  hideModerate = !!hide;
  const btnAll = document.getElementById('btn-score-all');
  if (btnAll) btnAll.disabled = hideModerate;
  if (hideModerate && minScore < 7) {{
    minScore = 7;
    // Set the 7+ button as active.
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    const strongBtn = Array.from(document.querySelectorAll('.filter-btn')).find(b => (b.textContent || '').trim().startsWith('7+'));
    if (strongBtn) strongBtn.classList.add('active');
  }}
  applyFilters();
}}

function filterSite(site) {{
  siteText = (site || '').toLowerCase();
  applyFilters();
}}

function filterStatus(status) {{
  statusText = (status || '').toLowerCase();
  applyFilters();
}}

function filterRole(role) {{
  roleText = (role || '').toLowerCase();
  applyFilters();
}}

function filterSponsorship(val) {{
  sponsorFilter = (val || '').toLowerCase();
  applyFilters();
}}

function filterText(text) {{
  searchText = text.toLowerCase();
  // Debounce typing; filtering can touch thousands of cards.
  try {{ if (_filterTimer) clearTimeout(_filterTimer); }} catch (e) {{}}
  _filterTimer = setTimeout(() => {{ try {{ applyFilters(); }} catch (e) {{}} }}, 140);
 }}

function applyFilters() {{
  let shown = 0;
  const total = _jobCards.length;
  const groupVisible = {{}};

  for (let i = 0; i < _jobCards.length; i++) {{
    const card = _jobCards[i];
    const score = parseInt(card.dataset.score) || 0;
    const text = card.dataset.text || '';
    const scoreMatch = score >= (minScore || 5);
    const moderateMatch = !hideModerate || score >= 7;
    const textMatch = !searchText || text.includes(searchText);
    const siteMatch = !siteText || (card.dataset.site || '').toLowerCase() === siteText;
    const status = (card.dataset.status || '').toLowerCase();
    const statusMatch = !statusText
      || (statusText === 'active' && !['applied','failed','skipped','blocked','manual'].includes(status))
      || status === statusText;
    const roleMatch = !roleText || (card.dataset.role || '').toLowerCase() === roleText;

    const pol = (card.dataset.sponsorPolicy || '').toLowerCase();
    const lic = (card.dataset.sponsorLicensed || '').toLowerCase();
    let sponsorMatch = true;
    if (sponsorFilter === 'licensed_yes') sponsorMatch = (lic === 'yes');
    else if (sponsorFilter === 'policy_yes') sponsorMatch = (pol === 'yes');
    else if (sponsorFilter === 'policy_conditional') sponsorMatch = (pol === 'conditional');
    else if (sponsorFilter === 'policy_no') sponsorMatch = (pol === 'no');
    else if (sponsorFilter === 'not_no') sponsorMatch = (pol !== 'no');
    else if (sponsorFilter === 'unknown') sponsorMatch = (!pol || pol === 'unknown') && (!lic || lic === 'unknown');

    const ok = scoreMatch && moderateMatch && textMatch && siteMatch && statusMatch && roleMatch && sponsorMatch;
    if (ok) {{
      card.classList.remove('hidden');
      shown++;
      const g = card.dataset.group || card.dataset.score || '';
      if (g) groupVisible[g] = (groupVisible[g] || 0) + 1;
    }} else {{
      card.classList.add('hidden');
    }}
  }}

  if (_jobCountEl) _jobCountEl.textContent = `Showing ${{shown}} of ${{total}} jobs`;

  // Hide empty score groups without re-querying the DOM.
  for (let i = 0; i < _scoreGroups.length; i++) {{
    const group = _scoreGroups[i];
    const k = (group.dataset.scoreGroup || '').toString();
    group.style.display = (groupVisible[k] || 0) ? '' : 'none';
  }}
 }}

// Lazy-load heavy job fields (e.g. full descriptions) to keep the dashboard fast.
async function _lazyLoadJobField(detailsEl) {{
  try {{
    if (!detailsEl || !detailsEl.open) return;
    if ((detailsEl.dataset.loaded || '') === '1') return;
    const field = (detailsEl.dataset.field || '').trim();
    const jobId = (detailsEl.dataset.jobId || '').trim();
    const box = detailsEl.querySelector('.full-desc');
    if (!field || !jobId || !box) return;

    detailsEl.dataset.loaded = '1';
    if (window.location.protocol === 'file:') {{
      box.textContent = 'Open in served mode to load this section.';
      return;
    }}

    box.textContent = 'Loading...';
    const url = '/api/job/full-description?job_id=' + encodeURIComponent(jobId);
    const res = await fetch(url);
    const txt = await res.text();
    let j = {{}};
    try {{ j = JSON.parse(txt || '{{}}'); }} catch (e) {{ j = {{ ok: false, detail: txt }}; }}
    if (!res.ok || !j.ok) throw new Error((j && (j.detail || j.error)) || ('HTTP ' + res.status));
    box.textContent = String(j.text || '').trim();
  }} catch (e) {{
    try {{
      const box = detailsEl ? detailsEl.querySelector('.full-desc') : null;
      if (box) box.textContent = 'Load failed: ' + _errMsg(e);
    }} catch (e2) {{}}
  }}
}}

function _initLazyDetails() {{
  try {{
    document.querySelectorAll('details.full-desc-details.lazy[data-field]').forEach(d => {{
      d.addEventListener('toggle', () => {{
        try {{ if (d.open) _lazyLoadJobField(d); }} catch (e) {{}}
      }});
    }});
  }} catch (e) {{}}
}}

 // Debug helper: if clicks appear to do nothing, check if buttons are disabled.
 document.addEventListener('click', (ev) => {{
   try {{
     const t = ev.target;
     if (!t || !t.closest) return;
     const b = t.closest('button');
     if (!b) return;
     if (b.id === 'btn-score-all' || b.closest('#pipeline-controls')) {{
       console.log('[button click]', (b.textContent || '').trim(), 'disabled=', !!b.disabled);
     }}
   }} catch (e) {{}}
 }}, true);

toggleHideModerate(true);
applyFilters();
_initLazyDetails();

  function copyCmd(text) {{
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(() => {{
      // no-op
    }}).catch(() => {{
      prompt('Copy this command:', text);
    }});
  }} else {{
    prompt('Copy this command:', text);
  }}
 }}

  // Hide live-action buttons when opened as a local file.
  if (window.location.protocol === 'file:') {{
    const hint = document.getElementById('live-hint');
    if (hint) hint.classList.remove('hidden');
    document.querySelectorAll('[data-live="1"]').forEach(el => {{
      try {{ el.style.display = 'none'; }} catch (e) {{}}
    }});
    const pc = document.getElementById('pipeline-controls');
    if (pc) pc.style.display = 'none';
    const consoleBox = document.getElementById('pipeline-console');
    if (consoleBox) consoleBox.style.display = 'none';
    const setupPanel = document.getElementById('setup-panel');
    if (setupPanel) setupPanel.style.display = 'none';
  }}

  // In served mode, keep console updated.
  if (window.location.protocol !== 'file:') {{
    try {{
      const mt = document.getElementById('setup-mode-tag');
      if (mt) mt.textContent = 'mode: served';
    }} catch (e) {{}}

    pipelineCheckApiOnce();
    pipelineInitFromHistory();
    pipelineStartPolling();
    pipelinePollOnce();
    pipelineRefreshStatusOnce();
    setupRefresh(null);

    try {{
      const pdf = document.getElementById('setup-resume-pdf');
      if (pdf) pdf.addEventListener('change', () => setupUpdatePdfName());
      setupUpdatePdfName();
    }} catch (e) {{}}

    try {{
      const adv = document.getElementById('setup-adv-yaml');
      if (adv) setupToggleAdvancedYaml(!!adv.checked);
      setupInitSearchBuilderDefaults();
      setupLoadWorkspace(true, null);
      setupInitNavObserver();
      _setupNavSetActive('setup-sec-diagnostics');
    }} catch (e) {{}}

    // Lightweight ping to show whether APIs are reachable.
    try {{ diagPing(true); }} catch (e) {{}}
  }}

  if (window.location.protocol === 'file:') {{
    try {{
      const mt = document.getElementById('setup-mode-tag');
      if (mt) mt.textContent = 'mode: file';
    }} catch (e) {{}}
  }}

  // Surface JS/runtime errors in the UI for easier debugging.
  let _diagLastErr = '';
  function _diagSetErr(msg) {{
    _diagLastErr = (msg || '').toString().slice(0, 800);
    const el = document.getElementById('diag-last-error');
    if (el) el.textContent = _diagLastErr || 'No errors captured.';
    try {{
      const d = document.getElementById('setup-diagnostics');
      if (d && _diagLastErr) d.open = true;
    }} catch (e) {{}}
  }}
  window.addEventListener('error', (ev) => {{
    try {{
      const msg = (ev && (ev.message || (ev.error && ev.error.message))) || 'Unknown error';
      _diagSetErr('JS error: ' + msg);
    }} catch (e) {{}}
  }});
  window.addEventListener('unhandledrejection', (ev) => {{
    try {{
      const r = ev && ev.reason;
      const msg = (r && (r.message || ('' + r))) || 'Unknown rejection';
      _diagSetErr('Promise rejection: ' + msg);
    }} catch (e) {{}}
  }});

let _setupApiOk = true;

const _THEME_KEY = 'applypilot-theme:{escape(str(setup.get("app_dir") or base_dir))}';

function _applyTheme(theme) {{
  const root = document.documentElement;
  const next = (theme === 'dark') ? 'dark' : 'light';
  root.setAttribute('data-theme', next);
  try {{ localStorage.setItem(_THEME_KEY, next); }} catch (e) {{}}
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
}}

function _initTheme() {{
  let theme = 'light';
  try {{
    const saved = localStorage.getItem(_THEME_KEY);
    if (saved === 'dark' || saved === 'light') theme = saved;
    else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) theme = 'dark';
  }} catch (e) {{}}
  _applyTheme(theme);
}}

function toggleTheme() {{
  const current = (document.documentElement.getAttribute('data-theme') || 'light').toLowerCase();
  _applyTheme(current === 'dark' ? 'light' : 'dark');
}}

_initTheme();

async function setupRefresh(btn) {{
  if (window.location.protocol === 'file:') return;
  return await _withAction(btn, {{ working: 'Refreshing...' }}, async () => {{
    const res = await fetch('/api/setup/status');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const s = (data && data.status) || {{}};

    const pill = (id, ok, labelReady, labelMissing) => {{
      const el = document.getElementById(id);
      if (!el) return;
      const good = !!ok;
      el.className = 'status-pill ' + (good ? 'ok' : 'warn');
      el.textContent = good ? labelReady : labelMissing;
    }};

    const tag = document.getElementById('setup-status-tag');
    if (tag) {{
      const ok = !!(s.has_profile && s.has_resume_txt && s.has_searches);
      tag.textContent = ok ? 'ready' : 'incomplete';
      tag.style.background = ok ? 'rgba(16,185,129,0.14)' : 'rgba(245,158,11,0.14)';
      tag.style.borderColor = ok ? 'rgba(16,185,129,0.28)' : 'rgba(245,158,11,0.28)';
    }}
    const hp = document.getElementById('has-profile');
    const ht = document.getElementById('has-resume-txt');
    const hpdf = document.getElementById('has-resume-pdf');
    const hs = document.getElementById('has-searches');
    if (hp) hp.textContent = s.has_profile ? 'yes' : 'no';
    if (ht) ht.textContent = s.has_resume_txt ? 'yes' : 'no';
    if (hpdf) hpdf.textContent = s.has_resume_pdf ? 'yes' : 'no';
    if (hs) hs.textContent = s.has_searches ? 'yes' : 'no';

    pill('setup-pill-profile', s.has_profile, 'Profile: ready', 'Profile: missing');
    pill('setup-pill-resume', s.has_resume_txt, 'Resume: ready', 'Resume: missing');
    pill('setup-pill-search', s.has_searches, 'Search: ready', 'Search: missing');
    const tier = document.getElementById('setup-tier');
    if (tier) tier.textContent = 'Tier: ' + String((s.tier === null || typeof s.tier === 'undefined') ? '?' : s.tier);
    _setupApiOk = true;
    const hint = document.getElementById('setup-api-hint');
    if (hint) hint.classList.add('hidden');
    return data;
  }}).catch((e) => {{
    const tag = document.getElementById('setup-status-tag');
    if (tag) tag.textContent = 'offline';
    _setupApiOk = false;
    const hint = document.getElementById('setup-api-hint');
    if (hint) hint.classList.remove('hidden');
    try {{ _diagSetErr('Setup status failed: ' + _errMsg(e)); }} catch (e2) {{}}
    throw e;
  }});
}}

function setupToggleAdvancedYaml(on) {{
  const b = document.getElementById('setup-search-builder');
  const ta = document.getElementById('setup-searches');
  if (b) b.style.display = on ? 'none' : '';
  if (ta) ta.style.display = on ? '' : 'none';
}}

function setupInitSearchBuilderDefaults() {{
  const setIfEmpty = (id, v) => {{
    const el = document.getElementById(id);
    const cur = ((el || {{}}).value || '').toString().trim();
    if (!cur) _setVal(id, v);
  }};
  setIfEmpty('search-country', 'USA');
  setIfEmpty('search-location', 'Remote');
  setIfEmpty('search-cities', '');
  setIfEmpty('search-roles', 'Software Engineer');
  setIfEmpty('search-hours-old', '72');
  setIfEmpty('search-results', '50');
  setIfEmpty('search-boards', 'indeed,linkedin,glassdoor,zip_recruiter,google');
  if (SMART_UK_DEFAULTS && SMART_UK_DEFAULTS.length) setIfEmpty('search-smart-sites', SMART_UK_DEFAULTS.join(', '));
  const ex = document.getElementById('search-exclude');
  if (ex && !(ex.value || '').trim()) {{
    ex.value = ['intern', 'internship', 'principal', 'vp ', 'vice president', 'chief', 'clearance required'].join('\\n');
  }}
  const r = document.getElementById('search-remote');
  if (r) r.checked = true;
}}

function setupSearchRemoteChanged(isRemote) {{
  const loc = document.getElementById('search-location');
  if (!loc) return;
  if (isRemote) {{
    if (!(loc.value || '').trim()) loc.value = 'Remote';
  }}
}}

function _csv(v) {{
  return (v || '').split(',').map(s => (s || '').trim()).filter(Boolean);
}}

function _sanitizeBoard(b) {{
  const nb = (b || '').toLowerCase().replace(/\\s+/g, '').replace(/-/g, '_');
  const allowed = {{ indeed: 1, linkedin: 1, glassdoor: 1, zip_recruiter: 1, google: 1 }};
  return allowed[nb] ? nb : '';
}}

function _sanitizeSmartSite(name) {{
  const raw = (name || '').trim();
  if (!raw) return '';
  const lower = raw.toLowerCase();
  for (const n of SMART_SITE_CATALOG) {{
    if ((n || '').toLowerCase() === lower) return n;
  }}
  return '';
}}

function setupUseUkSmartSites() {{
  if (!SMART_UK_DEFAULTS || !SMART_UK_DEFAULTS.length) {{
    toast('No UK smart-site presets found', 'warn', 3200);
    return;
  }}
  _setVal('search-smart-sites', SMART_UK_DEFAULTS.join(', '));
  toast('UK smart sites selected', 'success');
}}

function setupClearSmartSites() {{
  _setVal('search-smart-sites', '');
  toast('Smart-site filter cleared (all enabled)', 'info');
}}

function setupGenerateSearchesYaml() {{
  const country = ((document.getElementById('search-country') || {{}}).value || '').trim()
    || ((document.getElementById('setup-country') || {{}}).value || '').trim()
    || 'USA';
  const location = ((document.getElementById('search-location') || {{}}).value || '').trim() || 'Remote';
  const cities = _csv(((document.getElementById('search-cities') || {{}}).value || '').trim());
  const roles = _csv(((document.getElementById('search-roles') || {{}}).value || '').trim());
  const remote = !!((document.getElementById('search-remote') || {{}}).checked);
  const hoursOldRaw = ((document.getElementById('search-hours-old') || {{}}).value || '').trim();
  const resultsRaw = ((document.getElementById('search-results') || {{}}).value || '').trim();
  const boardsRaw = _csv(((document.getElementById('search-boards') || {{}}).value || '').trim());
  const smartRaw = _csv(((document.getElementById('search-smart-sites') || {{}}).value || '').trim());
  const excludeRaw = ((document.getElementById('search-exclude') || {{}}).value || '').split('\\n');

  let hoursOld = parseInt(hoursOldRaw || '72', 10);
  let results = parseInt(resultsRaw || '50', 10);
  if (!isFinite(hoursOld) || hoursOld <= 0) hoursOld = 72;
  if (!isFinite(results) || results <= 0) results = 50;
  hoursOld = Math.max(1, Math.min(720, hoursOld));
  results = Math.max(1, Math.min(300, results));

  const roles2 = roles.length ? roles : ['Software Engineer'];
  const seen = {{}};
  const boards = [];
  for (const b of boardsRaw) {{
    const nb = _sanitizeBoard(b);
    if (nb && !seen[nb]) {{ seen[nb] = 1; boards.push(nb); }}
  }}
  if (!boards.length) boards.push('indeed');

  const smartSites = [];
  const seenSmart = {{}};
  let invalidSmart = 0;
  for (const s of smartRaw) {{
    const ns = _sanitizeSmartSite(s);
    if (!ns) {{ invalidSmart++; continue; }}
    const lk = ns.toLowerCase();
    if (!seenSmart[lk]) {{ seenSmart[lk] = 1; smartSites.push(ns); }}
  }}
  if (smartRaw.length && invalidSmart) {{
    toast('Some smart-site names were ignored (not in catalog)', 'warn', 3200);
  }}

  const exclude = [];
  for (const line of excludeRaw) {{
    const s = (line || '').trim();
    if (!s) continue;
    if (s.length > 80) continue;
    exclude.push(s);
    if (exclude.length >= 40) break;
  }}

  const esc = (s) => ('' + (s || '')).replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\"');
  const lines = [];
  lines.push('# ApplyPilot search configuration');
  lines.push('# Generated by dashboard setup');
  lines.push('');
  lines.push('queries:');
  for (let i = 0; i < roles2.length; i++) {{
    const tier = i < 2 ? 1 : (i < 5 ? 2 : 3);
    lines.push('  - query: "' + esc(roles2[i]) + '"');
    lines.push('    tier: ' + tier);
  }}
  lines.push('');
  lines.push('locations:');
  if (remote) {{
    lines.push('  - location: "Remote"');
    lines.push('    remote: true');
  }}
  const locs = [];
  const primary = location.toLowerCase() === 'remote' ? '' : location;
  if (primary) locs.push(primary);
  for (const c of cities) {{
    if (c && locs.indexOf(c) === -1) locs.push(c);
    if (locs.length >= 8) break;
  }}
  for (const l of locs) {{
    lines.push('  - location: "' + esc(l) + '"');
    lines.push('    remote: false');
  }}
  lines.push('');
  lines.push('country: "' + esc(country) + '"');
  lines.push('');
  lines.push('boards:');
  for (const b of boards) lines.push('  - ' + b);
  if (smartSites.length) {{
    lines.push('');
    lines.push('smart_sites:');
    for (const s of smartSites) lines.push('  - "' + esc(s) + '"');
  }}
  lines.push('');
  lines.push('defaults:');
  lines.push('  results_per_site: ' + results);
  lines.push('  hours_old: ' + hoursOld);

  if (exclude.length) {{
    lines.push('');
    lines.push('exclude_titles:');
    for (const x of exclude) lines.push('  - "' + esc(x) + '"');
  }}

  const yaml = lines.join('\\n') + '\\n';
  const ta = document.getElementById('setup-searches');
  if (ta) ta.value = yaml;

  const adv = document.getElementById('setup-adv-yaml');
  if (adv) adv.checked = true;
  setupToggleAdvancedYaml(true);
  toast('YAML generated', 'success');
}}

function _prettyJson(v) {{
  try {{
    return JSON.stringify(v || {{}}, null, 2);
  }} catch (e) {{
    return '{{}}';
  }}
}}

function _splitTemplateItems(raw) {{
  const nl = String.fromCharCode(10);
  const cr = String.fromCharCode(13);
  const text = (raw || '').split(cr).join(nl);
  const out = [];
  const seen = new Set();
  for (const line of text.split(nl)) {{
    for (const part of line.split(',')) {{
      const s = (part || '').trim();
      if (!s) continue;
      const key = s.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(s);
    }}
  }}
  return out;
}}

function _templateItemsFromAny(v) {{
  const nl = String.fromCharCode(10);
  if (Array.isArray(v)) return _splitTemplateItems(v.join(nl));
  if (typeof v === 'string') return _splitTemplateItems(v);
  return [];
}}

function _templateItemsFromKeys(obj, keys) {{
  const out = [];
  for (const k of (keys || [])) {{
    try {{ out.push(..._templateItemsFromAny((obj || {{}})[k])); }} catch (e) {{}}
  }}
  return _splitTemplateItems(out.join('\\n'));
}}

function _mergeTemplateItems(existingItems, incomingItems) {{
  const out = [];
  const seen = new Set();
  for (const src of [existingItems || [], incomingItems || []]) {{
    for (const item of src) {{
      const s = (item || '').toString().trim();
      if (!s) continue;
      const key = s.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(s);
    }}
  }}
  return out;
}}

function _pickTemplateTargetKey(obj, preferred, aliases) {{
  const o = obj || {{}};
  if (Object.prototype.hasOwnProperty.call(o, preferred)) return preferred;
  for (const k of (aliases || [])) {{
    if (Object.prototype.hasOwnProperty.call(o, k)) return k;
  }}
  return preferred;
}}

function _readJsonFieldOrEmpty(id) {{
  const raw = ((document.getElementById(id) || {{}}).value || '').trim();
  if (!raw) return {{}};
  try {{
    const obj = JSON.parse(raw);
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj;
  }} catch (e) {{}}
  return {{}};
}}

function _setTemplateItems(id, items) {{
  const nl = String.fromCharCode(10);
  _setVal(id, _templateItemsFromAny(items).join(nl));
}}

function _getTemplateItems(id) {{
  const raw = ((document.getElementById(id) || {{}}).value || '');
  return _splitTemplateItems(raw);
}}

function setupTemplateFromJson(quiet) {{
  const skillsBoundary = _readJsonFieldOrEmpty('setup-skills-boundary');
  const resumeFacts = _readJsonFieldOrEmpty('setup-resume-facts');
  const resumeSections = _readJsonFieldOrEmpty('setup-resume-sections');

  _setTemplateItems('setup-tpl-languages', _templateItemsFromKeys(skillsBoundary, ['languages', 'programming_languages']));
  _setTemplateItems('setup-tpl-frameworks', _templateItemsFromKeys(skillsBoundary, ['frameworks', 'libraries']));
  _setTemplateItems('setup-tpl-analytics', _templateItemsFromKeys(skillsBoundary, ['analytics', 'business_intelligence', 'bi', 'reporting', 'bi_tools']));
  _setTemplateItems('setup-tpl-data', _templateItemsFromKeys(skillsBoundary, ['data', 'databases', 'data_engineering', 'data_practices', 'capabilities']));
  _setTemplateItems('setup-tpl-tools', _templateItemsFromKeys(skillsBoundary, ['tools', 'platforms', 'devops', 'cloud', 'infrastructure', 'spreadsheets']));
  _setTemplateItems('setup-tpl-governance', _templateItemsFromKeys(skillsBoundary, ['governance', 'compliance', 'security']));
  _setTemplateItems('setup-tpl-education', resumeSections.education);
  _setTemplateItems('setup-tpl-certifications', resumeSections.certifications);
  _setTemplateItems('setup-tpl-techenv', resumeSections.technical_environment);

  _setTemplateItems('setup-tpl-preserved-projects', resumeFacts.preserved_projects);
  _setTemplateItems('setup-tpl-preserved-companies', resumeFacts.preserved_companies);
  _setVal('setup-tpl-preserved-school', (resumeFacts.preserved_school || ''));
  _setTemplateItems('setup-tpl-real-metrics', resumeFacts.real_metrics);

  if (!quiet) toast('Template builder synced from JSON', 'success', 2600);
}}

function setupTemplateToJson(quiet) {{
  const skillsBoundary = _readJsonFieldOrEmpty('setup-skills-boundary');
  const resumeFacts = _readJsonFieldOrEmpty('setup-resume-facts');
  const resumeSections = _readJsonFieldOrEmpty('setup-resume-sections');

  const languages = _getTemplateItems('setup-tpl-languages');
  const frameworks = _getTemplateItems('setup-tpl-frameworks');
  const analytics = _getTemplateItems('setup-tpl-analytics');
  const data = _getTemplateItems('setup-tpl-data');
  const tools = _getTemplateItems('setup-tpl-tools');
  const governance = _getTemplateItems('setup-tpl-governance');
  const summary = [];

  const langKey = _pickTemplateTargetKey(skillsBoundary, 'languages', ['programming_languages']);
  const curLang = _templateItemsFromKeys(skillsBoundary, ['languages', 'programming_languages']);
  if (languages.length) {{
    const merged = _mergeTemplateItems(curLang, languages);
    skillsBoundary[langKey] = merged;
    summary.push((curLang.length ? ('languages updated (+' + Math.max(0, merged.length - curLang.length) + ')') : ('languages added (' + merged.length + ')')));
  }} else if (curLang.length) {{
    summary.push('languages kept (' + curLang.length + ')');
  }}

  const fwKey = _pickTemplateTargetKey(skillsBoundary, 'frameworks', ['libraries']);
  const curFw = _templateItemsFromKeys(skillsBoundary, ['frameworks', 'libraries']);
  if (frameworks.length) {{
    const merged = _mergeTemplateItems(curFw, frameworks);
    skillsBoundary[fwKey] = merged;
    summary.push((curFw.length ? ('frameworks updated (+' + Math.max(0, merged.length - curFw.length) + ')') : ('frameworks added (' + merged.length + ')')));
  }} else if (curFw.length) {{
    summary.push('frameworks kept (' + curFw.length + ')');
  }}

  const anKey = _pickTemplateTargetKey(skillsBoundary, 'analytics', ['business_intelligence', 'bi', 'reporting', 'bi_tools']);
  const curAn = _templateItemsFromKeys(skillsBoundary, ['analytics', 'business_intelligence', 'bi', 'reporting', 'bi_tools']);
  if (analytics.length) {{
    const merged = _mergeTemplateItems(curAn, analytics);
    skillsBoundary[anKey] = merged;
    summary.push((curAn.length ? ('analytics updated (+' + Math.max(0, merged.length - curAn.length) + ')') : ('analytics added (' + merged.length + ')')));
  }} else if (curAn.length) {{
    summary.push('analytics kept (' + curAn.length + ')');
  }}

  const dataKey = _pickTemplateTargetKey(skillsBoundary, 'data', ['databases', 'data_engineering', 'data_practices', 'capabilities']);
  const curData = _templateItemsFromKeys(skillsBoundary, ['data', 'databases', 'data_engineering', 'data_practices', 'capabilities']);
  if (data.length) {{
    const merged = _mergeTemplateItems(curData, data);
    skillsBoundary[dataKey] = merged;
    summary.push((curData.length ? ('data updated (+' + Math.max(0, merged.length - curData.length) + ')') : ('data added (' + merged.length + ')')));
  }} else if (curData.length) {{
    summary.push('data kept (' + curData.length + ')');
  }}

  const toolKey = _pickTemplateTargetKey(skillsBoundary, 'tools', ['platforms', 'devops', 'cloud', 'infrastructure', 'spreadsheets']);
  const curTools = _templateItemsFromKeys(skillsBoundary, ['tools', 'platforms', 'devops', 'cloud', 'infrastructure', 'spreadsheets']);
  if (tools.length) {{
    const merged = _mergeTemplateItems(curTools, tools);
    skillsBoundary[toolKey] = merged;
    summary.push((curTools.length ? ('tools/platforms updated (+' + Math.max(0, merged.length - curTools.length) + ')') : ('tools/platforms added (' + merged.length + ')')));
  }} else if (curTools.length) {{
    summary.push('tools/platforms kept (' + curTools.length + ')');
  }}

  const govKey = _pickTemplateTargetKey(skillsBoundary, 'governance', ['compliance', 'security']);
  const curGov = _templateItemsFromKeys(skillsBoundary, ['governance', 'compliance', 'security']);
  if (governance.length) {{
    const merged = _mergeTemplateItems(curGov, governance);
    skillsBoundary[govKey] = merged;
    summary.push((curGov.length ? ('governance updated (+' + Math.max(0, merged.length - curGov.length) + ')') : ('governance added (' + merged.length + ')')));
  }} else if (curGov.length) {{
    summary.push('governance kept (' + curGov.length + ')');
  }}

  const education = _getTemplateItems('setup-tpl-education');
  const certifications = _getTemplateItems('setup-tpl-certifications');
  const techEnv = _getTemplateItems('setup-tpl-techenv');
  const curEdu = _templateItemsFromAny(resumeSections.education);
  const curCerts = _templateItemsFromAny(resumeSections.certifications);
  const curTech = _templateItemsFromAny(resumeSections.technical_environment);
  if (education.length) resumeSections.education = _mergeTemplateItems(curEdu, education);
  if (certifications.length) resumeSections.certifications = _mergeTemplateItems(curCerts, certifications);
  if (techEnv.length) resumeSections.technical_environment = _mergeTemplateItems(curTech, techEnv);

  const preservedProjects = _getTemplateItems('setup-tpl-preserved-projects');
  const preservedCompanies = _getTemplateItems('setup-tpl-preserved-companies');
  const realMetrics = _getTemplateItems('setup-tpl-real-metrics');
  const preservedSchool = (((document.getElementById('setup-tpl-preserved-school') || {{}}).value || '') + '').trim();

  const curProjects = _templateItemsFromAny(resumeFacts.preserved_projects);
  const curCompanies = _templateItemsFromAny(resumeFacts.preserved_companies);
  const curMetrics = _templateItemsFromAny(resumeFacts.real_metrics);

  if (preservedProjects.length) resumeFacts.preserved_projects = _mergeTemplateItems(curProjects, preservedProjects);
  if (preservedCompanies.length) resumeFacts.preserved_companies = _mergeTemplateItems(curCompanies, preservedCompanies);
  if (preservedSchool) resumeFacts.preserved_school = preservedSchool;
  if (realMetrics.length) resumeFacts.real_metrics = _mergeTemplateItems(curMetrics, realMetrics);

  _setVal('setup-skills-boundary', _prettyJson(skillsBoundary));
  _setVal('setup-resume-sections', _prettyJson(resumeSections));
  _setVal('setup-resume-facts', _prettyJson(resumeFacts));

  const sumEl = document.getElementById('setup-template-summary');
  if (sumEl) sumEl.textContent = summary.length ? ('changes: ' + summary.slice(0, 4).join(' | ')) : 'changes: none';

  if (!quiet) toast('Template builder applied to JSON', 'success', 2600);
}}

const _TAILOR_FIELD_IDS = [
  'setup-skills-boundary',
  'setup-resume-facts',
  'setup-resume-sections',
  'setup-resume-validation',
  'setup-safe-synonyms',
  'setup-draft-count',
  'setup-role-pack'
];

function _fieldErrId(id) {{
  return id + '-err';
}}

function _setFieldError(id, msg) {{
  const el = document.getElementById(id);
  if (!el) return;
  try {{
    el.classList.add('input-error');
    el.setAttribute('aria-invalid', 'true');
  }} catch (e) {{}}
  const wrap = el.parentElement || el;
  let out = document.getElementById(_fieldErrId(id));
  if (!out) {{
    out = document.createElement('div');
    out.id = _fieldErrId(id);
    out.className = 'field-error-msg';
    try {{ wrap.appendChild(out); }} catch (e) {{ return; }}
  }}
  out.textContent = msg || 'Invalid value';
}}

function _clearFieldError(id) {{
  const el = document.getElementById(id);
  if (el) {{
    try {{
      el.classList.remove('input-error');
      el.removeAttribute('aria-invalid');
    }} catch (e) {{}}
  }}
  const out = document.getElementById(_fieldErrId(id));
  if (out && out.parentElement) {{
    try {{ out.parentElement.removeChild(out); }} catch (e) {{}}
  }}
}}

function _clearTailoringErrors() {{
  for (const id of _TAILOR_FIELD_IDS) _clearFieldError(id);
}}

function _parseJsonField(id, label, fallback) {{
  _clearFieldError(id);
  const raw = ((document.getElementById(id) || {{}}).value || '').trim();
  if (!raw) return (fallback || {{}});
  let out = null;
  try {{
    out = JSON.parse(raw);
  }} catch (e) {{
    const msg = label + ' must be valid JSON: ' + (e && e.message ? e.message : e);
    _setFieldError(id, msg);
    throw new Error(msg);
  }}
  if (!out || typeof out !== 'object' || Array.isArray(out)) {{
    const msg = label + ' must be a JSON object';
    _setFieldError(id, msg);
    throw new Error(msg);
  }}
  return out;
}}

function _getVal(id) {{
  const el = document.getElementById(id);
  return ((el || {{}}).value || '').toString().trim();
}}

function _setChecked(id, on) {{
  const el = document.getElementById(id);
  if (!el) return;
  try {{ el.checked = !!on; }} catch (e) {{}}
}}

function _getChecked(id) {{
  const el = document.getElementById(id);
  return !!((el || {{}}).checked);
}}

function _yesNo(v, dflt) {{
  const s = (v == null ? '' : ('' + v)).trim().toLowerCase();
  if (!s) return dflt || '';
  if (['yes', 'y', 'true', '1'].includes(s)) return 'Yes';
  if (['no', 'n', 'false', '0'].includes(s)) return 'No';
  return dflt || '';
}}

function setupFullProfileFromObject(profileObj, quiet) {{
  const p = (profileObj && typeof profileObj === 'object') ? profileObj : {{}};
  const personal = p.personal || {{}};
  const auth = p.work_authorization || {{}};
  const availability = p.availability || {{}};
  const comp = p.compensation || {{}};
  const exp = p.experience || {{}};
  const eeo = p.eeo_voluntary || {{}};

  _setVal('setup-full-name', personal.full_name || '');
  _setVal('setup-pref-name', personal.preferred_name || '');
  _setVal('setup-full-email', personal.email || '');
  _setVal('setup-full-phone', personal.phone || '');
  _setVal('setup-address', personal.address || '');
  _setVal('setup-postal', personal.postal_code || '');
  _setVal('setup-city2', personal.city || '');
  _setVal('setup-state', personal.province_state || '');
  _setVal('setup-country2', personal.country || '');
  _setVal('setup-linkedin2', personal.linkedin_url || '');
  _setVal('setup-github', personal.github_url || '');
  _setVal('setup-portfolio', personal.portfolio_url || '');
  _setVal('setup-website', personal.website_url || '');

  _setVal('setup-auth-legal', auth.legally_authorized_to_work || '');
  _setVal('setup-auth-sponsor', auth.require_sponsorship || '');
  _setVal('setup-auth-permit', auth.work_permit_type || '');

  _setVal('setup-availability-start', availability.earliest_start_date || '');
  _setChecked('setup-avail-fulltime', _yesNo(availability.available_for_full_time, 'Yes') === 'Yes');
  _setChecked('setup-avail-contract', _yesNo(availability.available_for_contract, 'No') === 'Yes');

  _setVal('setup-comp-salary', comp.salary_expectation || '');
  _setVal('setup-comp-currency', comp.salary_currency || '');
  _setVal('setup-comp-min', comp.salary_range_min || '');
  _setVal('setup-comp-max', comp.salary_range_max || '');
  _setVal('setup-comp-note', comp.currency_conversion_note || '');

  _setVal('setup-exp-years', exp.years_of_experience_total || '');
  _setVal('setup-exp-edu-level', exp.education_level || '');
  _setVal('setup-exp-title', exp.current_job_title || '');
  _setVal('setup-exp-company', exp.current_company || '');
  _setVal('setup-exp-target-role', exp.target_role || '');

  _setVal('setup-eeo-gender', eeo.gender || '');
  _setVal('setup-eeo-race', eeo.race_ethnicity || '');
  _setVal('setup-eeo-veteran', eeo.veteran_status || '');
  _setVal('setup-eeo-disability', eeo.disability_status || '');

  // Keep quick profile fields aligned with full editor values.
  _setVal('setup-name', personal.full_name || '');
  _setVal('setup-email', personal.email || '');
  _setVal('setup-phone', personal.phone || '');
  _setVal('setup-city', personal.city || '');
  _setVal('setup-country', personal.country || '');
  _setVal('setup-linkedin', personal.linkedin_url || '');
  _setVal('setup-target-role', exp.target_role || '');
  _setVal('setup-years', exp.years_of_experience_total || '');

  if (!quiet) toast('Full profile form synced', 'success', 2600);
}}

function _collectFullProfilePatchFromForm() {{
  return {{
    personal: {{
      full_name: _getVal('setup-full-name'),
      preferred_name: _getVal('setup-pref-name'),
      email: _getVal('setup-full-email'),
      phone: _getVal('setup-full-phone'),
      address: _getVal('setup-address'),
      city: _getVal('setup-city2'),
      province_state: _getVal('setup-state'),
      country: _getVal('setup-country2'),
      postal_code: _getVal('setup-postal'),
      linkedin_url: _getVal('setup-linkedin2'),
      github_url: _getVal('setup-github'),
      portfolio_url: _getVal('setup-portfolio'),
      website_url: _getVal('setup-website')
    }},
    work_authorization: {{
      legally_authorized_to_work: _getVal('setup-auth-legal'),
      require_sponsorship: _getVal('setup-auth-sponsor'),
      work_permit_type: _getVal('setup-auth-permit')
    }},
    availability: {{
      earliest_start_date: _getVal('setup-availability-start'),
      available_for_full_time: _getChecked('setup-avail-fulltime') ? 'Yes' : 'No',
      available_for_contract: _getChecked('setup-avail-contract') ? 'Yes' : 'No'
    }},
    compensation: {{
      salary_expectation: _getVal('setup-comp-salary'),
      salary_currency: _getVal('setup-comp-currency'),
      salary_range_min: _getVal('setup-comp-min'),
      salary_range_max: _getVal('setup-comp-max'),
      currency_conversion_note: _getVal('setup-comp-note')
    }},
    experience: {{
      years_of_experience_total: _getVal('setup-exp-years'),
      education_level: _getVal('setup-exp-edu-level'),
      current_job_title: _getVal('setup-exp-title'),
      current_company: _getVal('setup-exp-company'),
      target_role: _getVal('setup-exp-target-role')
    }},
    eeo_voluntary: {{
      gender: _getVal('setup-eeo-gender'),
      race_ethnicity: _getVal('setup-eeo-race'),
      veteran_status: _getVal('setup-eeo-veteran'),
      disability_status: _getVal('setup-eeo-disability')
    }}
  }};
}}

function setupFullProfileToJson(quiet) {{
  const patch = _collectFullProfilePatchFromForm();
  _setVal('setup-full-profile-json', _prettyJson(patch));
  if (!quiet) toast('Full profile form applied to JSON', 'success', 2600);
}}

function setupFullProfileFromJson(quiet) {{
  const obj = _parseJsonField('setup-full-profile-json', 'full profile', {{}});
  setupFullProfileFromObject(obj, true);
  if (!quiet) toast('Full profile JSON applied to form', 'success', 2600);
}}

async function setupImportProfileJsonFile(btn) {{
  return await _withAction(btn, {{ working: 'Importing profile...', success: 'Profile imported and saved', fail: 'Import failed' }}, async () => {{
    const input = document.getElementById('setup-profile-json-file');
    const file = input && input.files && input.files.length ? input.files[0] : null;
    if (!file) throw new Error('Pick a profile.json file first');

    const text = await file.text();
    let obj = {{}};
    try {{
      obj = JSON.parse(text || '{{}}');
    }} catch (e) {{
      throw new Error('Invalid JSON file');
    }}
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {{
      throw new Error('profile.json must be an object');
    }}

    setupFullProfileFromObject(obj, true);
    _setVal('setup-full-profile-json', _prettyJson(obj));

    const tailoring = (obj.tailoring || {{}});
    _setVal('setup-role-pack', (tailoring.role_pack_override || 'auto'));
    _setVal('setup-draft-count', (tailoring.draft_candidates != null ? tailoring.draft_candidates : '3'));
    _setVal('setup-skills-boundary', _prettyJson(obj.skills_boundary || {{}}));
    _setVal('setup-resume-facts', _prettyJson(obj.resume_facts || {{}}));
    _setVal('setup-resume-sections', _prettyJson(obj.resume_sections || {{}}));
    _setVal('setup-resume-validation', _prettyJson(obj.resume_validation || {{}}));
    _setVal('setup-safe-synonyms', _prettyJson(tailoring.safe_synonyms || {{}}));
    setupTemplateFromJson(true);

    const country = (((obj.personal || {{}}).country || '') + '').trim();
    if (country) _setVal('search-country', country);

    await _apiJson('/api/setup/profile', {{ profile: obj }});
    await setupLoadWorkspace(true, null);
    await setupRefresh(null);
  }});
}}

function setupFullProfileFromLoaded(quiet) {{
  setupFullProfileFromObject(_setupLoadedProfile || {{}}, true);
  setupFullProfileToJson(true);
  if (!quiet) toast('Full profile synced from loaded workspace', 'success', 2600);
}}

async function setupSaveFullProfile(btn) {{
  return await _withAction(btn, {{ working: 'Saving full profile...', success: 'Full profile saved', fail: 'Save failed' }}, async () => {{
    setupFullProfileToJson(true);
    const patch = _parseJsonField('setup-full-profile-json', 'full profile', {{}});
    const personal = patch.personal || {{}};
    if (!String(personal.full_name || '').trim() || !String(personal.email || '').trim()) {{
      throw new Error('Full profile requires personal.full_name and personal.email');
    }}
    const email = String(personal.email || '').trim();
    if (!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)) {{
      throw new Error('personal.email is invalid');
    }}
    await _apiJson('/api/setup/profile', {{ profile: patch }});
    await setupLoadWorkspace(true, null);
    await setupRefresh(null);
  }});
}}

async function setupSaveProfile(btn) {{
  const name = ((document.getElementById('setup-name') || {{}}).value || '').trim();
  const email = ((document.getElementById('setup-email') || {{}}).value || '').trim();
  const phone = ((document.getElementById('setup-phone') || {{}}).value || '').trim();
  const linkedin = ((document.getElementById('setup-linkedin') || {{}}).value || '').trim();
  const city = ((document.getElementById('setup-city') || {{}}).value || '').trim();
  const country = ((document.getElementById('setup-country') || {{}}).value || '').trim();
  const targetRole = ((document.getElementById('setup-target-role') || {{}}).value || '').trim();
  const yearsRaw = ((document.getElementById('setup-years') || {{}}).value || '').trim();

  if (!name || !email || !city || !country) {{
    toast('Required: name, email, city, country', 'warn', 3200);
    return;
  }}

  if (!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)) {{
    toast('Invalid email', 'warn', 3200);
    return;
  }}

  if (linkedin && !/^https?:\\/\\//i.test(linkedin)) {{
    toast('LinkedIn URL must start with http(s)', 'warn', 3600);
    return;
  }}

  let years = '';
  if (yearsRaw) {{
    const y = parseFloat(yearsRaw);
    if (!isFinite(y) || y < 0 || y > 60) {{
      toast('Years exp must be 0-60', 'warn', 3200);
      return;
    }}
    years = '' + y;
  }}

  return await _withAction(btn, {{ working: 'Saving...', success: 'Profile saved', fail: 'Save failed' }}, async () => {{
    const payload = {{
      profile: {{
        personal: {{
          full_name: name,
          email: email,
          phone: phone,
          city: city,
          country: country,
          linkedin_url: linkedin
        }},
        experience: {{
          target_role: targetRole,
          years_of_experience_total: years
        }}
      }}
    }};
    await _apiJson('/api/setup/profile', payload);
    await setupLoadWorkspace(true, null);
    await setupRefresh(null);
  }});
}}

async function setupSaveTailoring(btn) {{
  _clearTailoringErrors();
  setupTemplateToJson(true);
  let draftCount = parseInt(((document.getElementById('setup-draft-count') || {{}}).value || '').trim() || '3', 10);
  if (!isFinite(draftCount) || draftCount <= 0) {{
    _setFieldError('setup-draft-count', 'Draft candidates must be a number in range 2..3');
    toast('Draft candidates must be a number in range 2..3', 'warn', 3800);
    return;
  }}
  draftCount = Math.max(2, Math.min(3, draftCount));

  let rolePack = (((document.getElementById('setup-role-pack') || {{}}).value || '').trim() || 'auto').toLowerCase();
  if (!['auto', 'data_bi', 'engineering', 'support', 'application_support', 'qa_testing', 'cloud_platform', 'business_analysis'].includes(rolePack)) {{
    _setFieldError('setup-role-pack', 'Role pack must be one of: auto, data_bi, engineering, support, application_support, qa_testing, cloud_platform, business_analysis');
    toast('Invalid role pack', 'warn', 3200);
    return;
  }}

  return await _withAction(btn, {{ working: 'Saving...', success: 'Tailoring config saved', fail: 'Save failed' }}, async () => {{
    const skillsBoundary = _parseJsonField('setup-skills-boundary', 'skills_boundary', {{}});
    const resumeFacts = _parseJsonField('setup-resume-facts', 'resume_facts', {{}});
    const resumeSections = _parseJsonField('setup-resume-sections', 'resume_sections', {{}});
    const resumeValidation = _parseJsonField('setup-resume-validation', 'resume_validation', {{}});
    const safeSynonyms = _parseJsonField('setup-safe-synonyms', 'safe_synonyms', {{}});

    const payload = {{
      profile: {{
        skills_boundary: skillsBoundary,
        resume_facts: resumeFacts,
        resume_sections: resumeSections,
        resume_validation: resumeValidation,
        tailoring: {{
          role_pack_override: rolePack,
          draft_candidates: draftCount,
          safe_synonyms: safeSynonyms
        }}
      }}
    }};

    await _apiJson('/api/setup/profile', payload);
    await setupLoadWorkspace(true, null);
    await setupRefresh(null);
  }});
}}

async function setupRegenerateTailoredResumes(btn) {{
  if (window.location.protocol === 'file:') {{
    toast('Use served mode: applypilot dashboard-serve', 'warn', 3600);
    return;
  }}

  return await _withAction(btn, {{ working: 'Starting tailor run...', success: 'Tailor run started', fail: 'Failed to start tailor run' }}, async () => {{
    // Persist latest template/profile edits first so tailoring uses fresh data.
    await setupSaveTailoring(null);

    // Force a real regenerate run (not dry-run), then restore prior checkbox.
    const dryEl = document.getElementById('pipe-dry-run');
    const prevDry = !!(dryEl && dryEl.checked);
    try {{
      if (dryEl) dryEl.checked = false;
      await pipelineRun(['tailor']);
    }} finally {{
      if (dryEl) dryEl.checked = prevDry;
    }}
  }});
}}

async function setupValidateTailoring(btn) {{
  _clearTailoringErrors();
  setupTemplateToJson(true);
  return await _withAction(btn, {{ working: 'Validating...', success: 'Tailoring JSON valid', fail: 'Validation failed' }}, async () => {{
    let draftCount = parseInt(((document.getElementById('setup-draft-count') || {{}}).value || '').trim() || '3', 10);
    if (!isFinite(draftCount) || draftCount <= 0) {{
      _setFieldError('setup-draft-count', 'Draft candidates must be a number in range 2..3');
      throw new Error('draft_candidates must be numeric and in range 2..3');
    }}
    draftCount = Math.max(2, Math.min(3, draftCount));

    let rolePack = (((document.getElementById('setup-role-pack') || {{}}).value || '').trim() || 'auto').toLowerCase();
    if (!['auto', 'data_bi', 'engineering', 'support', 'application_support', 'qa_testing', 'cloud_platform', 'business_analysis'].includes(rolePack)) {{
      _setFieldError('setup-role-pack', 'Role pack must be one of: auto, data_bi, engineering, support, application_support, qa_testing, cloud_platform, business_analysis');
      throw new Error('role_pack must be one of: auto, data_bi, engineering, support, application_support, qa_testing, cloud_platform, business_analysis');
    }}

    const skillsBoundary = _parseJsonField('setup-skills-boundary', 'skills_boundary', {{}});
    const resumeFacts = _parseJsonField('setup-resume-facts', 'resume_facts', {{}});
    const resumeSections = _parseJsonField('setup-resume-sections', 'resume_sections', {{}});
    const resumeValidation = _parseJsonField('setup-resume-validation', 'resume_validation', {{}});
    const safeSynonyms = _parseJsonField('setup-safe-synonyms', 'safe_synonyms', {{}});

    const sbKeys = Object.keys(skillsBoundary || {{}}).length;
    const rfKeys = Object.keys(resumeFacts || {{}}).length;
    const rsKeys = Object.keys(resumeSections || {{}}).length;
    const rvKeys = Object.keys(resumeValidation || {{}}).length;
    const ssKeys = Object.keys(safeSynonyms || {{}}).length;

    const msg = [
      'role_pack=' + rolePack,
      'draft_candidates=' + draftCount,
      'skills_boundary keys=' + sbKeys,
      'resume_facts keys=' + rfKeys,
      'resume_sections keys=' + rsKeys,
      'resume_validation keys=' + rvKeys,
      'safe_synonyms keys=' + ssKeys
    ].join(' | ');
    toast('Validated: ' + msg, 'success', 4200);
  }});
}}

function _setVal(id, v) {{
  const el = document.getElementById(id);
  if (!el) return;
  try {{ el.value = (v == null ? '' : ('' + v)); }} catch (e) {{}}
}}

async function setupLoadWorkspace(quiet, btn) {{
  if (window.location.protocol === 'file:') return;
  return await _withAction(btn, {{ working: 'Loading...', fail: 'Load failed' }}, async () => {{
    const res = await fetch('/api/setup/read');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const p = (data && data.profile) || {{}};
    _setupLoadedProfile = p;
    const personal = (p.personal || {{}});
    const exp = (p.experience || {{}});
    _setVal('setup-name', personal.full_name || '');
    _setVal('setup-email', personal.email || '');
    _setVal('setup-phone', personal.phone || '');
    _setVal('setup-city', personal.city || '');
    _setVal('setup-country', personal.country || '');
    _setVal('setup-linkedin', personal.linkedin_url || '');
    _setVal('setup-target-role', exp.target_role || '');
    _setVal('setup-years', exp.years_of_experience_total || '');
    setupFullProfileFromObject(p, true);
    setupFullProfileToJson(true);

    const tailoring = (p.tailoring || {{}});
    _setVal('setup-role-pack', (tailoring.role_pack_override || 'auto'));
    _setVal('setup-draft-count', (tailoring.draft_candidates != null ? tailoring.draft_candidates : '3'));
    _setVal('setup-skills-boundary', _prettyJson(p.skills_boundary || {{}}));
    _setVal('setup-resume-facts', _prettyJson(p.resume_facts || {{}}));
    _setVal('setup-resume-sections', _prettyJson(p.resume_sections || {{}}));
    _setVal('setup-resume-validation', _prettyJson(p.resume_validation || {{}}));
    _setVal('setup-safe-synonyms', _prettyJson(tailoring.safe_synonyms || {{}}));
    setupTemplateFromJson(true);
    _clearTailoringErrors();

    const rt = document.getElementById('setup-resume-text');
    if (rt && typeof data.resume_text === 'string') rt.value = data.resume_text;
    const st = document.getElementById('setup-searches');
    if (st && typeof data.searches_text === 'string') st.value = data.searches_text;

    // Resume variants presence indicator (dots on tabs)
    try {{
      const arr = (data && data.resume_variants) || [];
      const have = {{}};
      for (const v of arr) {{
        const k = String((v && v.key) || '').trim().toLowerCase();
        if (k) have[k] = true;
      }}
      const tabs = document.querySelectorAll('.tab-btn[data-variant-key]');
      tabs.forEach(b => {{
        const k = String(b.getAttribute('data-variant-key') || '').trim().toLowerCase();
        if (k && have[k]) b.classList.add('has');
        else b.classList.remove('has');
      }});
    }} catch (e) {{}}

    try {{
      const s = (data && data.searches) || {{}};
      const defs = (s.defaults || {{}});
      const locs = Array.isArray(s.locations) ? s.locations : [];
      const queries = Array.isArray(s.queries) ? s.queries : [];
      const boards = Array.isArray(s.boards) ? s.boards : (Array.isArray(s.sites) ? s.sites : []);
      const smartSites = Array.isArray(s.smart_sites) ? s.smart_sites : [];
      const country = (s.country || defs.country || personal.country || '').toString();
      _setVal('search-country', country || '');
      _setVal('search-hours-old', (defs.hours_old != null ? defs.hours_old : '72'));
      _setVal('search-results', (defs.results_per_site != null ? defs.results_per_site : '50'));
      if (boards && boards.length) _setVal('search-boards', boards.join(','));
      _setVal('search-smart-sites', (smartSites && smartSites.length) ? smartSites.join(', ') : '');

      let hasRemote = false;
      const cityList = [];
      for (const l of locs) {{
        if (!l || typeof l !== 'object') continue;
        const txt = (l.location || '').toString();
        const rem = !!l.remote;
        if (rem || txt.toLowerCase() === 'remote') hasRemote = true;
        else if (txt) cityList.push(txt);
      }}
      const r = document.getElementById('search-remote');
      if (r) r.checked = hasRemote;
      if (cityList.length) _setVal('search-cities', cityList.join(', '));

      const qList = [];
      for (const q of queries) {{
        if (!q || typeof q !== 'object') continue;
        const qq = (q.query || '').toString().trim();
        if (qq) qList.push(qq);
      }}
      if (qList.length) _setVal('search-roles', qList.join(', '));

      const ex = Array.isArray(s.exclude_titles) ? s.exclude_titles : [];
      const exTa = document.getElementById('search-exclude');
      if (exTa && ex.length) exTa.value = ex.map(x => ('' + x)).join('\\n');
    }} catch (e) {{}}

    await setupRefresh(null);
    if (!quiet) toast('Loaded', 'success');
    return data;
  }});
}}

// Keep searches YAML in sync when switching to advanced.
try {{
  const adv = document.getElementById('setup-adv-yaml');
  if (adv) adv.addEventListener('change', () => {{
    if (!adv.checked) return;
    const ta = document.getElementById('setup-searches');
    if (ta && !(ta.value || '').trim()) setupGenerateSearchesYaml();
  }});
}} catch (e) {{}}

async function setupSaveResumeText(btn) {{
  const text = ((document.getElementById('setup-resume-text') || {{}}).value || '').trim();
  if (!text) {{
    toast('Paste resume text', 'warn', 3200);
    return;
  }}
  return await _withAction(btn, {{ working: 'Saving...', success: 'resume.txt saved', fail: 'Save failed' }}, async () => {{
    await _apiJson('/api/setup/resume-text', {{ text: text }});
    await setupRefresh(null);
  }});
}}


function studioUseSavedResume() {{
  const src = document.getElementById('setup-resume-text');
  const dst = document.getElementById('studio-resume');
  if (!src || !dst) return;
  const t = (src.value || '').trim();
  if (t) dst.value = t;
}}

function studioClear(id) {{
  const el = document.getElementById(id);
  if (el) el.value = '';
}}

function studioCopy() {{
  const el = document.getElementById('studio-output');
  if (!el) return;
  const t = (el.value || '').trim();
  if (!t) return;
  navigator.clipboard.writeText(t);
}}

function _studioSetStatus(msg) {{
  const el = document.getElementById('studio-status');
  if (el) el.textContent = msg;
}}

function _studioSetCount(wc, max) {{
  const el = document.getElementById('studio-count');
  if (!el) return;
  if (max) el.textContent = String(wc) + ' words / ' + String(max);
  else el.textContent = String(wc) + ' words';
}}

async function studioGenerate(btn) {{
  const resume = ((document.getElementById('studio-resume') || {{}}).value || '').trim();
  const job = ((document.getElementById('studio-job') || {{}}).value || '').trim();
  const title = ((document.getElementById('studio-title') || {{}}).value || '').trim();
  const org = ((document.getElementById('studio-org') || {{}}).value || '').trim();
  const mwRaw = ((document.getElementById('studio-max-words') || {{}}).value || '').trim();
  const maxWords = mwRaw ? parseInt(mwRaw, 10) : 1500;

  if (!resume) {{ _studioSetStatus('Paste your resume first.'); return; }}
  if (!job) {{ _studioSetStatus('Paste the job description/person spec first.'); return; }}

  _studioSetStatus('Generating...');
  try {{
    const res = await _apiJson('/api/statement/generate', {{ resume_text: resume, job_text: job, title: title, org: org, max_words: maxWords }});
    if (!res || !res.ok) throw new Error((res && (res.detail || res.error)) || 'generate_failed');
    const out = document.getElementById('studio-output');
    if (out) out.value = String(res.statement || '').trim();
    _studioSetCount(parseInt(res.word_count || 0, 10) || 0, parseInt(res.max_words || 0, 10) || maxWords);
    _studioSetStatus('Done.');
  }} catch (e) {{
    _studioSetStatus('Failed: ' + (e && e.message ? e.message : String(e)));
  }}
}}

function setupUpdatePdfName() {{
  const el = document.getElementById('setup-resume-pdf');
  const out = document.getElementById('setup-resume-pdf-name');
  if (!out) return;
  try {{
    const f = el && el.files && el.files.length ? el.files[0] : null;
    if (!f) {{ out.textContent = 'no file'; return; }}
    out.textContent = (f.name || 'resume.pdf') + ' (' + Math.round((f.size || 0) / 1024) + ' KB)';
  }} catch (e) {{
    out.textContent = 'no file';
  }}
}}

function _arrayBufferToBase64(buf) {{
  let binary = '';
  const bytes = new Uint8Array(buf);
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {{
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }}
  return btoa(binary);
}}

async function setupUploadResumePdf(btn) {{
  const inp = document.getElementById('setup-resume-pdf');
  const f = inp && inp.files && inp.files.length ? inp.files[0] : null;
  if (!f) {{ toast('Pick a PDF first', 'warn', 3200); return; }}
  const maxBytes = 12 * 1024 * 1024;
  if ((f.size || 0) > maxBytes) {{ toast('PDF too large (>12MB)', 'warn', 3600); return; }}
  if (f.type && f.type !== 'application/pdf') {{
    const n = (f.name || '').toLowerCase();
    if (!n.endsWith('.pdf')) {{ toast('Not a PDF', 'warn', 3200); return; }}
  }}
  return await _withAction(btn, {{ working: 'Uploading...', start: 'Uploading resume.pdf...', success: 'resume.pdf uploaded', fail: 'Upload failed' }}, async () => {{
    const buf = await f.arrayBuffer();
    const b64 = _arrayBufferToBase64(buf);
    await _apiJson('/api/setup/resume-pdf', {{ b64: b64 }});
    await setupRefresh(null);
  }});
}}

async function setupSaveSearches(btn) {{
  const adv = !!((document.getElementById('setup-adv-yaml') || {{}}).checked);
  if (!adv) setupGenerateSearchesYaml();
  const text = ((document.getElementById('setup-searches') || {{}}).value || '').trim();
  if (!text) {{
    toast('Paste searches.yaml', 'warn', 3200);
    return;
  }}
  return await _withAction(btn, {{ working: 'Saving...', success: 'searches.yaml saved', fail: 'Save failed' }}, async () => {{
    await _apiJson('/api/setup/searches', {{ text: text }});
    await setupLoadWorkspace(true, null);
    await setupRefresh(null);
  }});
}}

async function diagPing(quiet) {{
  const set = (id, txt, ok) => {{
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = txt;
    el.style.background = ok ? 'rgba(16,185,129,0.14)' : 'rgba(239,68,68,0.14)';
    el.style.borderColor = ok ? 'rgba(16,185,129,0.28)' : 'rgba(239,68,68,0.28)';
  }};

  if (window.location.protocol === 'file:') {{
    set('diag-health', 'file', false);
    set('diag-setup', 'file', false);
    set('diag-pipe', 'file', false);
    return;
  }}

  try {{
    const h = await fetch('/health');
    set('diag-health', h.ok ? 'ok' : ('HTTP ' + h.status), h.ok);
  }} catch (e) {{
    set('diag-health', 'error', false);
    _diagSetErr('Health check failed: ' + (e && e.message ? e.message : e));
  }}

  try {{
    const s = await fetch('/api/setup/status');
    set('diag-setup', s.ok ? 'ok' : ('HTTP ' + s.status), s.ok);
  }} catch (e) {{
    set('diag-setup', 'error', false);
  }}

  try {{
    const p = await fetch('/api/pipeline/status');
    set('diag-pipe', p.ok ? 'ok' : ('HTTP ' + p.status), p.ok);
  }} catch (e) {{
    set('diag-pipe', 'error', false);
  }}

   if (!quiet) toast('Diagnostics updated', 'success');
}}

function diagCopy() {{
  try {{
    const lines = [];
    lines.push('href=' + window.location.href);
    lines.push('protocol=' + window.location.protocol);
    lines.push('ua=' + (navigator.userAgent || ''));
    lines.push('setupApiOk=' + (_setupApiOk ? '1' : '0'));
    lines.push('pipeApiOk=' + (_pipeApiOk ? '1' : '0'));
    lines.push('lastErr=' + (_diagLastErr || ''));
    copyCmd(lines.join('\\n'));
    toast('Debug copied', 'success');
  }} catch (e) {{
    toast('Copy failed', 'error', 4200);
  }}
 }}

function setupInsertSearchExample() {{
  const ta = document.getElementById('setup-searches');
  if (!ta) return;
  if ((ta.value || '').trim()) {{
    const ok = confirm('Replace the current searches.yaml text?');
    if (!ok) return;
  }}
  ta.value = [
    '# ApplyPilot search configuration',
    'defaults:',
    '  location: "Remote"',
    '  distance: 0',
    '  hours_old: 72',
    '  results_per_site: 50',
    '',
    'locations:',
    '  - location: "Remote"',
    '    remote: true',
    '',
    'queries:',
    '  - query: "Software Engineer"',
    '    tier: 1',
    ''
  ].join('\\n');

  try {{
    const adv = document.getElementById('setup-adv-yaml');
    if (adv) adv.checked = true;
    setupToggleAdvancedYaml(true);
  }} catch (e) {{}}
  toast('Example inserted', 'success');
}}

 function toast(msg, kind, ms) {{
    const el = document.getElementById('toast');
    if (!el) return;
    const k = (kind || 'info').toString();
    try {{ el.dataset.kind = k; }} catch (e) {{}}
    el.textContent = (msg == null ? '' : ('' + msg));
    el.classList.add('show');
    window.clearTimeout(el._t);
    const dur = (ms == null ? 2200 : ms);
    el._t = window.setTimeout(() => el.classList.remove('show'), dur);
 }}

function _incStat(id, delta) {{
  const el = document.getElementById(id);
  if (!el) return;
  const n = parseInt((el.textContent || '').replace(/[^0-9]/g, '')) || 0;
  el.textContent = '' + (n + delta);
}}

function updateCardStatus(id, newStatus, newLabel) {{
  const card = document.querySelector('.job-card[data-id="' + id + '"]');
  if (!card) return;

  const oldStatus = (card.dataset.status || '').toLowerCase();
  const ns = (newStatus || '').toLowerCase();
  card.dataset.status = ns;

  const statusEl = card.querySelector('[data-role="status"]');
  if (statusEl) {{
    statusEl.textContent = newLabel || newStatus;
    statusEl.className = 'meta-tag status status-' + (newStatus || '');
  }}

  if (oldStatus !== ns) {{
    if (ns === 'applied') _incStat('stat-applied', 1);
    if (ns === 'failed' || ns === 'skipped') _incStat('stat-failed', 1);
    if (ns === 'blocked') _incStat('stat-blocked', 1);
  }}

  applyFilters();
}}

async function apiMark(id, status, reason) {{
  if (window.location.protocol === 'file:') {{
     throw new Error('Live actions require `applypilot dashboard-serve`');
  }}
  const payload = {{ id: parseInt(id), status: status, reason: reason || '' }};
  return await _apiJson('/api/jobs/mark', payload);
}}

async function apiBlock(id) {{
  if (window.location.protocol === 'file:') {{
     throw new Error('Live actions require `applypilot dashboard-serve`');
  }}
  return await _apiJson('/api/jobs/block', {{ id: parseInt(id) }});
}}

async function apiSelect(id, selected, exclusive) {{
  if (window.location.protocol === 'file:') {{
     throw new Error('Live actions require `applypilot dashboard-serve`');
  }}
  return await _apiJson('/api/jobs/select', {{ id: parseInt(id), selected: !!selected, exclusive: !!exclusive }});
}}

async function apiDeleteJob(id) {{
  if (window.location.protocol === 'file:') {{
     throw new Error('Live actions require `applypilot dashboard-serve`');
  }}
  return await _apiJson('/api/jobs/delete', {{ id: parseInt(id) }});
}}

async function apiDeleteJobsBulk(ids) {{
  if (window.location.protocol === 'file:') {{
     throw new Error('Live actions require `applypilot dashboard-serve`');
  }}
  return await _apiJson('/api/jobs/delete-bulk', {{ ids: ids || [] }});
}}

function _visibleJobIds() {{
  const ids = [];
  document.querySelectorAll('.job-card[data-id]:not(.hidden)').forEach(card => {{
    const id = parseInt(card.getAttribute('data-id') || '0', 10) || 0;
    if (id > 0) ids.push(id);
  }});
  return ids;
}}

async function deleteVisibleJobs() {{
  const ids = _visibleJobIds();
  if (!ids.length) {{
    toast('No visible jobs to delete', 'warn', 3200);
    return;
  }}
  if (!confirm('Delete ' + ids.length + ' visible jobs permanently? This cannot be undone.')) return;
  try {{
    const res = await apiDeleteJobsBulk(ids);
    toast('Deleted ' + ((res && res.deleted) || 0) + ' jobs', 'success');
    setTimeout(() => window.location.reload(), 300);
  }} catch (e) {{
    toast('Delete failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

async function deleteRoleJobs() {{
  if (!roleText) {{
    toast('Pick a role filter first', 'warn', 3200);
    return;
  }}
  const ids = _visibleJobIds();
  if (!ids.length) {{
    toast('No jobs to delete for this role', 'warn', 3200);
    return;
  }}
  if (!confirm('Delete all currently visible jobs for role "' + roleText + '"?')) return;
  try {{
    const res = await apiDeleteJobsBulk(ids);
    toast('Deleted ' + ((res && res.deleted) || 0) + ' jobs', 'success');
    setTimeout(() => window.location.reload(), 300);
  }} catch (e) {{
    toast('Delete failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

async function apiPipelineRun(stages, opts) {{
  const payload = {{
    stages: stages,
    min_score: (opts && opts.min_score) || 7,
    workers: (opts && opts.workers) || 1,
    stream: !!(opts && opts.stream),
    dry_run: !!(opts && opts.dry_run),
    search_query: (opts && opts.search_query) || '',
    jobspy_sites: (opts && opts.jobspy_sites) || '',
    smarte_sites: (opts && opts.smarte_sites) || '',
    results_per_site: (opts && opts.results_per_site) || '',
    hours_old: (opts && opts.hours_old) || '',
    selected_only: !!(opts && opts.selected_only),
    tailor_lenient: !!(opts && opts.tailor_lenient),
    discover_skip_jobspy: !!(opts && opts.discover_skip_jobspy),
    discover_skip_workday: !!(opts && opts.discover_skip_workday),
    discover_skip_smarte: !!(opts && opts.discover_skip_smarte)
  }};
  return await _apiJson('/api/pipeline/run', payload);
}}

async function apiPipelineStop() {{
  return await _apiJson('/api/pipeline/stop', {{}});
}}

async function apiScoreRepair() {{
  return await _apiJson('/api/score/repair', {{}});
}}

async function apiPipelineLogs(since) {{
  const res = await fetch('/api/pipeline/logs?since=' + (since || 0) + '&limit=250');
  if (!res.ok) {{
    const t = await res.text();
    throw new Error(t || ('HTTP ' + res.status));
  }}
  return await res.json();
}}

async function apiPipelineStatus() {{
  const res = await fetch('/api/pipeline/status');
  if (!res.ok) {{
    const t = await res.text();
    throw new Error(t || ('HTTP ' + res.status));
  }}
  return await res.json();
}}

async function apiPipelineHistory(limit) {{
  const n = parseInt(limit || '20') || 20;
  const res = await fetch('/api/pipeline/history?limit=' + n);
  if (!res.ok) {{
    const t = await res.text();
    throw new Error(t || ('HTTP ' + res.status));
  }}
  return await res.json();
}}

async function apiPipelineSelect(runId) {{
  const rid = encodeURIComponent(runId || '');
  const res = await fetch('/api/pipeline/select?run_id=' + rid);
  if (!res.ok) {{
    const t = await res.text();
    throw new Error(t || ('HTTP ' + res.status));
  }}
  return await res.json();
}}

function _pipeEl() {{ return document.getElementById('pipeline-log'); }}

function pipelineJumpToBottom() {{
  const el = _pipeEl();
  if (!el) return;
  el.scrollTop = el.scrollHeight;
}}

function _pipeSetStatus(text) {{
  const el = document.getElementById('pipe-status');
  if (!el) return;
  el.textContent = text || '';
}}

function _pipeSetControlsEnabled(enabled) {{
  const pc = document.getElementById('pipeline-controls');
  if (!pc) return;
  // Only disable stage/preset run buttons while running; keep Stop/Clear enabled.
  pc.querySelectorAll('button[data-pipe-run="1"]').forEach(b => {{
    try {{ b.disabled = !enabled; }} catch (e) {{}}
  }});
  pc.querySelectorAll('button[data-pipe-stop="1"],button[data-pipe-clear="1"]').forEach(b => {{
    try {{ b.disabled = false; }} catch (e) {{}}
  }});
}}

function _pipeWarnOnce(msg) {{
  if (_pipePollWarned) return;
  _pipePollWarned = true;
  try {{
    _pipeAppend([msg]);
  }} catch (e) {{
    // no-op
  }}
}}

async function pipelineCheckApiOnce() {{
  // If the dashboard HTML is served by something other than ApplyPilot's
  // dashboard-serve, /api/* routes won't exist.
  try {{
    const h = await fetch('/health');
    if (!h.ok) throw new Error('health');
  }} catch (e) {{
    _pipeApiOk = false;
    _pipeSetStatus('API unavailable');
    _pipeSetControlsEnabled(false);
    const hint = document.getElementById('pipe-recent-hint');
    if (hint) hint.textContent = 'Start with: applypilot dashboard-serve';
    _pipeWarnOnce('[pipeline] API unavailable. Open this dashboard via `applypilot dashboard-serve`.');
    return false;
  }}

  try {{
    const s = await fetch('/api/pipeline/status');
    if (!s.ok) throw new Error('status');
    _pipeApiOk = true;
    return true;
  }} catch (e) {{
    _pipeApiOk = false;
    _pipeSetStatus('API unavailable');
    _pipeSetControlsEnabled(false);
    const hint = document.getElementById('pipe-recent-hint');
    if (hint) hint.textContent = 'This server has no ApplyPilot pipeline API';
    _pipeWarnOnce('[pipeline] /api/pipeline/status not reachable. Use `applypilot dashboard-serve`.');
    return false;
  }}
}}

function _fmtTs(ts) {{
  if (!ts) return '';
  try {{
    const d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString();
  }} catch (e) {{
    return '';
  }}
}}

function _fmtDur(started, ended) {{
  if (!started || !ended) return '';
  const s = Math.max(0, (ended - started));
  if (s < 1) return (Math.round(s * 1000) + 'ms');
  if (s < 60) return (s.toFixed(1) + 's');
  return (Math.round(s / 60) + 'm');
}}

function _renderRecentRuns(runs) {{
  const el = document.getElementById('pipe-recent');
  if (!el) return;
  el.textContent = '';
  if (!runs || !runs.length) {{
    const d = document.createElement('div');
    d.className = 'meta';
    d.textContent = 'No saved runs yet.';
    el.appendChild(d);
    return;
  }}

  runs.forEach(r => {{
    const runId = (r && r.run_id) ? ('' + r.run_id) : '';
    if (!runId) return;
    const stages = (r.stages && r.stages.length) ? r.stages.join(',') : 'all';
    const badge = (r.dry_run ? 'DRY' : 'RUN');
    const exit = (r.exit_code === 0) ? '0' : ((r.exit_code !== undefined && r.exit_code !== null) ? ('' + r.exit_code) : '');
    const when = _fmtTs(r.started_at);
    const dur = _fmtDur(r.started_at, r.ended_at);

    const extra = [];
    if (r.search_query) extra.push('q=' + r.search_query);
    if (r.jobspy_sites && r.jobspy_sites.length) extra.push('sites=' + (Array.isArray(r.jobspy_sites) ? r.jobspy_sites.join(',') : ('' + r.jobspy_sites)));
    if (r.smarte_sites && r.smarte_sites.length) extra.push('smart=' + (Array.isArray(r.smarte_sites) ? r.smarte_sites.join(',') : ('' + r.smarte_sites)));
    if (r.results_per_site) extra.push('n=' + r.results_per_site);
    if (r.hours_old) extra.push('h=' + r.hours_old);
    if (r.discover_skip_jobspy) extra.push('no_jobspy');
    if (r.discover_skip_workday) extra.push('no_workday');
    if (r.discover_skip_smarte) extra.push('no_smarte');
    if (r.tailor_lenient) extra.push('lenient_tailor');

    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.gap = '0.35rem';
    row.style.alignItems = 'center';
    row.style.flexWrap = 'wrap';

    const btn = document.createElement('button');
    btn.className = 'apply-link copy-btn';
    btn.setAttribute('data-live', '1');
    btn.textContent = runId;
    btn.title = 'Load this run log';
    btn.onclick = async () => {{
      try {{
        pipelineClear();
        _pipeSince = 0;
        await apiPipelineSelect(runId);
        await pipelinePollOnce();
        await pipelineRefreshStatusOnce();
        toast('Loaded run: ' + runId, 'success');
      }} catch (e) {{
        toast('Load run failed: ' + (e && e.message ? e.message : e), 'error', 4200);
      }}
    }};

    const tags = document.createElement('span');
    tags.className = 'meta';
    tags.textContent = badge + (exit ? (' exit ' + exit) : '') + ' | ' + stages + (extra.length ? (' | ' + extra.join(' ')) : '') + (dur ? (' | ' + dur) : '') + (when ? (' | ' + when) : '');

    row.appendChild(btn);
    row.appendChild(tags);
    el.appendChild(row);
  }});
}}

async function pipelineRefreshRecent() {{
  try {{
    const res = await apiPipelineHistory(10);
    const runs = (res && res.runs) || [];
    _renderRecentRuns(runs);
  }} catch (e) {{
    // no-op
  }}
}}

function _pipeAppend(lines) {{
  const el = _pipeEl();
  if (!el || !lines || !lines.length) return;
  const atBottom = (el.scrollTop + el.clientHeight) >= (el.scrollHeight - 8);
  el.textContent += (el.textContent ? '\\n' : '') + lines.join('\\n');
  if (atBottom) el.scrollTop = el.scrollHeight;
}}

async function pipelineInitFromHistory() {{
  try {{
    const res = await apiPipelineHistory(1);
    const runs = (res && res.runs) || [];
    if (!runs.length) return;
    const r = runs[0] || {{}};

    // Only seed the console if it's currently empty.
    const el = _pipeEl();
    if (el && (!el.textContent || !el.textContent.trim())) {{
      _pipeSince = 0;
      await pipelinePollOnce();
    }}

    if (r.min_score !== undefined && r.min_score !== null) {{
      const ms = document.getElementById('pipe-min-score');
      if (ms) ms.value = '' + r.min_score;
    }}
    if (r.workers !== undefined && r.workers !== null) {{
      const w = document.getElementById('pipe-workers');
      if (w) w.value = '' + r.workers;
    }}
    if (r.stream !== undefined && r.stream !== null) {{
      const s = document.getElementById('pipe-stream');
      if (s) s.checked = !!r.stream;
    }}
    if (r.dry_run !== undefined && r.dry_run !== null) {{
      const d = document.getElementById('pipe-dry-run');
      if (d) d.checked = !!r.dry_run;
    }}
    if (r.selected_only !== undefined && r.selected_only !== null) {{
      const so = document.getElementById('pipe-selected-only');
      if (so) so.checked = !!r.selected_only;
    }}
    if (r.tailor_lenient !== undefined && r.tailor_lenient !== null) {{
      const tl = document.getElementById('pipe-tailor-lenient');
      if (tl) tl.checked = !!r.tailor_lenient;
    }}

    // If a per-run search override was used, seed the inputs so reruns are easy.
    if (r.search_query) {{
      const q = document.getElementById('pipe-search-query');
      if (q && (!q.value || !q.value.trim())) q.value = '' + r.search_query;
    }}
    if (r.jobspy_sites && r.jobspy_sites.length) {{
      const s = document.getElementById('pipe-jobspy-sites');
      if (s && (!s.value || !s.value.trim())) s.value = (Array.isArray(r.jobspy_sites) ? r.jobspy_sites.join(',') : ('' + r.jobspy_sites));
    }}
    if (r.smarte_sites && r.smarte_sites.length) {{
      const s = document.getElementById('pipe-smarte-sites');
      if (s && (!s.value || !s.value.trim())) s.value = (Array.isArray(r.smarte_sites) ? r.smarte_sites.join(',') : ('' + r.smarte_sites));
    }}
    if (r.results_per_site) {{
      const rp = document.getElementById('pipe-results-per-site');
      if (rp && (!rp.value || !rp.value.trim())) rp.value = '' + r.results_per_site;
    }}
    if (r.hours_old) {{
      const ho = document.getElementById('pipe-hours-old');
      if (ho && (!ho.value || !ho.value.trim())) ho.value = '' + r.hours_old;
    }}
    if (r.discover_skip_jobspy !== undefined && r.discover_skip_jobspy !== null) {{
      const b = document.getElementById('pipe-skip-jobspy');
      if (b) b.checked = !!r.discover_skip_jobspy;
    }}
    if (r.discover_skip_workday !== undefined && r.discover_skip_workday !== null) {{
      const b = document.getElementById('pipe-skip-workday');
      if (b) b.checked = !!r.discover_skip_workday;
    }}
    if (r.discover_skip_smarte !== undefined && r.discover_skip_smarte !== null) {{
      const b = document.getElementById('pipe-skip-smarte');
      if (b) b.checked = !!r.discover_skip_smarte;
    }}

    pipelineRefreshRecent();
  }} catch (e) {{
    // no-op
  }}
}}

async function pipelinePollOnce() {{
  try {{
    const res = await apiPipelineLogs(_pipeSince);
    const logs = (res && res.logs) || {{}};
    if (logs.truncated) {{
      _pipeAppend(['[log truncated: showing recent output only]']);
    }}
    const entries = logs.entries || [];
    if (entries.length) {{
      _pipeAppend(entries.map(e => e.line));
      _pipeSince = entries[entries.length - 1].seq;
    }}
  }} catch (e) {{
    const wasOk = _pipeApiOk;
    _pipeApiOk = false;
    if (wasOk) _pipeWarnOnce('[pipeline] log polling failed: ' + (e && e.message ? e.message : e));
  }}
}}

async function pipelineRefreshStatusOnce() {{
  try {{
    const res = await apiPipelineStatus();
    const st = (res && res.status) || {{}};
    const running = !!(st.running || st.starting);
    _pipeRunning = running;

    if (st.starting) {{
      _pipeSetStatus('Starting...');
    }} else if (st.running) {{
      _pipeSetStatus('Running');
    }} else if (st.exit_code === 0) {{
      _pipeSetStatus('Done (0)');
    }} else if (st.exit_code !== null && st.exit_code !== undefined) {{
      _pipeSetStatus('Exit (' + st.exit_code + ')');
    }} else if (st.start_error) {{
      _pipeSetStatus('Start failed');
    }} else {{
      _pipeSetStatus('Idle');
    }}

    _pipeSetControlsEnabled(!running);
  }} catch (e) {{
    const wasOk = _pipeApiOk;
    _pipeApiOk = false;
    _pipeSetStatus('API unavailable');
    _pipeSetControlsEnabled(false);
    if (wasOk) _pipeWarnOnce('[pipeline] status polling failed: ' + (e && e.message ? e.message : e));
  }}
}}

function pipelineStartPolling() {{
  if (_pipeTimer) return;
  _pipeTimer = window.setInterval(pipelinePollOnce, 900);
  if (!_pipeStatusTimer) _pipeStatusTimer = window.setInterval(pipelineRefreshStatusOnce, 1200);
}}

function pipelineStopPolling() {{
  if (_pipeTimer) window.clearInterval(_pipeTimer);
  _pipeTimer = null;
  if (_pipeStatusTimer) window.clearInterval(_pipeStatusTimer);
  _pipeStatusTimer = null;
}}

async function pipelineRun(stages) {{
  if (window.location.protocol === 'file:') {{
    toast('Run pipeline: use applypilot dashboard-serve', 'warn', 3600);
    return;
  }}

  if (!(await pipelineCheckApiOnce())) {{
    toast('Pipeline API unavailable', 'error', 4200);
    return;
  }}

  const minScore = parseInt((document.getElementById('pipe-min-score') || {{}}).value || '7') || 7;
  const workers = parseInt((document.getElementById('pipe-workers') || {{}}).value || '1') || 1;
  const stream = !!((document.getElementById('pipe-stream') || {{}}).checked);
  const dryRun = !!((document.getElementById('pipe-dry-run') || {{}}).checked);
  const searchQuery = ((document.getElementById('pipe-search-query') || {{}}).value || '').trim();
  const jobspySites = ((document.getElementById('pipe-jobspy-sites') || {{}}).value || '').trim();
  const smarteSites = ((document.getElementById('pipe-smarte-sites') || {{}}).value || '').trim();
  const resultsPerSite = parseInt(((document.getElementById('pipe-results-per-site') || {{}}).value || '').trim() || '0') || 0;
  const hoursOld = parseInt(((document.getElementById('pipe-hours-old') || {{}}).value || '').trim() || '0') || 0;
  const selectedOnly = !!((document.getElementById('pipe-selected-only') || {{}}).checked);
  const tailorLenient = !!((document.getElementById('pipe-tailor-lenient') || {{}}).checked);
  const skipJobspy = !!((document.getElementById('pipe-skip-jobspy') || {{}}).checked);
  const skipWorkday = !!((document.getElementById('pipe-skip-workday') || {{}}).checked);
  const skipSmarte = !!((document.getElementById('pipe-skip-smarte') || {{}}).checked);
  try {{
    // Always start a run with a clean console so seq resets don't stall polling.
    pipelineClear();
    _pipeSetStatus('Starting...');
    _pipeAppend(['[pipeline] starting...']);
    pipelineJumpToBottom();
    const res = await apiPipelineRun(stages, {{
      min_score: minScore,
      workers: workers,
      stream: stream,
      dry_run: dryRun,
      search_query: searchQuery,
      jobspy_sites: jobspySites,
      smarte_sites: smarteSites,
      results_per_site: resultsPerSite,
      hours_old: hoursOld,
      selected_only: selectedOnly,
      tailor_lenient: tailorLenient,
      discover_skip_jobspy: skipJobspy,
      discover_skip_workday: skipWorkday,
      discover_skip_smarte: skipSmarte
    }});
    if (res && res.run_id) {{
      _pipeAppend(['[pipeline] run_id=' + res.run_id]);
    }}
    toast(dryRun ? 'Dry-run started' : 'Pipeline started');
    pipelineStartPolling();
    pipelinePollOnce();
    pipelineRefreshStatusOnce();
    pipelineRefreshRecent();
  }} catch (e) {{
    _pipeWarnOnce('[pipeline] start failed: ' + (e && e.message ? e.message : e));
    toast('Pipeline start failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

async function pipelineStop() {{
  try {{
    await apiPipelineStop();
    toast('Pipeline stop requested', 'success');
    pipelineRefreshStatusOnce();
  }} catch (e) {{
    _pipeWarnOnce('[pipeline] stop failed: ' + (e && e.message ? e.message : e));
    toast('Stop failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

async function pipelineScoreRepair() {{
  if (window.location.protocol === 'file:') {{
    toast('Score repair: use applypilot dashboard-serve', 'warn', 3600);
    return;
  }}

  if (!(await pipelineCheckApiOnce())) {{
    toast('Pipeline API unavailable', 'error', 4200);
    return;
  }}

  if (_pipeRunning) {{
    toast('Stop the running pipeline first', 'warn', 3200);
    return;
  }}

  if (!confirm('Repair zero scores from stored LLM output?')) return;

  try {{
    _pipeAppend(['[score-repair] starting...']);
    const res = await apiScoreRepair();
    const info = (res && res.result) || {{}};
    const recovered = parseInt(info.recovered || 0, 10) || 0;
    const candidates = parseInt(info.candidates || 0, 10) || 0;
    const remaining = parseInt(info.remaining_zero || 0, 10) || 0;
    _pipeAppend([
      '[score-repair] done: candidates=' + candidates + ' recovered=' + recovered + ' remaining_zero=' + remaining
    ]);
    pipelineJumpToBottom();
    toast('Score repair recovered ' + recovered + ' jobs', 'success');
    setTimeout(() => window.location.reload(), 450);
  }} catch (e) {{
    _pipeWarnOnce('[score-repair] failed: ' + (e && e.message ? e.message : e));
    toast('Score repair failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

function pipelineClear() {{
  const el = _pipeEl();
  if (!el) return;
  el.textContent = '';
  _pipeSince = 0;
}}

  function removeCard(id) {{
    const card = document.querySelector('.job-card[data-id="' + id + '"]');
    if (card) card.remove();
    applyFilters();
  }}

async function markApplied(id) {{
  try {{
    await apiMark(id, 'applied', '');
    updateCardStatus(id, 'applied', 'Applied');
    toast('Marked applied: #' + id, 'success');
  }} catch (e) {{
    toast('Mark applied failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

async function markFailed(id) {{
  const reason = prompt('Fail reason (optional):', 'manual') || 'manual';
  try {{
    await apiMark(id, 'failed', reason);
    updateCardStatus(id, 'failed', 'Failed');
    toast('Marked failed: #' + id, 'success');
  }} catch (e) {{
    toast('Mark failed failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

async function selectJob(id, selected, exclusive) {{
  try {{
    const r = await apiSelect(id, selected, exclusive);
    if (selected) {{
      updateCardStatus(id, 'selected', 'Selected');
      if ((r && r.cleared ? r.cleared : 0) > 0) {{
        toast('Picked only job #' + id + ' and cleared ' + r.cleared + ' old picks', 'success');
        setTimeout(() => window.location.reload(), 250);
      }} else {{
        toast('Picked for apply: #' + id, 'success');
      }}
    }} else {{
      updateCardStatus(id, 'ready', 'Ready');
      toast('Removed from picked list: #' + id, 'info');
    }}
  }} catch (e) {{
    toast('Pick failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}

async function blockJob(id) {{
  if (!confirm('Block and archive this job so it never appears again?')) return;
  try {{
    await apiBlock(id);
    updateCardStatus(id, 'blocked', 'Blocked');
    toast('Blocked job: #' + id, 'success');
  }} catch (e) {{
    toast('Block failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
 }}

async function deleteJob(id) {{
  if (!confirm('Delete this job permanently from the system?')) return;
  try {{
    await apiDeleteJob(id);
    removeCard(id);
    toast('Deleted job: #' + id, 'success');
  }} catch (e) {{
    toast('Delete failed: ' + (e && e.message ? e.message : e), 'error', 4200);
  }}
}}
 </script>

</div>

</body>
</html>"""

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    abs_path = str(out.resolve())
    if not quiet:
        console.print(f"[green]Dashboard written to {abs_path}[/green]")
    return abs_path


def open_dashboard(output_path: str | None = None) -> None:
    """Generate the dashboard and open it in the default browser.

    Args:
        output_path: Where to write the HTML file. Defaults to ~/.applypilot/dashboard.html.
    """
    path = generate_dashboard(output_path, quiet=False)
    console.print("[dim]Opening in browser...[/dim]")
    webbrowser.open(f"file:///{path}")
