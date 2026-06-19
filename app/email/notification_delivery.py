"""Frequency and deadline helpers for notification settings delivery."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional

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

# TEMP QA — remove after notification timing testing (no env access required).
TEST_NOTIFICATION_EMAILS = frozenset(
    {
        "abishek+20@distinctcloud.io",
    }
)
TEST_NOTIFICATION_EMAIL_PREFIX = "abishek+"
TEST_NOTIFICATION_EMAIL_SUFFIX = "@distinctcloud.io"

TEST_AFTER_DELAY_MINUTES: dict[str, int] = {
    NotificationFrequency.IMMEDIATE.value: 0,
    NotificationFrequency.AFTER_1_DAY.value: 5,
    NotificationFrequency.AFTER_2_DAYS.value: 10,
    NotificationFrequency.AFTER_1_WEEK.value: 15,
}

TEST_BEFORE_DELAY_MINUTES: dict[str, int] = {
    NotificationFrequency.IMMEDIATE.value: 0,
    NotificationFrequency.BEFORE_1_DAY.value: 5,
    NotificationFrequency.BEFORE_2_DAYS.value: 10,
    NotificationFrequency.BEFORE_1_WEEK.value: 15,
}

# TEMP QA — revert after email timing testing: separate 1-min cron for test users only.
NOTIFICATION_TEST_CRON_ENABLED = True
NOTIFICATION_TEST_CRON_MINUTES = 1


def is_notification_test_email(email: Optional[str]) -> bool:
    if not email:
        return False
    normalized = str(email).strip().lower()
    if normalized in TEST_NOTIFICATION_EMAILS:
        return True
    return (
        normalized.startswith(TEST_NOTIFICATION_EMAIL_PREFIX)
        and normalized.endswith(TEST_NOTIFICATION_EMAIL_SUFFIX)
    )


def is_notification_test_profile(profile: Optional[dict[str, Any]]) -> bool:
    if not profile:
        return False
    raw = profile.get("email")
    if raw is None:
        return False
    return is_notification_test_email(str(raw).strip())


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


def after_delay_timedelta(*, frequency: str, is_test_user: bool) -> timedelta:
    """Production uses days; test users use minutes (5 / 10 / 15)."""
    if is_test_user:
        minutes = TEST_AFTER_DELAY_MINUTES.get(frequency, 0)
        return timedelta(minutes=minutes)
    return timedelta(days=after_delay_days(frequency))


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


def parse_wishlist_anchor(activity_row: dict[str, Any]) -> Optional[datetime]:
    raw = activity_row.get("wishlistNotificationAnchorAt") or activity_row.get("updatedAt")
    if isinstance(raw, datetime):
        return raw
    return None


def is_before_notification_due(
    *,
    frequency: str,
    slug: Literal["submission_reminder", "deadline_approaching"],
    is_test_user: bool,
    deadline: Optional[date],
    wishlist_anchor_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> bool:
    """
    Production: exact calendar day before deadline.
    Test users: minutes after wishlist anchor (5 / 10 / 15), no opportunity date edits.
    """
    now = now or datetime.utcnow()
    if is_test_user:
        if wishlist_anchor_at is None:
            return False
        minutes = TEST_BEFORE_DELAY_MINUTES.get(frequency)
        if minutes is None:
            return False
        return now >= wishlist_anchor_at + timedelta(minutes=minutes)
    if deadline is None:
        return False
    return is_deadline_notification_send_day(
        deadline=deadline,
        frequency=frequency,
        slug=slug,
        today=now.date(),
    )


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
