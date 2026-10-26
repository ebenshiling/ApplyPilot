# ApplyPilot — your next chapter, organized

## Production brief

**Status:** produced and verified. The finished film is `brag.mp4`; open `index.html` for a local player and download button.

- **What it is:** ApplyPilot is an application workspace that helps job seekers discover opportunities, prepare evidence-backed documents, apply with a visible browser assistant, and track what happens next.
- **Audience:** job seekers managing multiple roles, CV variants, employer portals, and hiring conversations.
- **Angle:** one opportunity, followed from search configuration through reviewed application documents to follow-up and measured outcomes.
- **Hook:** “Find the role. Prepare the evidence. Own the next step.” Reveal the real Jobs workspace immediately.
- **Three highlights:** focused discovery and decisions; verified professional evidence and versioned documents; visible assisted application and durable outcome tracking.
- **Strongest demonstrable claim:** role-specific preparation and an application timeline live in the same workspace. Show actual actions and their results.
- **Visual hook:** an elegant moving pointer joins the sidebar, a scored opportunity, its document preview, and its application timeline.
- **Tone:** polished, confident, warm, editorial. Calm precision, readable holds, purposeful zooms.
- **Punchline:** “Your next chapter, organized.” Close on the real ApplyPilot identity and `/workspace`.
- **Share caption:** “From finding the role to preparing the evidence and tracking the response: a complete tour of ApplyPilot’s application workspace.”

## Format and visual identity

- **Master:** 1920 × 1080 landscape, 30 fps, H.264 MP4, AAC stereo, browser capture at native resolution with no upscaling.
- **Target duration:** 240 seconds (4 minutes). The detailed feature walkthrough requested by the user takes precedence over the skill’s default short launch format.
- **Source:** actual running native workspace components and assets from `src/applypilot/web/`.
- **Palette:** paper `#f4f2eb`, panel `#fffef9`, ink `#22372f`, accent `#1b6552`, rail `#20372f`, brand mark `#d8e2a2`.
- **Typography:** the app’s DM Sans and Libre Caslon Display. Wait for fonts before capture.
- **Motion:** smooth pointer travel, a restrained click halo, deliberate scrolling, close-up crops for small fields. Keep the sidebar visible when establishing a section.
- **Transitions:** direct editorial cuts or dip through the paper background; avoid busy UI double-exposure crossfades.
- **Captions:** short step labels in a reserved lower-third area; do not cover fields or controls. Hold readable text approximately 0.3 seconds per word after it settles.
- **Audio direction:** a cohesive understated music bed with soft clicks in the same acoustic space. Narration should explain the click → action → result sequence; duck music beneath speech.

## Storyboard — 240 seconds total

| Time | Duration | Scene and real mouse navigation | Viewer takeaway |
|---|---:|---|---|
| 00:00–00:06 | 6s | Brand hook over the settled Jobs view; pointer moves toward a scored opportunity. | This is an application workspace for your job search. |
| 00:06–00:16 | 10s | Introduce the native sidebar, collapse and expand it, briefly switch theme and return to the light identity. | Learn the workspace layout and personal viewing controls. |
| 00:16–00:34 | 18s | Settings → professional profile: inspect name/contact/address, work authorization, availability, summary, skills, verified facts and reusable screening answers. Save one demo-only edit. | Saved professional evidence supports preparation and form filling. |
| 00:34–00:54 | 20s | CV Studio: import demo CV variants, open an editable source, show role assignment and revision history; navigate qualifications and experience, then a document preview/download. | Maintain multiple CVs, verified qualifications and versioned job-specific documents. |
| 00:54–01:06 | 12s | Portfolio: open a project, inspect achievements, links and an attachment, then save a small demo update. | Reuse evidence from real projects in future applications. |
| 01:06–01:32 | 26s | Pipeline: select a campaign; open Searches & resume rules; show role priority/CV mapping and country/state/city/remote locations. Inspect board routing, direct-source selection and extraction-preview feedback. Preview a bounded Find & assess workflow. | Configure discovery and inspect the effective work before running it. |
| 01:32–01:48 | 16s | Jobs: search/filter, open a record, read fit and eligibility evidence, inspect duplicates/listing checks, use the fit assistant, and Pick the demo job. Briefly show bulk selection and archive/block options. | Decide which opportunities deserve preparation. |
| 01:48–02:10 | 22s | Cover Studio and Statement Studio: choose the demo job, review criteria/evidence in the NHS planner, inspect word/character limits, edit a saved draft, validate it, and open versioned preview/PDF links. | Documents stay editable, evidence-backed and reviewable. |
| 02:10–02:24 | 14s | Pipeline → Prepare selected applications; inspect required CV/cover/statement/PDF packet readiness, stage progress and recent runs; show a scoped retry for a prepared demo failure. | Preparation is distinct from submission, and failures have targeted recovery. |
| 02:24–02:50 | 26s | Job record → Apply with agent: review documents, open a controlled demo employer form, show visible browser feedback and real pointer/keyboard navigation; select a question, request an evidence-cited answer, demonstrate takeover and pause/resume/cancel controls, then the separate final-submit checkpoint. | Watch and control assisted application; submission requires its own review. |
| 02:50–03:04 | 14s | Applications: open a recorded demo submission, inspect confirmation evidence, timeline and versioned documents; add a hiring update, reminder/calendar export and show scheduling/reconciliation controls. | Technical execution and hiring outcomes are tracked separately. |
| 03:04–03:18 | 14s | Evidence inbox: show Gmail/Outlook connection options, import a synthetic employer message, inspect category/match suggestions and source evidence, then review the hiring update. | Turn employer messages into reviewed next steps. |
| 03:18–03:38 | 20s | Insights: hover and click real chart drill-downs; show cohorts, responses/outcomes and provider usage, open the opportunity ledger, record demo activity and export CSV/JSON/complete analysis. | Understand progress from recorded evidence and export the underlying data. |
| 03:38–03:47 | 9s | History & storage: open a retained timeline, show closure reasons, generated-file storage, cleanup retry and compaction controls. | Closing opportunities preserves their story and supports storage cleanup. |
| 03:47–03:56 | 9s | Settings: inspect private connection cards, per-task AI routing/budgets/prices, optional Decisions observer, backup download and admin-only account/audit controls. | Configure assistance and keep workspace access and portability manageable. |
| 03:56–04:00 | 4s | Return to the strongest settled workspace shot and brand outro. | Your next chapter, organized. |

## Capture preparation

1. Use a dedicated demonstration workspace with clearly fictional candidate and employer records, representative saved document versions, messages, history, and measured demo outcomes.
2. Exercise actual UI actions and API responses. Use a controlled employer-form fixture for browser interaction so the walkthrough can repeat reliably. Label the fixture as a demonstration.
3. Use saved demo generation results for long-running AI scenes; disclose any time compression. Every visible result must correspond to real supported functionality.
4. Capture at 1920 × 1080, wait for fonts/images and page-specific readiness, then move the visible pointer to the actual control before clicking. Capture result states rather than spinner holds.
5. Keep a shot manifest with section, selected record, action, resulting state, raw clip path, and duration under `work/`.
6. Verify stills from every scene and mid-transition for legibility, clipping, cursor position and caption overlap before rendering the master.

## Delivered files

- `brag.mp4`: the detailed 1080p commercial walkthrough.
- `brag.jpg`: strongest settled poster frame, also replacing frame zero of the MP4.
- `share-copy.txt`: ready-to-post product-specific copy.
- `work/`: capture scripts, demo fixtures, raw clips, scene manifest, audio and render intermediates.
- `index.html`: local playback page with an MP4 download link.
- `brag.srt` and `brag.vtt`: optional chapter-guide subtitles.

## Tool readiness

Production used Playwright/Chromium with the real native workspace in a separate demonstration server, the imageio-ffmpeg binary, Windows system speech narration and an original synthesized music bed. The 240-second H.264/AAC file was fully decoded without errors and verified in Chromium at 1920 × 1080 and 30 fps. Scene and transition stills were reviewed. Production records were not used for capture.
