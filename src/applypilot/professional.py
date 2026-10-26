"""Deterministic rendering of user-supplied professional facts."""
from __future__ import annotations

from copy import deepcopy


def credential_line(entry: dict) -> str:
    parts = [str(entry.get("name") or "").strip()]
    if entry.get("issuer"):
        parts.append(str(entry["issuer"]))
    if entry.get("awarded"):
        parts.append(str(entry["awarded"]))
    status = entry.get("status", "earned")
    if status != "earned":
        parts.append("In progress" if status == "in_progress" else "Expired")
    if entry.get("expires"):
        parts.append("Expires " + str(entry["expires"]))
    return " | ".join(parts)


def enrich_evidence(text: str, profile: dict, job: dict) -> tuple[str, dict]:
    profile = deepcopy(profile)
    sections = profile.setdefault("resume_sections", {})
    title = str(job.get("title") or "").lower()
    family = "application_support" if any(x in title for x in ("application support", "production support", "platform support")) else "support" if "support" in title else "data_bi" if any(x in title for x in ("data", "analytics", "reporting", "business intelligence")) else "engineering"
    raw_certs = sections.get("certifications") or []
    certs = raw_certs.splitlines() if isinstance(raw_certs, str) else list(raw_certs)
    structured_names = {str(entry.get("name") or "").strip().casefold() for entry in sections.get("credentials", [])}
    # A structured entry is authoritative for its name, including exclusion/status.
    certs = [line for line in certs if str(line).split("|", 1)[0].strip().casefold() not in structured_names]
    for entry in sections.get("credentials", []):
        if entry.get("include", True) and entry.get("family", "") in {"", "all", family}:
            line = credential_line(entry)
            if line and line not in certs:
                certs.append(line)
    sections["certifications"] = certs
    evidence = []
    professional = [e for e in sections.get("professional_experience", []) if e.get("include", True) and e.get("family", "all") in {"", "all", family}]
    sections["professional_experience"] = professional
    for entry in professional:
        evidence.extend([entry["header"], entry.get("subtitle", "")])
        evidence.extend("- " + b for b in entry.get("bullets", []))
        if " at " in entry["header"]:
            company = entry["header"].split(" at ", 1)[1].strip()
            facts = profile.setdefault("resume_facts", {})
            companies = list(facts.get("preserved_companies") or [])
            if company and company not in companies:
                companies.append(company)
                facts["preserved_companies"] = companies
    evidence.extend(certs)
    for project_key in ("projects", "data_projects", "support_projects", "application_support_projects"):
        group_family = {"projects": "all", "data_projects": "data_bi", "support_projects": "support", "application_support_projects": "application_support"}[project_key]
        entries = [p for p in sections.get(project_key, []) if p.get("include", True) and p.get("family", group_family) in {"", "all", family}]
        sections[project_key] = entries
        for project in entries:
            evidence += [project["header"], project.get("subtitle", "")]
            evidence.extend("- " + b for b in project.get("bullets", []))
    summary = sections.get("summary", [])
    evidence.extend(summary.splitlines() if isinstance(summary, str) else summary)
    for name, values in (profile.get("skills_boundary") or {}).items():
        evidence.append(name + ": " + ", ".join(values))
    for key in ("memberships", "professional_development"):
        items = sections.get(key, [])
        evidence.extend(items.splitlines() if isinstance(items, str) else items)
    if evidence:
        text += "\n\nADDITIONAL USER-SUPPLIED PROFESSIONAL TEXT (not automatically fact-verified)\n" + "\n".join(evidence)
    return text, profile
