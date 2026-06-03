"""Cron: submission reminder emails for wishlisted, not-yet-applied opportunities."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.email.enums import EmailEventType
from app.email.helpers import speaker_profile_notification_email
from app.email.submission_reminder_notification import build_submission_reminder_template_model
from app.models.Opportunity import OpportunityModel
from app.models.OpportunityActivity import OpportunityActivityModel
from app.models.OpportunityEmailStatus import OpportunityEmailStatusModel
from app.models.SpeakerProfile import SpeakerProfileModel

logger = logging.getLogger(__name__)


class SubmissionReminderCronService:
    def __init__(
        self,
        activity_model: OpportunityActivityModel | None = None,
        opportunity_model: OpportunityModel | None = None,
        speaker_profile_model: SpeakerProfileModel | None = None,
        opportunity_email_status_model: OpportunityEmailStatusModel | None = None,
    ):
        self.activity_model = activity_model or OpportunityActivityModel()
        self.opportunity_model = opportunity_model or OpportunityModel()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.opportunity_email_status_model = (
            opportunity_email_status_model or OpportunityEmailStatusModel()
        )

    async def run_once(self) -> dict[str, Any]:
        cooldown = int(os.getenv("SUBMISSION_REMINDER_COOLDOWN_MINUTES", "1440"))
        batch = int(os.getenv("SUBMISSION_REMINDER_BATCH_SIZE", "50"))

        rows = await self.activity_model.find_wishlist_pending_submission(
            cooldown_minutes=cooldown,
            limit=batch,
        )
        sent = 0
        skipped = 0
        errors = 0

        from app.dependencies import get_email_service

        email_service = get_email_service()

        for row in rows:
            speaker_id = (row.get("speaker_id") or "").strip()
            opportunity_id = (row.get("opportunityId") or "").strip()
            if not speaker_id or not opportunity_id:
                skipped += 1
                continue
            if not self.activity_model.is_valid_object_id(speaker_id) or not self.activity_model.is_valid_object_id(
                opportunity_id
            ):
                skipped += 1
                continue
            if bool(row.get("isArchived")):
                skipped += 1
                continue

            try:
                if await self.opportunity_email_status_model.is_submission_sent(
                    speaker_id, opportunity_id
                ):
                    skipped += 1
                    continue

                opp = await self.opportunity_model.get_by_id(opportunity_id)
                if not opp:
                    skipped += 1
                    continue
                profile = await self.speaker_profile_model.get_profile(speaker_id)
                if not profile:
                    skipped += 1
                    continue

                to_email = speaker_profile_notification_email(profile)
                if not to_email:
                    skipped += 1
                    continue

                if opp.get("_id") is not None:
                    opp = {**opp, "_id": str(opp["_id"])}

                template_model = build_submission_reminder_template_model(
                    profile=profile,
                    opportunity=opp,
                )
                ok = email_service.send_event_email(
                    event_type=EmailEventType.ALERT_SUBMISSION_REMINDER,
                    to_email=to_email,
                    template_model=template_model,
                )
                if ok:
                    await self.opportunity_email_status_model.mark_submission_sent(
                        speaker_id, opportunity_id
                    )
                    await self.activity_model.mark_last_submission_reminder_sent(
                        speaker_id, opportunity_id
                    )
                    sent += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "Submission reminder failed speaker_id=%s opportunityId=%s",
                    speaker_id,
                    opportunity_id,
                )
                errors += 1

        return {
            "candidates": len(rows),
            "sent": sent,
            "skipped": skipped,
            "errors": errors,
        }


def run_submission_reminder_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler (Motor must run on the FastAPI event loop)."""

    async def _run() -> None:
        summary = await SubmissionReminderCronService().run_once()
        if summary["sent"] or summary["errors"] or summary["candidates"]:
            logger.info("Submission reminder cron: %s", summary)

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=300)
    except Exception:
        logger.exception("Submission reminder cron top-level failure")
