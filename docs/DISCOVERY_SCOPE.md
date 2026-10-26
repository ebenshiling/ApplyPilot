# Review a bounded discovery run

The native **Pipeline → Find & assess** workflow previews the actual roles,
locations, provider groups and request window before starting. Editing the visible
controls updates that preview; unsaved edits are included in this run. **Save search
configuration** retains those settings for later.

## Defaults and controls

- **Maximum new jobs to save: 50**, shared across all enabled providers.
- **Results inspected per board/portal search: 10**. Workday no longer paginates
  through up to 500 jobs per portal for a bounded dashboard search.
- **Matching titles enabled**. A returned title must contain one of the selected
  role phrases; excluded title phrases are also checked. Provider keyword results
  alone do not establish a title match.
- **Only new jobs from this search** are enriched/scored by default. Previously
  saved jobs do not silently enter the run, even if they are still unscored.
- Job boards are enabled initially; direct websites and Workday require their
  provider switches to be enabled. An empty source selection does not mean all.
- Source search budgets are positive, bounded counts; zero/unlimited is rejected
  in this dashboard mode. The existing legacy CLI path remains available.

Each provider can return fewer matches or fail. The preview does not predict exact
live vacancies. **These searches will run** shows the frozen board/query/location,
Workday portal/query and direct-page window; execution can stop early at the total
new-job cap. A whole-country location such as **UK** broadens the location scope.
The preview calls this out, shows the active location filtering mode, and identifies
roles outside the current bounded window.

## Start a run

1. Enter only the wanted job titles, one per line.
2. Review locations/default country and choose the desired location filtering.
3. Select provider groups and adjust the new-job/result/search limits.
4. Read the live preview, then click **Review & run**.
5. Check the exact plan and click **Start this run**.

Starting validates a fingerprint of that plan again, including current settings
and selected source catalogs. A changed or unreviewed plan is rejected. Cached old
native pages cannot start an unscoped discovery/scoring run; refresh the page.

**Matching saved jobs** is an explicit bounded processing mode for custom/existing
work. Preparation of picked applications preserves the explicit pick selection,
rather than replacing it with the current discovery titles. Recent-run actions
also go through scope review instead of blindly replaying an old large backlog.

## Storage and processing

Collection uses a context-local shared budget and serialized storage batches.
Only actual inserted jobs consume quota; duplicate or rejected rows do not.
Reserved membership keys in the existing `cc_campaign_jobs` table track this
run's new jobs and processing selection. Pending queues, scoring repairs and
invalidation are restricted to that selection. Existing job rows are not deleted
or globally reclassified when settings change.

Stop controls stop the current run; already committed rows remain available for
review/bulk deletion. Retrying or restarting is an explicit user action.
