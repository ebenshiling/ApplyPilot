# Global location searches and visual Insights

## Search country, state/province and city

Open **Pipeline → Searches & resume rules**. The one-location-per-line editor remains available for quick entries. Existing location labels, remote flags and advanced row metadata are preserved when a row is unchanged.

Set **Default search country** for places that do not specify their own country. Open **Country, state & city editor** to configure individual places:

- **City / entered place:** country plus city, optionally state/province, such as Toronto / Ontario / Canada or Austin / Texas / United States.
- **State / region:** country plus state/province, such as Bavaria / Germany.
- **Whole country:** country with no city/state restriction.
- **Remote:** country-restricted remote work, or **Worldwide** for global remote searches.

Country/territory, state and city inputs accept free text. The country suggestions describe the installed board integration's recognized markets; they are not an exhaustive list of permissible places. UK/GB/United Kingdom and US/USA/United States aliases are normalized for provider requests. Countries outside a board's market list remain valid search text for global boards and direct sources, while unsupported board combinations are shown in **Effective location routing**.

Each place is routed with its own country. A Canadian target no longer inherits the UK country parameter merely because the workspace defaults to the UK. Campaigns retain per-place geography and their role priorities/resume routing.

### Location filtering

Choose **Match selected countries, states and cities** to filter returned advert location text against the configured places. This mode ignores old UK-only accept/reject patterns rather than silently rejecting new Canadian/US/etc. targets. Unknown advert locations remain unknown and are retained for later assessment.

**Existing accept/reject pattern rules** retains the saved legacy filters. Preview reports a concrete conflict when a selected location does not pass those rules. The rules are not erased when switching modes.

### Discovery coverage

The preview shows effective location text, country, remote scope, eligible boards and skipped board/country combinations. Direct sources hosted on known country-specific domains are scoped to that country in structured geography mode; global/unspecified sources retain global scope. Workday searches are filtered against selected returned locations.

Direct search templates can use `{location_encoded}`, `{country_encoded}`, `{state_encoded}` and `{city_encoded}` alongside `{query_encoded}`. Different sources and boards have different market coverage; a configured country is not proof that a particular source can return results there.

## Insights: visual explanations and drill-downs

The Insights page now includes:

- Unique opportunity-state donut with active/archive/deleted-history segments.
- Independent preparation milestone bars and recorded recovery counts.
- Thirty-day activity rhythm, ending with the latest selected event.
- Hiring progress and employer-response rate, with an explicit submitted denominator.
- Source-volume bars with recorded response/interview outcomes.
- Fit-score bands, keeping unscored records separate.
- Employer outcome and closure-reason breakdowns.
- Exact saved location distribution.
- Median discovery-to-submission time only where both valid dates exist.
- Data coverage, unknown dates and unscored totals.
- Workspace-wide model token usage and estimated cost coverage, with separate pipeline and assistant/workspace ledgers.

Click a segment/bar or use the record-cohort/source controls to filter the matching records and timeline. **Show all records** clears the selection. Daily activity bars filter by event date. **What this tells you** explains the metric, denominator and interpretation for every chart. Keyboard controls, reduced-motion preferences and narrow/mobile layouts are supported.

Charts count unique opportunity URLs for opportunity/outcome metrics, not repeated timeline events. Preparation stages are independent recorded milestones, since documents can be optional and historical dates may be missing. An applied date prevents stale execution status from being counted as a current submission failure. Employer responses include acknowledgement, assessment, interview, offer and employer rejection; candidate withdrawal is not an employer response.

Token sums with missing requests are labelled partial. Cost is unknown until prices and complete token counts exist. AI usage is workspace-wide and is not attributed to a chart-selected opportunity cohort. Complete analysis downloads and the detailed ledger remain available, including retained deleted records and filtered export scope.
