# UI-driven professional workspace

Open `/workspace`. Profile, CVs, projects, writing and pipeline configuration can be maintained through the served UI.

## CV Studio

- Upload **multiple PDF, DOCX, TXT or MD files** (up to 20 per selection, 12 MB per file). Imports show an outcome for each file; one invalid file does not discard successful imports.
- Originals are retained. New imports use collision-safe variant identities and leave the existing default CV intact.
- Give an upload a target role to create a direct role-title mapping. Use **Map role** later to update that association.
- Edit sources, preview them, download text/original/PDF, duplicate or rename variants, choose a default, or archive and detach mappings.
- Every source edit retains revisions. Restore an older source through the library revision selector.
- **Summary & skills**, **Qualifications**, and **Experience** provide editable professional facts. Experience can be reordered, excluded or restricted to role families.
- Build editable CV text from the saved profile, then review and save it. Existing job-specific and submitted CV versions remain separate.

PDF imports require readable text; image-only or locked PDFs receive a per-file error. Imports do not silently infer qualifications or replace the profile. Literal contact/project suggestions are available for explicit review.

## Profile and AI settings

**Settings** contains native contact, work-authorization, availability, compensation, experience, summary, skills and verified-fact editors. Changes merge with existing profile fields. You can propose missing contact/summary/skill fields from a selected CV and review them before saving.

**AI preferences** shows status-only provider cards, expandable task models, a daily writing allowance and optional model-rate estimates. API keys are not displayed, and there are no credential fields on the main settings page. Select a provider's **Manage connection** to choose **Use server connection** or **Set a workspace connection**. Only the latter creates an empty password input for a new key; closing the dialog clears and removes it. Saved keys are encrypted in the workspace and never returned by the settings endpoint. Connection saves are independent of model/allowance/rate edits, and changes apply to subsequent requests without recreating the service. Server inheritance preserves configuration files while ignoring this provider's workspace overrides.

Model availability remains provider-dependent. Reasoning/thinking controls must be compatible with the selected model/provider. Settings and response metadata expose the effective task route; they are not quality rankings.

## Portfolio

Create and reorder project/case-study entries with titles, subtitles/dates, technologies, verified bullets, project/repository links, role applicability and inclusion controls.

Choose **General**, **Data & BI**, **IT support**, or **Application support** collections. Import literal project proposals from a selected CV for review. Attach PNG, JPEG, PDF or TXT evidence (8 MB per attachment). Saved, included evidence is reused for appropriate scoring and writing tasks.

## Cover and Statement Studios

For a click-by-click NHS workflow, supporting-evidence form differences, criteria mapping, international-student targeting and the fine-tuning decision, see [NHS application workflow](NHS_APPLICATION_WORKFLOW.md).

1. Select a saved application, or paste a role/employer/description.
2. Choose automatic routing, a named CV, the selected job's tailored CV, or custom source text.
3. Set word targets, tone, verified supplemental evidence and writing guidance. Statements also offer criteria-led, example-led and values-led structures.
4. Generate a draft, or write one manually. Identical generation requests can reuse cached drafts; “fresh alternative” requests a new generation.
5. Edit with live word count and document preview. Check validation, then save another version.
6. Download text/PDF, copy text, or explicitly **Use for application**.

Generation uses the configured writing model, shared verified facts, existing writing logic and validators. A workspace generation budget applies (20 requests/day by default; `studio_daily_budget` can configure 1–200). Failed attempts retain reservations. Manual editing and saved-draft retrieval do not require another model call.

Publication revalidates the document, rejects changed profile/CV inputs, and excludes submitted, archived, blocked or active applications. Draft files and prior approved versions are preserved. A draft does not become an application document merely because it was generated.

## Portable backup

Settings offers **Download workspace backup** with profile, source CVs, original uploads, portfolio evidence, generated documents and history. The database is captured through SQLite backup so committed WAL data is included. API credentials and connector encryption keys are excluded from the UI download; model settings remain portable.

CLI transfer commands remain compatible. Imported generated paths are rebased only to restored files inside the destination workspace.
