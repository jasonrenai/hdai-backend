"""Resolve notification settings and enqueue delayed opportunity emails."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from app.email.notification_delivery import (
    after_delay_timedelta,
    default_pref_for_slug,
    is_before_notification_due,
    is_notification_test_email,
    is_notification_test_profile,
    parse_wishlist_anchor,
    pref_from_notification_doc,
    user_id_from_profile,
)
from app.models.NotificationSettings import NotificationSettingsModel
from app.models.OpportunityActivity import OpportunityActivityModel
from app.models.OpportunityEmailStatus import OpportunityEmailStatusModel
from app.models.PendingNotificationEmail import PendingNotificationEmailModel
from app.schemas.NotificationSettings import NotificationSlug

logger = logging.getLogger(__name__)


class NotificationDeliveryService:
    def __init__(
        self,
        notification_settings_model: NotificationSettingsModel | None = None,
        pending_model: PendingNotificationEmailModel | None = None,
        activity_model: OpportunityActivityModel | None = None,
        email_status_model: OpportunityEmailStatusModel | None = None,
    ):
        self.notification_settings_model = notification_settings_model or NotificationSettingsModel()
        self.pending_model = pending_model or PendingNotificationEmailModel()
        self.activity_model = activity_model or OpportunityActivityModel()
        self.email_status_model = email_status_model or OpportunityEmailStatusModel()

    async def is_test_user(self, profile: dict[str, Any]) -> bool:
        if is_notification_test_profile(profile):
            return True
        user_id = user_id_from_profile(profile)
        if not user_id:
            return False
        try:
            from bson import ObjectId
            from bson.errors import InvalidId

            from app.models.User import UserModel

            oid = ObjectId(user_id)
        except (InvalidId, TypeError):
            return False
        user = await UserModel().get_user({"_id": oid})
        if user is None:
            return False
        return is_notification_test_email(str(user.email))

    async def after_delay_for_profile(
        self,
        profile: dict[str, Any],
        frequency: str,
    ) -> timedelta:
        is_test = await self.is_test_user(profile)
        return after_delay_timedelta(frequency=frequency, is_test_user=is_test)

    async def is_before_send_due(
        self,
        *,
        profile: dict[str, Any],
        frequency: str,
        slug: NotificationSlug,
        deadline: Optional[Any],
        activity_row: dict[str, Any],
        now: Optional[datetime] = None,
    ) -> bool:
        from datetime import date

        deadline_date: date | None = None
        if deadline is not None:
            if isinstance(deadline, date) and not isinstance(deadline, datetime):
                deadline_date = deadline
            elif hasattr(deadline, "date"):
                deadline_date = deadline.date()

        is_test = await self.is_test_user(profile)
        return is_before_notification_due(
            frequency=frequency,
            slug=slug,  # type: ignore[arg-type]
            is_test_user=is_test,
            deadline=deadline_date,
            wishlist_anchor_at=parse_wishlist_anchor(activity_row),
            now=now,
        )

    async def get_pref_for_speaker(
        self,
        profile: dict[str, Any],
        slug: NotificationSlug,
    ) -> dict[str, object]:
        user_id = user_id_from_profile(profile)
        if not user_id:
            return default_pref_for_slug(slug)
        doc = await self.notification_settings_model.get_by_user_id(user_id)
        return pref_from_notification_doc(doc, slug)

    async def is_notification_enabled(
        self,
        profile: dict[str, Any],
        slug: NotificationSlug,
    ) -> bool:
        pref = await self.get_pref_for_speaker(profile, slug)
        return bool(pref.get("enabled", True))

    async def get_frequency_for_speaker(
        self,
        profile: dict[str, Any],
        slug: NotificationSlug,
    ) -> str:
        pref = await self.get_pref_for_speaker(profile, slug)
        return str(pref.get("frequency") or default_pref_for_slug(slug)["frequency"])

    async def enqueue_new_opportunity(
        self,
        *,
        speaker_profile_id: str,
        to_email: str,
        template_model: dict[str, Any],
        opportunity_ids: list[str],
        frequency: str,
        profile: dict[str, Any],
    ) -> bool:
        delay = await self.after_delay_for_profile(profile, frequency)
        if delay.total_seconds() <= 0:
            return False
        send_at = datetime.utcnow() + delay
        await self.pending_model.enqueue(
            {
                "slug": "new_opportunity",
                "speaker_id": str(speaker_profile_id),
                "to_email": to_email,
                "template_model": template_model,
                "opportunity_ids": [str(v) for v in opportunity_ids if v],
                "send_at": send_at,
            }
        )
        return True

    async def enqueue_pitch_ready(
        self,
        *,
        speaker_profile_id: str,
        opportunity_id: str,
        email_content_id: str,
        to_email: str,
        template_model: dict[str, Any],
        frequency: str,
        profile: dict[str, Any],
    ) -> bool:
        delay = await self.after_delay_for_profile(profile, frequency)
        if delay.total_seconds() <= 0:
            return False
        send_at = datetime.utcnow() + delay
        await self.pending_model.enqueue(
            {
                "slug": "pitch_ready",
                "speaker_id": str(speaker_profile_id),
                "opportunity_id": str(opportunity_id),
                "email_content_id": str(email_content_id),
                "to_email": to_email,
                "template_model": template_model,
                "send_at": send_at,
            }
        )
        return True

    async def should_cancel_pending(
        self,
        row: dict[str, Any],
        profile: dict[str, Any],
    ) -> Optional[str]:
        slug = str(row.get("slug") or "")
        speaker_id = str(row.get("speaker_id") or "")
        if not slug or not speaker_id:
            return "missing_ids"

        pref = await self.get_pref_for_speaker(profile, slug)  # type: ignore[arg-type]
        if not pref.get("enabled", True):
            return "notification_disabled"

        if slug == "new_opportunity":
            opportunity_ids = row.get("opportunity_ids") or []
            if not opportunity_ids:
                return "missing_opportunity_ids"
            sent_map = await self.email_status_model.get_sent_map_for_matched(
                speaker_id,
                [str(v) for v in opportunity_ids],
            )
            if all(sent_map.get(str(oid), False) for oid in opportunity_ids):
                return "already_sent"
            for oid in opportunity_ids:
                activity = await self.activity_model.get_one(speaker_id, str(oid))
                if activity and activity.get("isArchived"):
                    return "archived"
            return None

        if slug == "pitch_ready":
            opportunity_id = str(row.get("opportunity_id") or "")
            if not opportunity_id:
                return "missing_opportunity_id"
            activity = await self.activity_model.get_one(speaker_id, opportunity_id)
            if activity and activity.get("isArchived"):
                return "archived"
            if await self.pending_model.has_sent_for_pitch(speaker_id, opportunity_id):
                return "already_sent"
            return None

        return "unsupported_slug"
