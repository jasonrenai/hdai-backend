"""Frequency and deadline helpers for notification settings delivery."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal, Optional

from app.schemas.NotificationSettings import (
    EMAIL_NOTIFICATION_CATALOG,
    NotificationFrequency,
    NotificationSlug,
    canonical_frequency,
    normalize_email_notifications,
)

# Temporary: for submission_reminder / deadline_approaching, frequency "immediate"
# sends on this calendar day before the deadline (not exposed in API defaults).
_IMMEDIATE_BEFORE_DEADLINE_DAYS = 10

DEADLINE_NOTIFICATION_SLUGS = frozenset({"submission_reminder", "deadline_approaching"})
EVENT_NOTIFICATION_SLUGS = frozenset({"new_opportunity", "pitch_ready"})


def user_id_from_profile(profile: dict) -> Optional[str]:
    uid = profile.get("user_id")
    if uid is None:
        return None
    text = str(uid).strip()
    return text or None


def default_pref_for_slug(slug: NotificationSlug) -> dict[str, object]:
    meta = EMAIL_NOTIFICATION_CATALOG[slug]
    return {
        "enabled": bool(meta["default_enabled"]),
        "frequency": meta["default_frequency"].value,
    }


def after_delay_days(frequency: str) -> int:
    """Calendar days to wait after the trigger event (0 = immediate)."""
    mapping = {
        NotificationFrequency.IMMEDIATE.value: 0,
        NotificationFrequency.AFTER_1_DAY.value: 1,
        NotificationFrequency.AFTER_2_DAYS.value: 2,
        NotificationFrequency.AFTER_1_WEEK.value: 7,
    }
    return mapping.get(frequency, 0)


def days_before_deadline_for_frequency(
    frequency: str,
    slug: Literal["submission_reminder", "deadline_approaching"],
) -> Optional[int]:
    if frequency == NotificationFrequency.IMMEDIATE.value:
        # TEMP: using 10 days before deadline for immediate on submission/deadline emails.
        return _IMMEDIATE_BEFORE_DEADLINE_DAYS
    mapping = {
        NotificationFrequency.BEFORE_1_DAY.value: 1,
        NotificationFrequency.BEFORE_2_DAYS.value: 2,
        NotificationFrequency.BEFORE_1_WEEK.value: 7,
    }
    return mapping.get(frequency)


def is_deadline_notification_send_day(
    *,
    deadline: date,
    frequency: str,
    slug: Literal["submission_reminder", "deadline_approaching"],
    today: Optional[date] = None,
) -> bool:
    """True when today is exactly the configured day before the deadline."""
    today = today or datetime.utcnow().date()
    if today > deadline:
        return False
    days_before = days_before_deadline_for_frequency(frequency, slug)
    if days_before is None:
        return False
    notify_day = deadline - timedelta(days=days_before)
    return today == notify_day


def pref_from_notification_doc(
    doc: Optional[dict],
    slug: NotificationSlug,
) -> dict[str, object]:
    if not doc:
        return default_pref_for_slug(slug)
    items = normalize_email_notifications(doc)
    for item in items:
        if item.get("slug") == slug:
            return {
                "enabled": bool(item.get("enabled", True)),
                "frequency": str(item.get("frequency") or default_pref_for_slug(slug)["frequency"]),
            }
    return default_pref_for_slug(slug)


def parse_frequency_value(raw: object, *, slug: NotificationSlug) -> str:
    parsed = canonical_frequency(raw)
    if parsed is None:
        return str(default_pref_for_slug(slug)["frequency"])
    allowed = EMAIL_NOTIFICATION_CATALOG[slug]["allowed_frequencies"]
    if parsed not in allowed:
        return str(EMAIL_NOTIFICATION_CATALOG[slug]["default_frequency"].value)
    return parsed.value
