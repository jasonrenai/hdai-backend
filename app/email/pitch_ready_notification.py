"""Pitch-ready alert when outreach email content is generated — Postmark ALERT_PITCH_READY template."""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlencode

from app.email.constants import PITCH_REVIEW_FRONTEND_BASE
from app.email.enums import EmailEventType
from app.email.notification_delivery import is_weekly_digest_frequency
from app.models.EmailContent import EmailContentModel
from app.models.Opportunity import OpportunityModel
from app.models.OpportunityActivity import OpportunityActivityModel
from app.services.NotificationDeliveryService import NotificationDeliveryService

logger = logging.getLogger(__name__)


def _scalar_date_str(value: Any) -> str:
    if value is None or value == "":
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            return str(value).strip()
    return str(value).strip()


def _format_event_date(opportunity: dict) -> str:
    start = _scalar_date_str(opportunity.get("start_date"))
    end = _scalar_date_str(opportunity.get("end_date"))
    if start and end and start != end:
        return f"{start} – {end}"
    return start or end or ""


def _deadline_from_metadata(opportunity: dict) -> str:
    meta = opportunity.get("metadata") if isinstance(opportunity.get("metadata"), dict) else {}
    for key in ("submission_deadline", "application_submission_deadline", "deadline"):
        raw = meta.get(key)
        if raw in (None, ""):
            continue
        s = _scalar_date_str(raw).strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
    return ""


def build_pitch_review_url(
    *,
    opportunity_id: str,
    speaker_profile_id: str,
    email_content_id: str,
) -> str:
    q = urlencode(
        {
            "speakerProfileId": speaker_profile_id,
            "email_content_id": email_content_id,
        }
    )
    return f"{PITCH_REVIEW_FRONTEND_BASE}/opportunities/{opportunity_id}/curated-speaker-pitch?{q}"


def build_pitch_ready_template_model(
    *,
    user_name: str,
    opportunity: dict,
    opportunity_id: str,
    speaker_profile_id: str,
    email_content_id: str,
) -> dict[str, str]:
    oid = str(opportunity.get("_id") or opportunity_id)
    event_name = (opportunity.get("event_name") or opportunity.get("title") or "").strip()
    event_location = (opportunity.get("location") or "").strip()
    return {
        "user_name": (user_name or "").strip() or "there",
        "event_name": event_name,
        "event_date": _format_event_date(opportunity),
        "event_location": event_location,
        "deadline_date": _deadline_from_metadata(opportunity),
        "pitch_review_url": build_pitch_review_url(
            opportunity_id=oid,
            speaker_profile_id=speaker_profile_id,
            email_content_id=email_content_id,
        ),
    }


def send_pitch_ready_email(
    *,
    user_name: str,
    opportunity: dict,
    opportunity_id: str,
    speaker_profile_id: str,
    email_content_id: str,
    to_email: Optional[str] = None,
) -> bool:
    """Send Pitch_ready template to the speaker profile contact address."""
    from app.dependencies import get_email_service

    recipient = (to_email or "").strip()
    if not recipient:
        return False

    return get_email_service().send_event_email(
        event_type=EmailEventType.ALERT_PITCH_READY,
        to_email=recipient,
        template_model=build_pitch_ready_template_model(
            user_name=user_name,
            opportunity=opportunity,
            opportunity_id=opportunity_id,
            speaker_profile_id=speaker_profile_id,
            email_content_id=email_content_id,
        ),
    )


async def try_send_or_schedule_pitch_ready_email(
    *,
    profile: dict,
    opportunity: dict,
    opportunity_id: str,
    speaker_profile_id: str,
    email_content_id: str,
    delivery_service: NotificationDeliveryService | None = None,
    email_content_model: EmailContentModel | None = None,
) -> None:
    """Send pitch-ready email immediately when frequency is immediate; weekly defers to Monday cron."""
    try:
        from app.email.helpers import speaker_profile_notification_email

        delivery = delivery_service or NotificationDeliveryService()
        if not await delivery.is_notification_enabled(profile, "pitch_ready"):
            logger.info(
                "Pitch-ready email skipped: pitch_ready disabled speaker_profile_id=%s",
                speaker_profile_id,
            )
            return

        to_email = speaker_profile_notification_email(profile)
        if not to_email:
            logger.warning(
                "Pitch-ready email skipped: no email on speaker profile speaker_profile_id=%s",
                speaker_profile_id,
            )
            return

        frequency = await delivery.get_frequency_for_speaker(profile, "pitch_ready")
        if is_weekly_digest_frequency(frequency):
            logger.info(
                "Pitch-ready email deferred to weekly cron speaker_profile_id=%s email_content_id=%s",
                speaker_profile_id,
                email_content_id,
            )
            return

        user_name = (profile.get("full_name") or "").strip()
        sent = send_pitch_ready_email(
            user_name=user_name,
            opportunity=opportunity,
            opportunity_id=opportunity_id,
            speaker_profile_id=speaker_profile_id,
            email_content_id=email_content_id,
            to_email=to_email,
        )
        if not sent:
            logger.warning("Pitch-ready email was not sent (send_event_email returned false).")
            return

        content_model = email_content_model or EmailContentModel()
        await content_model.mark_pitch_ready_notification_sent(email_content_id)
    except Exception as e:
        logger.warning("Pitch-ready email failed: %s", e)


async def send_unsent_pitch_ready_emails_for_profile(
    profile: dict,
    *,
    delivery_service: NotificationDeliveryService | None = None,
    email_content_model: EmailContentModel | None = None,
    opportunity_model: OpportunityModel | None = None,
    activity_model: OpportunityActivityModel | None = None,
) -> int:
    """Send all unsent pitch-ready emails for one speaker profile (weekly cron). Returns send count."""
    from app.email.helpers import speaker_profile_notification_email

    speaker_profile_id = str(profile.get("_id") or "").strip()
    if not speaker_profile_id:
        return 0

    delivery = delivery_service or NotificationDeliveryService()
    if not await delivery.is_notification_enabled(profile, "pitch_ready"):
        return 0

    frequency = await delivery.get_frequency_for_speaker(profile, "pitch_ready")
    if not is_weekly_digest_frequency(frequency):
        return 0

    to_email = speaker_profile_notification_email(profile)
    if not to_email:
        return 0

    content_model = email_content_model or EmailContentModel()
    opp_model = opportunity_model or OpportunityModel()
    act_model = activity_model or OpportunityActivityModel()
    user_name = (profile.get("full_name") or "").strip()

    unsent_rows = await content_model.list_unsent_pitch_ready_by_speaker_id(speaker_profile_id)
    sent_count = 0
    for row in unsent_rows:
        email_content_id = str(row.get("_id") or "").strip()
        opportunity_id = str(row.get("opportunity_id") or "").strip()
        if not email_content_id or not opportunity_id:
            continue

        activity = await act_model.get_one(speaker_profile_id, opportunity_id)
        if activity and activity.get("isArchived"):
            continue

        opportunity = await opp_model.get_by_id(opportunity_id)
        if not opportunity:
            continue
        if opportunity.get("_id") is not None:
            opportunity = {**opportunity, "_id": str(opportunity["_id"])}

        if send_pitch_ready_email(
            user_name=user_name,
            opportunity=opportunity,
            opportunity_id=opportunity_id,
            speaker_profile_id=speaker_profile_id,
            email_content_id=email_content_id,
            to_email=to_email,
        ):
            await content_model.mark_pitch_ready_notification_sent(email_content_id)
            sent_count += 1

    return sent_count


def try_send_pitch_ready_email_after_content_created(
    *,
    profile: dict,
    opportunity: dict,
    opportunity_id: str,
    speaker_profile_id: str,
    email_content_id: str,
) -> None:
    """Backward-compatible sync wrapper; schedules on the app event loop."""
    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(
            try_send_or_schedule_pitch_ready_email(
                profile=profile,
                opportunity=opportunity,
                opportunity_id=opportunity_id,
                speaker_profile_id=speaker_profile_id,
                email_content_id=email_content_id,
            ),
            timeout=60,
        )
    except Exception as e:
        logger.warning("Pitch-ready email scheduling failed: %s", e)
