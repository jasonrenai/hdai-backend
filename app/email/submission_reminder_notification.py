"""Template model for ALERT_SUBMISSION_REMINDER (Postmark Reminder_submition)."""

from __future__ import annotations

from typing import Any

from app.email.opportunity_urls import opportunity_action_url
from app.email.pitch_ready_notification import _deadline_from_metadata, _format_event_date


def build_submission_apply_url(opportunity: dict) -> str:
    return opportunity_action_url(opportunity)


def build_submission_reminder_template_model(
    *,
    profile: dict[str, Any],
    opportunity: dict[str, Any],
) -> dict[str, str]:
    user_name = (profile.get("full_name") or "").strip() or "there"
    event_name = (opportunity.get("event_name") or opportunity.get("title") or "").strip()
    return {
        "user_name": user_name,
        "event_name": event_name,
        "event_date": _format_event_date(opportunity),
        "event_location": (opportunity.get("location") or "").strip(),
        "deadline_date": _deadline_from_metadata(opportunity),
        "submission_url": build_submission_apply_url(opportunity),
    }
