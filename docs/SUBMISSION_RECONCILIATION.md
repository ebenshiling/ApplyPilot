# Reconcile an uncertain submission

**Submission uncertain** means a submit attempt was made but ApplyPilot did not verify an employer receipt. It is neither confirmed failure nor confirmed success. The retry lock prevents duplicate applications.

## Check the employer outcome

1. Open the employer portal in your own browser, sign in if needed, and check **My applications** for the exact job.
2. Look for **Submitted/Received**, a reference and a timestamp. Check employer confirmation email, including spam/junk.
3. If unclear, contact the employer/recruiter. Missing email alone does not prove that no application was received.

## Save the verified result

Open **View record → Reconcile submission**. The same control appears in the blocked Apply-with-agent packet view.

- **Confirmed submitted / employer received it:** select the evidence source, describe the receipt/portal result and add the reference. You may supply the actual submission date from the receipt. Confirm you checked the result. The application becomes submitted and its timeline/document snapshot are preserved. Without a receipt date, the current manual-recording time is used.
- **Confirmed not submitted / draft or validation failure:** use explicit portal/draft/validation or employer confirmation evidence, not merely missing email. Confirm you checked it. The job returns to **prepared**, so you may deliberately start a new reviewed attempt. No retry or submission is started by reconciliation itself.
- **Still unsure:** record what you checked or whom you contacted. Uncertainty and the retry lock remain; return after obtaining clearer evidence.

Only the current unresolved attempt can be reconciled. Reload a stale reconciliation dialog. Ordinary **Record failure** cannot clear uncertainty. **Mark applied** on an uncertain attempt requires verified evidence/reference; the dedicated dialog provides source/date tracking.

Historical browser sessions can still say uncertain because that was the automation result. The reconciliation event records the later verified outcome. Eligibility follows the current job status, and all previous timeline events remain.

## Attempt evidence

New attempts save a reviewed-attempt image and the latest available image when confirmation is missing. **View saved last-attempt image** appears if that artifact exists. Older attempts may have no image. Images are workspace-owned/authenticated and included in generated-file cleanup; an image alone does not automatically establish submission.

## Dashboard counts

The summary separates **Current jobs** (active, non-skipped opportunities), **Archived jobs** and **Deleted history**. Deletion/archiving reduces the appropriate current count. **Submitted · all time** and **Interviews/offers · all time** preserve historical outcomes after full records are deleted. Blocked/snoozed records are not active jobs. The API also exposes stored-record and all-found counts.
