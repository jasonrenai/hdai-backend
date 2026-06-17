from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

NotificationSlug = Literal[
    "new_opportunity",
    "pitch_ready",
    "submission_reminder",
    "deadline_approaching",
]

NOTIFICATION_SLUGS: tuple[NotificationSlug, ...] = (
    "new_opportunity",
    "pitch_ready",
    "submission_reminder",
    "deadline_approaching",
)


class NotificationFrequency(str, Enum):
    IMMEDIATE = "immediate"
    BEFORE_1_DAY = "before1day"
    BEFORE_2_DAYS = "before2days"
    BEFORE_1_WEEK = "before1week"
    AFTER_1_DAY = "after1day"
    AFTER_2_DAYS = "after2days"
    AFTER_1_WEEK = "after1week"


# Accept legacy underscore values from older clients or DB records.
LEGACY_FREQUENCY_ALIASES: dict[str, NotificationFrequency] = {
    "before_1_day": NotificationFrequency.BEFORE_1_DAY,
    "before_2_days": NotificationFrequency.BEFORE_2_DAYS,
    "before_1_week": NotificationFrequency.BEFORE_1_WEEK,
    "after_1_day": NotificationFrequency.AFTER_1_DAY,
    "after_2_days": NotificationFrequency.AFTER_2_DAYS,
    "after_1_week": NotificationFrequency.AFTER_1_WEEK,
}


def canonical_frequency(raw: Any) -> Optional[NotificationFrequency]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, NotificationFrequency):
        return raw
    key = str(raw).strip().lower()
    if key in LEGACY_FREQUENCY_ALIASES:
        return LEGACY_FREQUENCY_ALIASES[key]
    try:
        return NotificationFrequency(key)
    except ValueError:
        return None


EMAIL_NOTIFICATION_CATALOG: dict[str, dict[str, Any]] = {
    "new_opportunity": {
        "name": "New opportunity",
        "default_enabled": True,
        "default_frequency": NotificationFrequency.IMMEDIATE,
        "allowed_frequencies": {
            NotificationFrequency.IMMEDIATE,
            NotificationFrequency.AFTER_1_DAY,
            NotificationFrequency.AFTER_2_DAYS,
            NotificationFrequency.AFTER_1_WEEK,
        },
    },
    "pitch_ready": {
        "name": "Pitch ready",
        "default_enabled": True,
        "default_frequency": NotificationFrequency.IMMEDIATE,
        "allowed_frequencies": {
            NotificationFrequency.IMMEDIATE,
            NotificationFrequency.AFTER_1_DAY,
            NotificationFrequency.AFTER_2_DAYS,
            NotificationFrequency.AFTER_1_WEEK,
        },
    },
    "submission_reminder": {
        "name": "Submission reminder",
        "default_enabled": True,
        "default_frequency": NotificationFrequency.BEFORE_2_DAYS,
        "allowed_frequencies": {
            NotificationFrequency.BEFORE_1_DAY,
            NotificationFrequency.BEFORE_2_DAYS,
            NotificationFrequency.BEFORE_1_WEEK,
            NotificationFrequency.AFTER_1_DAY,
            NotificationFrequency.AFTER_2_DAYS,
            NotificationFrequency.AFTER_1_WEEK,
        },
    },
    "deadline_approaching": {
        "name": "Deadline approaching",
        "default_enabled": True,
        "default_frequency": NotificationFrequency.BEFORE_2_DAYS,
        "allowed_frequencies": {
            NotificationFrequency.IMMEDIATE,
            NotificationFrequency.BEFORE_1_DAY,
            NotificationFrequency.BEFORE_2_DAYS,
            NotificationFrequency.BEFORE_1_WEEK,
        },
    },
}


def default_email_notifications() -> list[dict[str, Any]]:
    return [
        email_notification_to_document(
            {
                "slug": slug,
                "name": meta["name"],
                "enabled": meta["default_enabled"],
                "frequency": meta["default_frequency"].value,
            }
        )
        for slug, meta in EMAIL_NOTIFICATION_CATALOG.items()
    ]


def _frequency_value(raw: Any, *, slug: str) -> str:
    catalog = EMAIL_NOTIFICATION_CATALOG[slug]
    default = catalog["default_frequency"]
    parsed = canonical_frequency(raw)
    return (parsed or default).value


def _enabled_value(raw: Any, *, slug: str) -> bool:
    if raw is None:
        return bool(EMAIL_NOTIFICATION_CATALOG[slug]["default_enabled"])
    return bool(raw)


def normalize_email_notifications(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a full email_notifications list from stored doc or legacy flat booleans."""
    stored = doc.get("email_notifications")
    by_slug: dict[str, dict[str, Any]] = {}

    if isinstance(stored, list):
        for item in stored:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            if slug in EMAIL_NOTIFICATION_CATALOG:
                by_slug[slug] = item

    for slug in NOTIFICATION_SLUGS:
        catalog = EMAIL_NOTIFICATION_CATALOG[slug]
        item = by_slug.get(slug, {})
        enabled = _enabled_value(item.get("enabled"), slug=slug)
        if slug in doc and "enabled" not in item and isinstance(doc.get(slug), bool):
            enabled = bool(doc[slug])
        frequency = _frequency_value(item.get("frequency"), slug=slug)
        by_slug[slug] = {
            "slug": slug,
            "name": catalog["name"],
            "enabled": enabled,
            "frequency": frequency,
        }

    return [by_slug[slug] for slug in NOTIFICATION_SLUGS]


def email_notification_to_document(item: dict[str, Any]) -> dict[str, Any]:
    """Persisted MongoDB shape for one email notification setting."""
    frequency = _frequency_value(item.get("frequency"), slug=item["slug"])
    return {
        "slug": item["slug"],
        "name": EMAIL_NOTIFICATION_CATALOG[item["slug"]]["name"],
        "enabled": bool(item.get("enabled", True)),
        "frequency": frequency,
    }


def validate_email_notification_item(item: dict[str, Any]) -> dict[str, Any]:
    parsed = EmailNotificationSetting.model_validate(item)
    return parsed.model_dump()


class EmailNotificationSetting(BaseModel):
    slug: NotificationSlug
    name: str
    enabled: bool = True
    frequency: NotificationFrequency

    @field_validator("frequency", mode="before")
    @classmethod
    def normalize_frequency(cls, value: Any) -> Any:
        if value is None:
            return value
        parsed = canonical_frequency(value)
        if parsed is None:
            return value
        return parsed.value

    @model_validator(mode="after")
    def validate_item(self) -> "EmailNotificationSetting":
        catalog = EMAIL_NOTIFICATION_CATALOG[self.slug]
        allowed = catalog["allowed_frequencies"]
        if self.frequency not in allowed:
            allowed_values = ", ".join(sorted(f.value for f in allowed))
            raise ValueError(
                f"frequency '{self.frequency.value}' is not allowed for '{self.slug}'. "
                f"Allowed: {allowed_values}"
            )
        return self


class NotificationSettingsResponse(BaseModel):
    user_id: str
    email_notifications: list[EmailNotificationSetting] = Field(min_length=4, max_length=4)
    createdOn: Optional[datetime] = None
    updatedOn: Optional[datetime] = None


class EmailNotificationUpdateItem(BaseModel):
    slug: NotificationSlug
    name: Optional[str] = None
    enabled: Optional[bool] = None
    frequency: Optional[NotificationFrequency] = None

    @field_validator("frequency", mode="before")
    @classmethod
    def normalize_frequency(cls, value: Any) -> Any:
        if value is None:
            return value
        parsed = canonical_frequency(value)
        if parsed is None:
            return value
        return parsed.value

    @model_validator(mode="after")
    def validate_update_item(self) -> "EmailNotificationUpdateItem":
        if self.enabled is None and self.frequency is None:
            raise ValueError(
                f"At least one of 'enabled' or 'frequency' must be provided for slug '{self.slug}'"
            )

        catalog = EMAIL_NOTIFICATION_CATALOG[self.slug]
        allowed = catalog["allowed_frequencies"]
        if self.frequency is not None and self.frequency not in allowed:
            allowed_values = ", ".join(sorted(f.value for f in allowed))
            raise ValueError(
                f"frequency '{self.frequency.value}' is not allowed for '{self.slug}'. "
                f"Allowed: {allowed_values}"
            )
        return self


class NotificationSettingsUpdateSchema(BaseModel):
    email_notifications: list[EmailNotificationUpdateItem] = Field(min_length=1)

    @field_validator("email_notifications")
    @classmethod
    def unique_slugs(
        cls, items: list[EmailNotificationUpdateItem]
    ) -> list[EmailNotificationUpdateItem]:
        slugs = [item.slug for item in items]
        if len(slugs) != len(set(slugs)):
            raise ValueError("email_notifications must not contain duplicate slugs")
        return items

    def non_empty_updates(self) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        for item in self.email_notifications:
            payload = item.model_dump(exclude_none=True)
            payload.pop("name", None)
            updates.append(payload)
        return updates
