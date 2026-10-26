# Complete application profile and responsive browser controls

## Saved application details

**Settings → Personal identity, contact & address** now includes title/salutation, legal and separate names, preferred name, suffix, birth date, nationality, country of birth, email, primary/mobile/alternative phones, three address lines, town/city, county, state/province/region, postcode, country/country code and professional links.

The profile also includes work authorization, sponsorship, visa/permit expiry, driving licence, transport, availability for work types/shifts, relocation/travel preferences, salary and experience. The **Optional equality-monitoring answers** section includes gender, pronouns, ethnicity, disability, reasonable adjustments/details, religion/belief, sexual orientation, marital status and veteran status. Free text/datalists allow your own accurate answer, including a preferred non-disclosure answer; blank answers are left unfilled.

Save the profile explicitly before starting a new agent session. Separate names initially use the existing legal name as an editable starting point; check them, especially for compound names. Editing separate names updates the legal-name input. Existing profile keys and verified facts are deep-merged and preserved. Legacy street-address and province/state values remain compatible.

The assisted browser fills clear matching fields locally from saved values, including native title/country/equality selects and clearly labelled radio groups. It preserves existing answers, avoids privacy/consent/declaration checkboxes and leaves unsupported or ambiguous options for takeover. Missing demographic answers are not guessed. Birth date, nationality and equality values are local-only in form matching rather than sent to the forms planner. Job-specific questions still use verified answers and reviewed documents.

## Faster manual interaction

The live view now uses an authenticated continuous JPEG stream rather than waiting 750 ms between serialized status/image requests. Capture targets **up to 12 frames per second**, sending changed frames and periodic keep-alives. Actual responsiveness depends on the employer site, machine/network and active browser load.

Typing and wheel input are coalesced into short ordered batches. Clicks, text, keys and scroll events retain their order; pending input is flushed before resume, review or other controls. Batches have bounded event/text sizes and remain manual-only. Final submission still uses the existing single-use, page-bound approval and browser submit guards.

Browser command, status, screenshot and stream routes now use workspace-owned in-memory sessions without initializing database schema on each keystroke. Manual DOM inspection is throttled separately from screen capture, and unchanged controls are not repeatedly recreated in the workspace UI. Streaming pauses when the view is hidden/closed, with polling recovery available if a stream connection fails.

This is a remote view of a server browser, so it is not identical to a local browser at 60 fps. The changes address the previous one-request-per-character overhead and roughly once-a-second viewing delay.
