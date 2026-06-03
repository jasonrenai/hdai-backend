"""Cron: deadline approaching email for wishlisted, not-applied rows (metadata deadline window)."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from app.email.deadline_approaching_notification import (
    build_deadline_approaching_template_model,
    is_deadline_in_notification_window,
    parse_metadata_deadline_date,
)
from app.email.enums import EmailEventType
from app.email.helpers import speaker_profile_notification_email
from app.models.Opportunity import OpportunityModel
from app.models.OpportunityActivity import OpportunityActivityModel
from app.models.OpportunityEmailStatus import OpportunityEmailStatusModel
from app.models.SpeakerProfile import SpeakerProfileModel

logger = logging.getLogger(__name__)


class DeadlineApproachingCronService:
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
        cooldown = int(os.getenv("DEADLINE_APPROACHING_COOLDOWN_MINUTES", "1380"))
        batch = int(os.getenv("DEADLINE_APPROACHING_BATCH_SIZE", "100"))

        rows = await self.activity_model.find_wishlist_for_deadline_approaching(
            cooldown_minutes=cooldown,
            limit=batch,
        )
        sent = 0
        skipped = 0
        errors = 0
        skip_reasons: dict[str, int] = {
            "missing_ids": 0,
            "invalid_object_id": 0,
            "archived": 0,
            "already_sent": 0,
            "opportunity_not_found": 0,
            "no_metadata_deadline": 0,
            "deadline_too_far_in_future": 0,
            "deadline_passed": 0,
            "speaker_profile_not_found": 0,
            "no_speaker_email": 0,
            "postmark_send_false": 0,
        }

        from app.dependencies import get_email_service

        email_service = get_email_service()

        for row in rows:
            speaker_id = (row.get("speaker_id") or "").strip()
            opportunity_id = (row.get("opportunityId") or "").strip()
            if not speaker_id or not opportunity_id:
                skipped += 1
                skip_reasons["missing_ids"] += 1
                continue
            if not self.activity_model.is_valid_object_id(speaker_id) or not self.activity_model.is_valid_object_id(
                opportunity_id
            ):
                skipped += 1
                skip_reasons["invalid_object_id"] += 1
                continue
            if bool(row.get("isArchived")):
                skipped += 1
                skip_reasons["archived"] += 1
                continue

            try:
                if await self.opportunity_email_status_model.is_deadline_sent(
                    speaker_id, opportunity_id
                ):
                    skipped += 1
                    skip_reasons["already_sent"] += 1
                    continue

                opp = await self.opportunity_model.get_by_id(opportunity_id)
                if not opp:
                    skipped += 1
                    skip_reasons["opportunity_not_found"] += 1
                    continue
                today = datetime.utcnow().date()
                if not is_deadline_in_notification_window(opp):
                    skipped += 1
                    d = parse_metadata_deadline_date(opp)
                    if d is None:
                        skip_reasons["no_metadata_deadline"] += 1
                    elif today > d:
                        skip_reasons["deadline_passed"] += 1
                    else:
                        skip_reasons["deadline_too_far_in_future"] += 1
                    logger.debug(
                        "Deadline approaching skip (outside notify window): opportunityId=%s speaker_id=%s",
                        opportunity_id,
                        speaker_id,
                    )
                    continue

                profile = await self.speaker_profile_model.get_profile(speaker_id)
                if not profile:
                    skipped += 1
                    skip_reasons["speaker_profile_not_found"] += 1
                    continue

                to_email = speaker_profile_notification_email(profile)
                if not to_email:
                    skipped += 1
                    skip_reasons["no_speaker_email"] += 1
                    continue

                if opp.get("_id") is not None:
                    opp = {**opp, "_id": str(opp["_id"])}

                template_model = build_deadline_approaching_template_model(
                    profile=profile,
                    opportunity=opp,
                )
                ok = email_service.send_event_email(
                    event_type=EmailEventType.ALERT_DEADLINE_APPROACHING,
                    to_email=to_email,
                    template_model=template_model,
                )
                if ok:
                    await self.opportunity_email_status_model.mark_deadline_sent(
                        speaker_id, opportunity_id
                    )
                    await self.activity_model.mark_last_deadline_approaching_sent(
                        speaker_id, opportunity_id
                    )
                    sent += 1
                else:
                    skipped += 1
                    skip_reasons["postmark_send_false"] += 1
            except Exception:
                logger.exception(
                    "Deadline approaching email failed speaker_id=%s opportunityId=%s",
                    speaker_id,
                    opportunity_id,
                )
                errors += 1

        return {
            "candidates": len(rows),
            "sent": sent,
            "skipped": skipped,
            "errors": errors,
            "skip_reasons": skip_reasons,
        }


def run_deadline_approaching_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler (Motor must run on the FastAPI event loop)."""

    async def _run() -> None:
        summary = await DeadlineApproachingCronService().run_once()
        if summary["sent"] or summary["errors"] or summary["candidates"]:
            logger.info("Deadline approaching cron: %s", summary)

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=300)
    except Exception:
        logger.exception("Deadline approaching cron top-level failure")
