"""Pitch-ready alert when outreach email content is generated — Postmark ALERT_PITCH_READY template."""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlencode

from app.email.constants import PITCH_REVIEW_FRONTEND_BASE
from app.email.enums import EmailEventType

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

    oid = str(opportunity.get("_id") or opportunity_id)
    event_name = (opportunity.get("event_name") or opportunity.get("title") or "").strip()
    event_location = (opportunity.get("location") or "").strip()

    return get_email_service().send_event_email(
        event_type=EmailEventType.ALERT_PITCH_READY,
        to_email=recipient,
        template_model={
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
        },
    )


def try_send_pitch_ready_email_after_content_created(
    *,
    profile: dict,
    opportunity: dict,
    opportunity_id: str,
    speaker_profile_id: str,
    email_content_id: str,
) -> None:
    """Fire-and-forget after EmailContent insert; never raises."""
    try:
        from app.email.helpers import speaker_profile_notification_email

        to_email = speaker_profile_notification_email(profile)
        if not to_email:
            logger.warning(
                "Pitch-ready email skipped: no email on speaker profile speaker_profile_id=%s",
                speaker_profile_id,
            )
            return
        user_name = (profile.get("full_name") or "").strip()
        if not send_pitch_ready_email(
            user_name=user_name,
            opportunity=opportunity,
            opportunity_id=opportunity_id,
            speaker_profile_id=speaker_profile_id,
            email_content_id=email_content_id,
            to_email=to_email,
        ):
            logger.warning("Pitch-ready email was not sent (send_event_email returned false).")
    except Exception as e:
        logger.warning("Pitch-ready email failed: %s", e)
