# Close, delete and retain application history

Open a workspace application record and choose **Close / delete record**.

## Record why the opportunity ended

Choose a reason and add a note:

- **Advert closed:** the employer is no longer accepting applications. Availability-check results are shown in the dialog. A possible-closure detection still needs your judgement.
- **Requirements not met:** essential evidence or eligibility is missing. This is an opportunity decision, not an employer rejection.
- **CV / document preparation failed:** the pipeline could not produce an approved document. The existing validation failure and requirement-gap evidence are available in the dialog and retained in the closure timeline event.
- **Rejected by employer** or **Application withdrawn:** hiring outcomes that require an application already recorded as submitted.
- **No longer pursuing:** a deliberate decision to end work on the opportunity.

Closing a record does not invent a submission or rejection. Active application/browser/scheduled workers and uncertain submissions must be stopped/reconciled first.

## Close and archive

This removes the opportunity from active work while retaining its full record and generated files. Find it with the **Archived** filter in Jobs. **Restore** reopens it and records that action in the timeline. Queued application schedules are cancelled when the opportunity closes.

Archiving is reversible and does not reclaim generated-file space.

## Delete job & generated files

The dialog previews the exclusively owned files, estimated recoverable file bytes and shared files that will be retained. This action:

1. Saves a compact record containing the original job number, URL, title, employer/source, outcome, dates, reference, confirmation text and a bounded note.
2. Preserves the activity timeline, including the reason, deletion and cleanup results.
3. Removes the full job row, job-specific document contexts and diagnostic records.
4. Removes exclusively owned generated CV/letter/statement versions, reports and browser packet/confirmation files listed in the preview.

The original CV library, profile, portfolio evidence, verified answers and inbox messages are preserved. Files referenced by another job, a source CV or another document remain. Cleanup is limited to workspace-owned generated directories; symlinks and paths outside the workspace are not followed for deletion. Unknown/unattributed files are not swept broadly.

Deleting files is irreversible. The compact History record cannot restore the removed documents. A changed cleanup preview must be reloaded before deletion.

Deleted URLs are remembered so normal discovery does not immediately re-import them. New jobs receive numbers above retained original job numbers, avoiding reuse of deleted job identities. Submitted/rejected history continues to contribute to workspace hiring metrics.

After closing or deleting a record, the interface returns to **Jobs**. It does not
automatically navigate to History & storage. Your current Jobs search/filter is retained.

## Delete multiple jobs

1. Open **Jobs**. Choose a search/filter if you want to work on a subset. Choose
   **All** to include archived records in the displayed list.
2. Tick individual job cards, or use **Select all shown**. It selects only records
   matching the current search/filter. The selected count shows any selections
   outside the current filter; **Clear selection** clears the whole selection.
3. Click **Delete selected**.
4. Review the count/record list, choose a reason and optional note, then confirm
   **Delete N jobs & generated files** once.

The dialog shows progress. **Stop remaining** lets the current operation finish and
leaves the rest alone. Each selected record uses the same fresh cleanup preview,
identity/country validation, retirement transaction and file cleanup as individual
deletion. No automatic retry is performed.

When the batch finishes, Jobs shows how many deletions were confirmed, bytes removed,
records not deleted/unconfirmed and files needing cleanup retry. Remaining existing
records stay selected. A partial failure does not undo earlier completed deletions.
Active/uncertain records are retained and reported, and source CVs and files still
referenced by other records remain protected. The controls also appear on NZ saved
vacancies without selecting or deleting UK/legacy records.

## History & storage

Use the new **History & storage** navigation item to search deleted records and open **View timeline**. This is a read-only compact record rather than a full application workspace.

Locked or unavailable files are recorded as pending after the history record is saved. Close their viewers and use **Retry files**. Ownership is checked again during retry, so newly shared files are retained. The displayed removed bytes count successful file removals, not promised cleanup.

Deleting large database records makes their SQLite pages available for reuse; the database file does not necessarily shrink immediately. **Compact database** returns reusable space to disk while retaining history. It requires idle preparation/application work and can report a busy database rather than interrupting work. Generated-file usage and database/WAL file size are reported separately.

The cleanup flow pauses while a preparation pipeline is active. Dashboard runs proven ended in run history are reconciled conservatively as interrupted if their pipeline metadata was left running. Unknown CLI work remains guarded until its lifecycle is resolved.

These controls apply to the new `/workspace` lifecycle workflow. Existing Classic-dashboard deletion controls retain their separate legacy behaviour.
