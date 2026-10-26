# Add and test a direct source

In **Pipeline → Sources & extraction previews**, choose a source name, type and public URL.

## Search template versus fixed listing

- **Search URL template** needs `{query_encoded}` (or `{query}`) in the site's search-results URL. Add `{location_encoded}` when the site supports location search.
- **Static listing page** uses a fixed careers/jobs page, with an optional location placeholder. Role/query placeholders require the search-template type.

For the screenshot's Indeed UK example, use:

```text
https://uk.indeed.com/jobs?q={query_encoded}&l={location_encoded}
```

The form offers **Use Indeed search template** when an Indeed homepage is entered with the search-template type. That button fills the search URL; **Save source** registers it in the workspace. Save and Test apply the same validation.

After saving, the source stays open for editing/testing. If current searches use an explicit direct-source selection, tick the saved source in the list and choose **Save source selection**. Existing role overrides and unsaved search edits are retained while saving/testing a source.

## Extraction preview

**Test extraction** is a bounded, read-only HTTP/JSON-LD and candidate-link check. It does not save configuration, insert jobs or invoke AI. It validates public URLs at every redirect.

Preview feedback stays next to the source form:

- **HTTP 401/403:** the site blocked the automated preview.
- **HTTP 429:** the site rate-limited the preview.
- **HTTP 404:** check the search/listing URL.
- **HTTP 5xx, timeout or connection failure:** retry when the source is available.
- **Browser verification page:** the returned page is a challenge, not extractable listings.
- **No structured jobs found:** inspect candidate links; this is not proof that the source contains no vacancies.

A failed network preview does not prevent saving a valid source. Indeed can return HTTP 403 to this HTTP-only preview, including with the correct search template. The dashboard reports that actual site response rather than advising you to change an AI provider.

Indeed is also a supported **Job boards** entry (`indeed`) under **Searches & resume rules**. Board discovery and direct-source previews are separate paths; a preview result is not a board-discovery result.

## Existing source settings

Use **Edit / test** to update an existing stable source identifier. Creating a new source with an existing name/identifier reports a duplicate instead of silently replacing it. Updates preserve unrelated catalog keys, other sources, advanced source metadata and declared base URLs. Invalid workspace YAML is reported before a write.
