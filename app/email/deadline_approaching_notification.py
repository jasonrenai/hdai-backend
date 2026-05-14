"""Template model for ALERT_DEADLINE_APPROACHING (Postmark Deadline_approaching)."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any

from app.email.pitch_ready_notification import _format_event_date
from app.email.submission_reminder_notification import build_submission_apply_url

# Metadata often stores deadline as plain string "2026-05-14" (YYYY-MM-DD); allow that anywhere in the string.
_ISO_DATE_IN_METADATA = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _iso_date_from_metadata_value(raw: Any) -> str | None:
    """Return normalized YYYY-MM-DD if `raw` is or contains a valid ISO date string."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = _ISO_DATE_IN_METADATA.search(s)
    if not m:
        return None
    ymd = m.group(1)
    try:
        datetime.strptime(ymd, "%Y-%m-%d")
    except ValueError:
        return None
    return ymd


def _raw_application_deadline_date(opportunity: dict) -> str:
    """
    First valid ISO date from opportunity.metadata, in key order:
    application_submission_deadline, submission_deadline, deadline.
    Values are strings like ``2026-05-14`` (or text containing that pattern).
    """
    meta = opportunity.get("metadata") if isinstance(opportunity.get("metadata"), dict) else {}
    for key in ("application_submission_deadline", "submission_deadline", "deadline"):
        raw = meta.get(key)
        ymd = _iso_date_from_metadata_value(raw)
        if ymd:
            return ymd
    return ""


def parse_metadata_deadline_date(opportunity: dict) -> date | None:
    """Parse metadata deadline to a date, or None if missing / invalid."""
    raw = _raw_application_deadline_date(opportunity)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def application_submission_deadline_display(opportunity: dict) -> str:
    return _raw_application_deadline_date(opportunity)


def notification_lead_calendar_days() -> int:
    """
    How many calendar days *before* the deadline date to start sending (inclusive of deadline day).

    Default ``1``: send when ``today`` is the deadline date **or** the calendar day before
    (e.g. deadline 2026-05-14 → notify on 2026-05-13 and 2026-05-14 UTC date).

    Set ``DEADLINE_APPROACHING_LEAD_DAYS=0`` to only send when ``deadline_date <= today`` (deadline day or later).
    """
    try:
        v = int(os.getenv("DEADLINE_APPROACHING_LEAD_DAYS", "1"))
    except ValueError:
        return 1
    return max(0, v)


def is_deadline_in_notification_window(
    opportunity: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """
    True if ``today`` (UTC calendar date) falls in
    ``[deadline_date - LEAD_DAYS, deadline_date]`` inclusive, where ``LEAD_DAYS`` comes from
    :func:`notification_lead_calendar_days`.
    """
    now = now or datetime.utcnow()
    today = now.date()
    d = parse_metadata_deadline_date(opportunity)
    if d is None:
        return False
    lead = notification_lead_calendar_days()
    first_notify_day = d - timedelta(days=lead)
    return first_notify_day <= today <= d


def is_deadline_on_or_before_today(
    opportunity: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """
    Strict rule: deadline calendar date is on or before today (UTC).
    Equivalent to :func:`is_deadline_in_notification_window` with ``LEAD_DAYS=0``.
    """
    now = now or datetime.utcnow()
    today = now.date()
    d = parse_metadata_deadline_date(opportunity)
    if d is None:
        return False
    return d <= today


# Backwards-compatible name: uses lead window (default 1 day before through deadline day).
is_application_deadline_within_one_day = is_deadline_in_notification_window


def days_remaining_label_for_approaching(opportunity: dict, *, now: datetime | None = None) -> str:
    """Calendar days from today (UTC) until deadline date; 0 on or after deadline day."""
    now = now or datetime.utcnow()
    today = now.date()
    d = parse_metadata_deadline_date(opportunity)
    if d is None:
        return "0"
    return str(max(0, (d - today).days))


def build_deadline_approaching_template_model(
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
        "deadline_date": application_submission_deadline_display(opportunity),
        "days_remaining": days_remaining_label_for_approaching(opportunity),
        "submission_url": build_submission_apply_url(opportunity),
    }
