# Opportunity Email Delivery Plan

## Goal

Prevent duplicate email sends and block sends for archived opportunities, while keeping implementation simple and extensible.

Scope covers:
- Matched opportunities email
- Submission reminder email
- Deadline-approaching reminder email

No behavioral changes outside these flows.

## Current Observations

- `Find Opportunities` can be triggered multiple times and may send repeated matched-opportunity emails.
- Matching recomputes and rewrites `matchedOpportunities`; same opportunity IDs can appear again.
- Reminder emails run via cron and currently rely on activity/cooldown windows, not a unified per-type send flag store.

## Proposed Simple Design

Use one lightweight MongoDB collection with boolean flags per speaker+opportunity.

Collection: `opportunity_email_status`

Document shape:
- `speaker_id: string`
- `opportunity_id: string`
- `matched_email_sent: boolean` (default `false`)
- `submission_email_sent: boolean` (default `false`)
- `deadline_email_sent: boolean` (default `false`)
- `createdAt: datetime`
- `updatedAt: datetime`

Notes:
- Keep this model intentionally simple.
- Add more boolean flags in future as new one-time email types are introduced.

## Archived Rule (Common Across All 3 Email Types)

Before any email send attempt, check if the opportunity is archived for that speaker.

Rule:
- If archived, do not send.

Source of truth:
- `OpportunityActivity.isArchived` (speaker-specific archived flag)

Implementation suggestion:
- Create one reusable helper used by all 3 flows:
  - `is_email_blocked_for_archived(speaker_id, opportunity_id) -> bool`

## Flow-Level Implementation Details

### 1) Matched Opportunities Email (Find Opportunities flow)

Where:
- Matching completes in `OpportunityService.run_matching_and_save(...)`
- Email sent via `MatchedOpportunitiesEmailService.send_matched_opportunities_email(...)`

Plan:
1. For each matched opportunity, apply archive check.
2. Remove archived opportunities from email payload.
3. For remaining opportunities, check `opportunity_email_status`.
4. Include only entries where `matched_email_sent == false`.
5. Send one email with list of eligible opportunities.
6. On successful send, mark `matched_email_sent=true` for each included opportunity.

Result:
- Still one batched email, but only for unsent+non-archived opportunities.

### 2) Submission Reminder Cron

Where:
- `SubmissionReminderCronService.run_once(...)`

Plan per candidate row:
1. Archive check first; if archived, skip.
2. Existing eligibility checks (wishlist/pending/not-applied/cooldown) continue as-is.
3. Check `submission_email_sent`; if true, skip.
4. Send email.
5. On success, set `submission_email_sent=true`.

### 3) Deadline-Approaching Reminder Cron

Where:
- `DeadlineApproachingCronService.run_once(...)`

Plan per candidate row:
1. Archive check first; if archived, skip.
2. Existing deadline-window validation continues as-is.
3. Check `deadline_email_sent`; if true, skip.
4. Send email.
5. On success, set `deadline_email_sent=true`.

## Service/Model Additions

Add a small model/service around `opportunity_email_status` with methods:
- `get_status(speaker_id, opportunity_id)`
- `mark_matched_sent(speaker_id, opportunity_id)`
- `mark_submission_sent(speaker_id, opportunity_id)`
- `mark_deadline_sent(speaker_id, opportunity_id)`
- optional: `bulk_get_status_map(speaker_id, opportunity_ids)` for matched batch optimization

Behavior:
- Upsert on mark methods.
- Set `createdAt` on insert, always update `updatedAt`.

## Idempotency and Concurrency (Practical Guidance)

For now, keep app logic simple:
- Read flag -> decide -> send -> mark sent.

If duplicate sends are still observed in production due to near-simultaneous requests:
- Add stricter atomic guard (e.g., unique+claim strategy) in next iteration.

## Rollout Plan

1. Add collection model + helper methods. (Done for matched flow)
2. Add shared archive-check helper. (Done for matched flow)
3. Integrate with matched-opportunities email flow. (Done)
4. Integrate with submission reminder cron flow. (Done)
5. Integrate with deadline-approaching cron flow. (Done)
6. Add logs for skip reasons:
   - archived
   - already_sent
   - existing flow-specific reasons

## Implementation Status

Completed:
- Added `OpportunityEmailStatusModel` (`opportunity_email_status` collection access).
- Matched opportunities email: archive filter via `OpportunityActivity.isArchived`, dedupe via `matched_email_sent`, mark sent after success.
- Submission reminder cron: skip if `OpportunityActivity.isArchived`, skip if `submission_email_sent`, mark `submission_email_sent=true` after success.
- Deadline-approaching cron: skip if `OpportunityActivity.isArchived`, skip if `deadline_email_sent`, mark `deadline_email_sent=true` after success.

## Acceptance Criteria

- Repeated `Find Opportunities` clicks do not resend the same matched opportunity email items already marked as sent.
- Submission reminder does not send when archived and sends only once per speaker+opportunity.
- Deadline reminder does not send when archived and sends only once per speaker+opportunity.
- Matched email remains a single email containing a list of eligible opportunities.

## Future Extension (Optional)

When needed, extend by adding booleans for new one-time email types without redesigning schema.
