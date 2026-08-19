"""Cron: one digest email per speaker of liked opportunities whose deadlines are near."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.email.deadline_approaching_notification import (
    build_deadline_approaching_template_model,
    parse_metadata_deadline_date,
)
from app.email.enums import EmailEventType
from app.email.helpers import speaker_profile_notification_email
from app.email.notification_delivery import is_deadline_in_lead_window
from app.models.Opportunity import OpportunityModel
from app.models.OpportunityActivity import OpportunityActivityModel
from app.models.OpportunityEmailStatus import OpportunityEmailStatusModel
from app.models.SpeakerProfile import SpeakerProfileModel
from app.services.NotificationDeliveryService import NotificationDeliveryService

logger = logging.getLogger(__name__)


class DeadlineApproachingCronService:
    def __init__(
        self,
        activity_model: OpportunityActivityModel | None = None,
        opportunity_model: OpportunityModel | None = None,
        speaker_profile_model: SpeakerProfileModel | None = None,
        opportunity_email_status_model: OpportunityEmailStatusModel | None = None,
        notification_delivery_service: NotificationDeliveryService | None = None,
    ):
        self.activity_model = activity_model or OpportunityActivityModel()
        self.opportunity_model = opportunity_model or OpportunityModel()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.opportunity_email_status_model = (
            opportunity_email_status_model or OpportunityEmailStatusModel()
        )
        self.notification_delivery_service = (
            notification_delivery_service or NotificationDeliveryService()
        )

    async def run_once(self) -> dict[str, Any]:
        batch = int(os.getenv("DEADLINE_APPROACHING_BATCH_SIZE", "100"))
        now = datetime.utcnow()
        today = now.date()

        speaker_ids = await self.activity_model.find_speaker_ids_with_open_wishlist(limit=batch)
        rows = await self.activity_model.find_open_wishlist_for_speakers(speaker_ids)

        by_speaker: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            speaker_id = (row.get("speaker_id") or "").strip()
            if speaker_id:
                by_speaker[speaker_id].append(row)

        sent = 0
        skipped = 0
        errors = 0
        skip_reasons: dict[str, int] = {
            "invalid_object_id": 0,
            "already_sent": 0,
            "opportunity_not_found": 0,
            "no_metadata_deadline": 0,
            "not_in_window": 0,
            "deadline_passed": 0,
            "notification_disabled": 0,
            "speaker_profile_not_found": 0,
            "no_speaker_email": 0,
            "no_eligible_opportunities": 0,
            "postmark_send_false": 0,
        }

        from app.dependencies import get_email_service

        email_service = get_email_service()

        for speaker_id, speaker_rows in by_speaker.items():
            if not self.activity_model.is_valid_object_id(speaker_id):
                skipped += 1
                skip_reasons["invalid_object_id"] += 1
                continue

            try:
                profile = await self.speaker_profile_model.get_profile(speaker_id)
                if not profile:
                    skipped += 1
                    skip_reasons["speaker_profile_not_found"] += 1
                    continue

                if not await self.notification_delivery_service.is_notification_enabled(
                    profile, "deadline_approaching"
                ):
                    skipped += 1
                    skip_reasons["notification_disabled"] += 1
                    continue

                frequency = await self.notification_delivery_service.get_frequency_for_speaker(
                    profile, "deadline_approaching"
                )
                to_email = speaker_profile_notification_email(profile)
                if not to_email:
                    skipped += 1
                    skip_reasons["no_speaker_email"] += 1
                    continue

                opportunity_ids = [
                    (row.get("opportunityId") or "").strip()
                    for row in speaker_rows
                    if (row.get("opportunityId") or "").strip()
                    and self.activity_model.is_valid_object_id(str(row.get("opportunityId") or ""))
                ]
                opportunities = await self.opportunity_model.get_by_ids(opportunity_ids)
                opp_by_id = {
                    str(o.get("_id")): ({**o, "_id": str(o["_id"])} if o.get("_id") is not None else o)
                    for o in opportunities
                }

                eligible: list[dict] = []
                for opportunity_id in opportunity_ids:
                    if await self.opportunity_email_status_model.is_deadline_sent(
                        speaker_id, opportunity_id
                    ):
                        skipped += 1
                        skip_reasons["already_sent"] += 1
                        continue
                    opp = opp_by_id.get(opportunity_id)
                    if not opp:
                        skipped += 1
                        skip_reasons["opportunity_not_found"] += 1
                        continue
                    deadline = parse_metadata_deadline_date(opp)
                    if deadline is None:
                        skipped += 1
                        skip_reasons["no_metadata_deadline"] += 1
                        continue
                    if today > deadline:
                        skipped += 1
                        skip_reasons["deadline_passed"] += 1
                        continue
                    if not is_deadline_in_lead_window(
                        deadline=deadline,
                        frequency=frequency,
                        slug="deadline_approaching",
                        today=today,
                    ):
                        skipped += 1
                        skip_reasons["not_in_window"] += 1
                        continue
                    eligible.append(opp)

                if not eligible:
                    skipped += 1
                    skip_reasons["no_eligible_opportunities"] += 1
                    continue

                eligible.sort(
                    key=lambda o: parse_metadata_deadline_date(o) or today
                )

                template_model = build_deadline_approaching_template_model(
                    profile=profile,
                    opportunities=eligible,
                    speaker_profile_id=speaker_id,
                    now=now,
                )
                ok = email_service.send_event_email(
                    event_type=EmailEventType.ALERT_DEADLINE_APPROACHING,
                    to_email=to_email,
                    template_model=template_model,
                )
                if ok:
                    sent_ids = [str(o.get("_id")) for o in eligible if o.get("_id")]
                    await self.opportunity_email_status_model.mark_deadline_sent_many(
                        speaker_id, sent_ids
                    )
                    await self.activity_model.mark_last_deadline_approaching_sent_many(
                        speaker_id, sent_ids
                    )
                    sent += 1
                    logger.info(
                        "Deadline approaching digest sent speaker_id=%s opportunities=%s",
                        speaker_id,
                        len(sent_ids),
                    )
                else:
                    skipped += 1
                    skip_reasons["postmark_send_false"] += 1
            except Exception:
                logger.exception(
                    "Deadline approaching digest failed speaker_id=%s",
                    speaker_id,
                )
                errors += 1

        return {
            "candidates": len(by_speaker),
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
