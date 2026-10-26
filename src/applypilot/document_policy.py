"""Versioned country presentation, independent of shared professional history."""
from applypilot.country_context import normalize_country


def prompt(job,kind):
    code=normalize_country(job.get("country_code")) or "GB"
    common=("Factual authority: only currently verified canonical facts and explicitly human-reviewed, evidence-linked wording may become final. "
            "A source CV, supplemental note, citation ID, AI reviewer PASS, related tool or learnable skill is not verification. "
            "Preserve employer/project attribution, actual dates, numbers, overlapping/part-time period distinctions and incomplete/expired credential status. "
            "New phrasing remains a draft until the common final-byte gate accepts it. Do not invent fillers, project years or experience to reach a length target. ")
    if code=="NZ":
        return "\n\nNZ DOCUMENT POLICY nz-evidence-only-v1\n"+common+(
            "Use New Zealand English and the requested CV/cover/evidence format. A supporting statement is employer-requested evidence, not an assumed NHS form. "
            "Do not imply prior NZ employment, residence, citizenship or work rights from vacancy location. Use only explicitly approved NZ disclosures; absent information is unknown. "
            "Do not inherit UK visa, relocation, salary or sensitive answers. Accreditation/AEWV/job-check statements are employer assertions, not candidate eligibility or sponsorship confirmation. "
            "Keep genuine verified UK/NHS history when relevant; never transform it into NZ experience. Respect employer word/character limits and actual TXT/PDF/DOCX-original availability.")
    return "\n\nGB DOCUMENT POLICY gb-evidence-only-v1\n"+common+"Retain UK English and relevant, genuine UK/NHS experience and criteria guidance. Country presentation never changes shared factual history."
