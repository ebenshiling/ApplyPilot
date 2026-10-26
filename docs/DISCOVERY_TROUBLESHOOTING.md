# Discovery and AI-provider recovery

## “python-jobspy is required” when it is installed

The distribution can be installed while its Python import fails on a transitive dependency. In the diagnosed deployment, `python-jobspy==1.2.0` required `curl_cffi>=0.16.2`, which the old `--no-deps` setup omitted.

Current Docker/bootstrap setup installs JobSpy with normal dependency resolution and verifies `from jobspy import scrape_jobs` during provisioning. Discovery preflights once, so a missing dependency is reported before every board/query combination is attempted.

Direct smart sources (NHS Jobs, GOV.UK, HealthJobsUK, etc.) are independent of JobSpy. To run only those through the UI, use Pipeline → Advanced run controls → Skip Boards, select the desired direct sources and preview the effective scope.

## Gemini HTTP 402

HTTP 402 is a provider payment/access rejection, not a job-fit result. The diagnosed key was accepted by the read-only Gemini model catalog (HTTP 200), while `generateContent` returned 402. That confirms catalog authentication; it does not determine the exact billing restriction or establish available generation credit.

Check the billing/usage settings for the Google AI Studio/API project linked to that specific key. An OpenAI balance, a different Google project balance or a consumer subscription does not establish API access for this key/project/model.

Alternatively, explicitly choose another configured provider in **Settings → AI providers & task models**:

- **Job scoring and fit explanations:** select the desired provider and available model.
- **Discovery and description extraction:** likewise, if Gemini extraction is blocked.
- **Summaries:** update this too if it uses the blocked account and you want those tasks available.

Changing scoring/extraction does not require changing CV/statement/cover models. API-provider changes are user choices; the system does not switch to a different paid provider automatically.

After fixing access/settings, use **Pipeline → Problems & recovery → Retry eligible scoring failures**, or retry individual records. Completed descriptions and discovered jobs are retained.

## Reading the run outcome

- **Discovery partial:** one provider failed while others could still add jobs.
- **Filtered by title/salary:** configured preferences rejected the listing; this is not a transport/provider error.
- **Already existed:** URL deduplication avoided a new record.
- **Enrichment partial / page-text fallback:** AI extraction failed, but cleaned page text and any deterministic application link were retained. Inspect the description before relying on it.
- **Scoring unavailable / provider blocked:** no valid fit score was stored. Account-level failures pause further requests in that batch and retain deferred jobs.
- **Successfully scored:** only valid completed fit assessments count here. Failed requests are not described as “jobs scored”.
