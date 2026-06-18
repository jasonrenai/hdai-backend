"""Cron: send delayed new_opportunity / pitch_ready emails from pending queue."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.email.enums import EmailEventType
from app.email.helpers import speaker_profile_notification_email
from app.models.OpportunityEmailStatus import OpportunityEmailStatusModel
from app.models.PendingNotificationEmail import PendingNotificationEmailModel
from app.models.SpeakerProfile import SpeakerProfileModel
from app.services.NotificationDeliveryService import NotificationDeliveryService

logger = logging.getLogger(__name__)


class PendingNotificationEmailCronService:
    def __init__(
        self,
        pending_model: PendingNotificationEmailModel | None = None,
        speaker_profile_model: SpeakerProfileModel | None = None,
        delivery_service: NotificationDeliveryService | None = None,
        email_status_model: OpportunityEmailStatusModel | None = None,
    ):
        self.pending_model = pending_model or PendingNotificationEmailModel()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.delivery_service = delivery_service or NotificationDeliveryService()
        self.email_status_model = email_status_model or OpportunityEmailStatusModel()

    async def run_once(self) -> dict[str, Any]:
        batch = int(os.getenv("PENDING_NOTIFICATION_EMAIL_BATCH_SIZE", "50"))
        rows = await self.pending_model.find_due_pending(limit=batch)
        sent = 0
        cancelled = 0
        skipped = 0
        errors = 0

        from app.dependencies import get_email_service

        email_service = get_email_service()

        for row in rows:
            pending_id = str(row.get("_id") or "")
            slug = str(row.get("slug") or "")
            speaker_id = str(row.get("speaker_id") or "")
            if not pending_id or not slug or not speaker_id:
                skipped += 1
                continue

            try:
                profile = await self.speaker_profile_model.get_profile(speaker_id)
                if not profile:
                    await self.pending_model.mark_cancelled(pending_id, reason="speaker_profile_not_found")
                    cancelled += 1
                    continue

                cancel_reason = await self.delivery_service.should_cancel_pending(row, profile)
                if cancel_reason:
                    await self.pending_model.mark_cancelled(pending_id, reason=cancel_reason)
                    cancelled += 1
                    continue

                to_email = (row.get("to_email") or "").strip() or speaker_profile_notification_email(profile)
                if not to_email:
                    skipped += 1
                    continue

                template_model = row.get("template_model")
                if not isinstance(template_model, dict):
                    skipped += 1
                    continue

                if slug == "new_opportunity":
                    event_type = EmailEventType.ALERT_NEW_OPPORTUNITY
                elif slug == "pitch_ready":
                    event_type = EmailEventType.ALERT_PITCH_READY
                else:
                    await self.pending_model.mark_cancelled(pending_id, reason="unsupported_slug")
                    cancelled += 1
                    continue

                ok = email_service.send_event_email(
                    event_type=event_type,
                    to_email=to_email,
                    template_model=template_model,
                )
                if not ok:
                    skipped += 1
                    continue

                if slug == "new_opportunity":
                    opportunity_ids = row.get("opportunity_ids") or []
                    if opportunity_ids:
                        await self.email_status_model.mark_matched_sent_many(
                            speaker_id,
                            [str(v) for v in opportunity_ids if v],
                        )
                await self.pending_model.mark_sent(pending_id)
                sent += 1
            except Exception:
                logger.exception(
                    "Pending notification email failed pending_id=%s slug=%s speaker_id=%s",
                    pending_id,
                    slug,
                    speaker_id,
                )
                errors += 1

        return {
            "candidates": len(rows),
            "sent": sent,
            "cancelled": cancelled,
            "skipped": skipped,
            "errors": errors,
        }


def run_pending_notification_email_cron_sync() -> None:
    async def _run() -> None:
        summary = await PendingNotificationEmailCronService().run_once()
        if summary["sent"] or summary["errors"] or summary["candidates"]:
            logger.info("Pending notification email cron: %s", summary)

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=300)
    except Exception:
        logger.exception("Pending notification email cron top-level failure")
