# Using ApplyPilot for NHS applications

Guidance checked **5 October 2026**. Employer instructions and your actual application form take precedence over a generic template. This workflow supports applicants; it does not reproduce an NHS shortlisting panel or decide immigration eligibility.

## What official guidance establishes

The [NHSBSA applicant-support page](https://www.nhsbsa.nhs.uk/nhs-jobs-help-and-support/help-and-support-applicants) links the current [supporting-evidence guide](https://www.nhsbsa.nhs.uk/sites/default/files/2026-08/How%20to%20apply%20for%20a%20job%20-%20Add%20your%20supporting%20evidence%20in%20NHS%20Jobs%20user%20guide.pdf). Its examples distinguish:

- mandatory qualifications and experience criteria, and additional employer criteria such as knowledge/skills;
- separate **essential** and **desirable** evidence boxes;
- **up to 5,000 characters** in the illustrated evidence boxes, not a universal 1,500-word essay;
- no identifying personal information such as name/contact details in those supporting-evidence fields;
- explaining how you gained and used the relevant qualifications/experience.

Other employers/portals can use a single supporting-information statement or different questions/limits. [University Hospitals Birmingham's application page](https://www.uhb.nhs.uk/how-to-apply/) routes applicants to its own recruitment portal, illustrating why “the NHS system” is not one identical form everywhere.

[The NHS Constitution for England](https://www.gov.uk/government/publications/the-nhs-constitution-for-england/the-nhs-constitution-for-england#nhs-values) identifies working together for patients, respect/dignity, commitment to quality, compassion, improving lives and everyone counts. It also says organisations develop these values locally and applicants/staff should be honest. Use the actual trust's values rather than substitute another employer's slogan. These English constitutional values should not be presented as the identical local policy of every UK health board.

For non-clinical applicants, patient/service benefit can be explained through reliable systems, accurate reporting, communication, safe information handling and service improvements. [Leeds Teaching Hospitals' digital-careers page](https://www.leedsth.nhs.uk/careers/digital-careers/) describes information, hardware/software and digital foundations supporting care. That is context for motivation, not permission to claim direct patient-care or NHS employment experience.

## Pipeline: your first focused run

1. **Settings:** save accurate contact, experience, authorization/availability and verified skills. **CV Studio:** upload/edit the role variants. **Portfolio:** save factual project/work examples.
2. **Pipeline → Find & assess.** Enter one role per line, for example `Application Support Analyst` and `Data Analyst`. Blank overrides use the saved campaign/workspace queries.
3. For a focused NHS run, select `NHS Jobs` and relevant health/public-sector sources under **Sources & extraction previews**, then save the selection. Skip board/Workday providers for that run if they are not wanted.
4. Start with a small smart-target budget (for example 10), workers 1, and streaming off while learning. A budget of 0 means all configured work. Budgets limit search targets, not the total number of resulting jobs or all downstream AI calls.
5. **Preview run:** inspect current effective searches/targets, eligible stage items and resume decisions. Counts refresh when controls change. An old 900-target saved configuration is not the effective count for a two-role capped run.
6. **Run workflow**, then inspect **Jobs**. Check the actual advert, closing date, person specification, salary, work arrangements and sponsorship wording; the fit score is only an assessment against supplied evidence.
7. **Pick** suitable jobs. The “Picked to apply” metric must be nonzero before a picked-only preparation run can process those jobs.
8. **Prepare selected applications** creates required documents/PDFs for picked jobs. Use individual Studios for the careful NHS criteria review below. Submission is a separate action; generating a CV or statement does not submit an application.

## Statement Studio: criteria before prose

1. Select a saved job, or enter its title/employer/description. Choose a specific appropriate CV if automatic routing is ambiguous.
2. Obtain the **complete person specification**, including attached PDF/Word tables. A scraped advert may omit the attachment. Paste the specification in the NHS/employer planner.
3. Select the actual target field: full supporting information, qualifications, experience, knowledge/skills, or another field. If the form separates essential/desirable evidence, select the matching priority.
4. Enter the field's actual word and/or character limit. The 5,000-character value is a convenience for the documented NHS Jobs example, not a rule for every portal. A suggested minimum word target is not an employer requirement.
5. Paste local trust values, genuine reasons for applying and any employer instructions, including an AI-use rule/declaration where one exists. We did not establish a universal NHS AI-use policy. Follow the specific advert/form; “AI-generated content not permitted” disables generation but leaves manual drafting/planning available.
6. Extract criteria **for review**. Correct missed/misclassified items and add missing criteria, especially qualifications/registration/experience requirements.
7. Map each criterion to direct, transferable or missing evidence. Label its origin accurately: employment, academic project, personal project, training, volunteering or other evidence.
8. Add your real example. A useful compact pattern is **situation → task → your action → actual result**. Include measured figures only when you can substantiate them; a genuine nonnumeric outcome is also valid. This pattern is our writing aid, not a mandatory universal NHS format.
9. Confirm an example only when it is accurate and you can discuss it at interview. Unconfirmed/missing rows are not supplied as candidate evidence. **Check evidence plan** identifies planned essential criteria needing an example.
10. Generate a draft, read every claim, check words/characters and use **Check draft**. Formatting/factual checks are not an NHS panel score or a guarantee that all criteria are met. Then edit and **Save new version**.
11. Use the text in the correct employer boxes, or **Use for application** to associate a validated draft with the saved job. Existing submitted documents remain historical. Complete the rest of the application form as well.

### Example plan for an IT/data applicant

| Employer criterion | What to provide | What not to imply |
|---|---|---|
| SQL/reporting skills | Exact queries, data checks, reporting project and your actions | A coursework project was paid NHS employment |
| Communication | A real example of explaining an issue or finding, who the audience was and the outcome | Clinical communication with patients unless it happened |
| Incident resolution | The actual issue, investigation, fix/escalation and result | Experience with a named patient administration system you have not used |
| Qualifications | Exact award/course title, institution/issuer and completion status | Three individual Google courses constitute the entire professional certificate |
| Confidentiality/quality | Actual handling/checking practices or a clearly labelled academic example | NHS information-governance training or patient-data access you never had |

An unmet mandatory qualification/registration is a targeting issue, not something stronger prose or a fine-tuned model can supply. Check whether an equivalence/alternative is explicitly accepted in that person specification.

## Cover Studio

Use the same complete source inputs, reviewed examples, employer instructions and limits. Keep a cover letter concise and choose the most relevant evidence; a short letter cannot give a detailed response to dozens of criteria. Its usual purpose is motivation and a focused introduction. Where the employer requests supporting-information/criteria boxes, complete those too. Do not assume uploading a cover letter substitutes for mandatory evidence sections.

## What the AI actually uses

Both Studios use the selected CV plus role-appropriate saved qualifications, experience, projects and skills; the target job; optional full person specification; your additional factual evidence and confirmed planner examples; local values/instructions; style, structure and limits; and the configured task model. The current deployment inherited OpenAI `gpt-6-luna` with low reasoning for statement/cover, but the effective model is shown in Settings and saved response metadata.

The writer is instructed not to invent qualifications, registrations, employers, dates, metrics, tools or direct NHS/clinical experience. Training and projects are kept distinct from employment. It generates a draft and runs deterministic quality/format checks. Those checks include targeted unsupported-claim patterns and cannot independently verify every factual sentence or inspect every employer's scoring practice. Your review remains important.

## Should we train/fine-tune a model?

Start with the evidence workflow and evaluations, not fine-tuning. Fine-tuning mainly teaches recurring writing patterns; it does not add your missing qualifications or provide current person specifications and visa rules. Employer-specific requirements and private applicant facts still need to be supplied per application.

A sensible later programme:

1. Build consented, anonymised examples containing the advert/person specification, verified applicant evidence, accepted final text and expert review notes.
2. Define a rubric: factual accuracy, essential-criterion evidence, appropriate transferable claims, employer values/motivation, field limits, clarity and useful specificity.
3. Compare the current prompt/model with alternative prompts/models on held-out cases. Include negative cases: no NHS experience, incomplete qualifications, missing criteria, character-limited fields and conflicting instructions.
4. Measure expert-rated errors/coverage and cost. Recorded interviews can inform outcomes, but cannot isolate writing quality from competition, eligibility and other factors.
5. Consider fine-tuning only if a repeatable style/structure weakness remains and a quality dataset supports it. Preserve independent validation, source provenance and current employer context.

## International-student targeting

Keep work eligibility separate from statement merit. The [Student visa overview](https://www.gov.uk/student-visa) says permitted work depends on the course and term-time circumstances; check your own eVisa conditions and university guidance rather than assuming unrestricted permanent/full-time work.

For a [Student → Skilled Worker switch](https://www.gov.uk/skilled-worker-visa/switch-to-this-visa), GOV.UK specifies additional course/start-date conditions (or the applicable PhD condition). The [Skilled Worker job rules](https://www.gov.uk/skilled-worker-visa/your-job) require an eligible occupation, approved sponsor and applicable salary/going rate, with route-specific exceptions. Ask the recruiter to confirm sponsorship for the actual vacancy, occupation code and start date.

An NHS employer being a licensed sponsor does not establish sponsorship for every vacancy. [Health and Care Worker eligibility](https://www.gov.uk/health-care-worker-visa/your-job) is occupation-specific; an NHS IT/data role is not automatically on that route simply because it is inside the NHS. Use the correct route and the actual employer confirmation. Put accurate authorization answers in the relevant application fields; do not make the supporting statement mainly a plea for sponsorship.
