"""Reviewed criteria and candidate evidence for NHS/public-sector writing.

This is an evidence planner, not an NHS shortlisting score or visa decision.
Employer form instructions and the actual field limit take precedence.
"""
from __future__ import annotations

import json

FIELDS = {"supporting_information", "qualifications", "experience", "knowledge_skills", "cover_letter", "other"}
SOURCE_TYPES = {"employment", "academic_project", "personal_project", "training", "volunteering", "other"}


def validated_plan(data: dict, kind: str) -> dict:
    person_spec = str(data.get("person_spec") or "").strip()
    values = str(data.get("trust_values") or "").strip()
    employer = str(data.get("employer_instructions") or "").strip()
    if len(person_spec) > 30000 or len(values) > 6000 or len(employer) > 6000:
        raise ValueError("Person specification/values/employer instructions exceed the input limits")
    field = str(data.get("application_field") or ("cover_letter" if kind == "cover" else "supporting_information"))
    priority = str(data.get("priority_filter") or "all")
    policy = str(data.get("employer_ai_policy") or "unknown")
    if field not in FIELDS or priority not in {"all", "essential", "desirable"} or policy not in {"unknown", "allowed", "limited", "not_allowed"}:
        raise ValueError("Choose valid application-field, priority and AI-policy settings")
    character_limit = int(data.get("max_characters") or 0)
    if character_limit and not 100 <= character_limit <= 30000:
        raise ValueError("Character limit must be 100–30000, or 0 when the form uses a word limit")
    rows = data.get("criteria_evidence") or []
    if not isinstance(rows, list) or len(rows) > 50:
        raise ValueError("Use up to 50 reviewed criterion rows")
    cleaned = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("Each criterion needs a structured evidence row")
        clean = {key: str(row.get(key) or "").strip() for key in ("criterion", "evidence", "situation", "task", "action", "result")}
        if not clean["criterion"] or len(clean["criterion"]) > 1200 or any(len(clean[k]) > 2400 for k in clean if k != "criterion"):
            raise ValueError("Provide a criterion under 1200 characters and bounded evidence/example fields")
        clean.update(id=str(row.get("id") or index)[:80], priority=row.get("priority", "essential"), category=row.get("category", "other"),
                     basis=row.get("basis", "gap"), source_type=row.get("source_type", "other"), confirmed=row.get("confirmed") is True)
        if clean["priority"] not in {"essential", "desirable"} or clean["category"] not in {"qualifications", "experience", "knowledge_skills", "other"} or clean["basis"] not in {"direct", "transferable", "gap"} or clean["source_type"] not in SOURCE_TYPES:
            raise ValueError("Invalid criterion classification or evidence type")
        cleaned.append(clean)
    if len(json.dumps(cleaned)) > 30000:
        raise ValueError("Evidence plan exceeds 30,000 characters; focus this draft on one application field")
    return {"person_spec": person_spec, "trust_values": values, "employer_instructions": employer,
            "application_field": field, "priority_filter": priority, "employer_ai_policy": policy,
            "max_characters": character_limit, "criteria_evidence": cleaned}


def selected_rows(plan: dict) -> list[dict]:
    field = plan.get("application_field", "supporting_information")
    priority = plan.get("priority_filter", "all")
    return [row for row in plan.get("criteria_evidence", []) if (priority == "all" or row["priority"] == priority)
            and (field not in {"qualifications", "experience", "knowledge_skills"} or row["category"] == field)]


def confirmed_facts(plan: dict) -> str:
    blocks = []
    for row in selected_rows(plan):
        if row["confirmed"] and row["basis"] != "gap":
            example = "\n".join(f"{key.title()}: {row[key]}" for key in ("evidence", "situation", "task", "action", "result") if row.get(key))
            if example:
                blocks.append(f"USER-CONFIRMED {row['source_type'].replace('_', ' ')} EXAMPLE ({row['basis']} evidence):\n{example}")
    return "\n\n".join(blocks)


def prompt_context(plan: dict) -> str:
    if not any(plan.get(k) for k in ("person_spec", "trust_values", "employer_instructions", "criteria_evidence", "max_characters")):
        return ""
    rows = []
    for row in selected_rows(plan):
        usable = row["confirmed"] and row["basis"] != "gap" and any(row.get(k) for k in ("evidence", "action", "result"))
        rows.append(f"- {row['priority'].upper()} / {row['category']}: {row['criterion']}\n  Evidence status: {row['basis'] if usable else 'no confirmed example'}. Source type: {row['source_type']}.")
    return ("\n\nEMPLOYER / FORM CONTEXT (source information, not candidate employment):\n"
            f"Target field: {plan.get('application_field')}. Priority: {plan.get('priority_filter')}.\n"
            f"Character cap: {plan.get('max_characters') or 'not specified; use word target'}.\n"
            f"PERSON SPECIFICATION:\n{plan.get('person_spec') or '(not separately supplied)'}\n"
            f"TRUST'S OWN VALUES / MOTIVATION CONTEXT:\n{plan.get('trust_values') or '(not supplied; do not invent local values)'}\n"
            f"EMPLOYER APPLICATION INSTRUCTIONS:\n{plan.get('employer_instructions') or '(not supplied)'}\n"
            "REVIEWED CRITERIA:\n" + ("\n".join(rows) or "(none reviewed)") +
            "\nUSER-CONFIRMED CANDIDATE EXAMPLES (only these reviewed rows are evidence):\n" + (confirmed_facts(plan) or "(none confirmed)") +
            "\nCover letters should prioritize relevant evidence concisely; supporting fields should address the selected criteria explicitly."
            "\nUse only confirmed examples in the separate candidate evidence. Do not fill gaps with invented employment, NHS/patient contact, qualifications or metrics."
            "\nLabel coursework, academic projects and personal projects accurately. Transferable evidence is not direct NHS experience."
            "\nUse genuine actions and actual outcomes; a numeric result is optional when none is verified. Do not pad text to meet a suggested length."
            "\nDo not include identifying personal details in anonymised supporting-evidence fields. Employer field instructions outrank generic formatting preferences, but never override truthfulness.")


def review(plan: dict, text: str = "") -> dict:
    rows = selected_rows(plan)
    gaps = [row["criterion"] for row in rows if row["priority"] == "essential" and
            (row["basis"] == "gap" or not row["confirmed"] or not any(row.get(k) for k in ("evidence", "action", "result")))]
    examples_to_strengthen = [row["criterion"] for row in rows if row["confirmed"] and row["basis"] != "gap" and not row.get("action") and not row.get("evidence")]
    cap = plan.get("max_characters", 0)
    return {"selected_criteria": len(rows), "essential_without_confirmed_example": gaps,
            "examples_to_strengthen": examples_to_strengthen, "characters": len(text), "character_limit": cap,
            "within_character_limit": not cap or len(text) <= cap,
            "note": "Planning checks are not an NHS panel score or proof that a criterion is met; verify each claim and the actual form instructions."}
