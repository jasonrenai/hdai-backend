"""Cron: weekly Find Opportunities + pitch-ready emails for after1week users."""

from __future__ import annotations

import logging
from typing import Any

from app.email.pitch_ready_notification import send_unsent_pitch_ready_emails_for_profile
from app.models.NotificationSettings import NotificationSettingsModel
from app.models.PendingNotificationEmail import PendingNotificationEmailModel
from app.models.SpeakerProfile import SpeakerProfileModel
from app.services.Opportunity import OpportunityService

logger = logging.getLogger(__name__)


class WeeklyNewOpportunityCronService:
    def __init__(
        self,
        notification_settings_model: NotificationSettingsModel | None = None,
        speaker_profile_model: SpeakerProfileModel | None = None,
        opportunity_service: OpportunityService | None = None,
        pending_model: PendingNotificationEmailModel | None = None,
    ):
        self.notification_settings_model = (
            notification_settings_model or NotificationSettingsModel()
        )
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.opportunity_service = opportunity_service or OpportunityService()
        self.pending_model = pending_model or PendingNotificationEmailModel()

    async def _speaker_profiles_for_user_ids(
        self, user_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not user_ids:
            return []

        grouped = await self.speaker_profile_model.get_profiles_by_user_ids(user_ids)
        user_id_set = {str(uid) for uid in user_ids}
        profiles: list[dict[str, Any]] = []
        seen_profile_ids: set[str] = set()
        for key, items in grouped.items():
            if str(key) not in user_id_set:
                continue
            for profile in items:
                profile_id = str(profile.get("_id") or "").strip()
                if not profile_id or profile_id in seen_profile_ids:
                    continue
                seen_profile_ids.add(profile_id)
                profiles.append(profile)
        return profiles

    async def _run_weekly_new_opportunity_matches(
        self, profiles: list[dict[str, Any]]
    ) -> dict[str, int]:
        processed = 0
        skipped = 0
        errors = 0

        for profile in profiles:
            speaker_profile_id = str(profile.get("_id") or "").strip()
            if not speaker_profile_id:
                skipped += 1
                continue
            try:
                await self.opportunity_service.run_matching_and_save(
                    speaker_profile_id,
                    send_matched_email=True,
                )
                processed += 1
            except Exception:
                logger.exception(
                    "Weekly new opportunity cron failed speaker_profile_id=%s",
                    speaker_profile_id,
                )
                errors += 1

        return {"new_opportunity_processed": processed, "new_opportunity_skipped": skipped, "new_opportunity_errors": errors}

    async def _run_weekly_pitch_ready_emails(
        self, profiles: list[dict[str, Any]]
    ) -> dict[str, int]:
        sent = 0
        skipped = 0
        errors = 0

        for profile in profiles:
            speaker_profile_id = str(profile.get("_id") or "").strip()
            if not speaker_profile_id:
                skipped += 1
                continue
            try:
                count = await send_unsent_pitch_ready_emails_for_profile(profile)
                sent += count
            except Exception:
                logger.exception(
                    "Weekly pitch-ready cron failed speaker_profile_id=%s",
                    speaker_profile_id,
                )
                errors += 1

        return {"pitch_emails_sent": sent, "pitch_skipped": skipped, "pitch_errors": errors}

    async def run_once(self) -> dict[str, Any]:
        cancelled_new_opportunity = await self.pending_model.cancel_pending_by_slug(
            "new_opportunity"
        )
        cancelled_pitch_ready = await self.pending_model.cancel_pending_by_slug("pitch_ready")

        new_opp_user_ids = (
            await self.notification_settings_model.list_user_ids_with_weekly_new_opportunity()
        )
        pitch_user_ids = (
            await self.notification_settings_model.list_user_ids_with_weekly_pitch_ready()
        )

        new_opp_profiles = await self._speaker_profiles_for_user_ids(new_opp_user_ids)
        pitch_profiles = await self._speaker_profiles_for_user_ids(pitch_user_ids)

        match_summary = await self._run_weekly_new_opportunity_matches(new_opp_profiles)
        pitch_summary = await self._run_weekly_pitch_ready_emails(pitch_profiles)

        return {
            "weekly_new_opportunity_users": len(new_opp_user_ids),
            "weekly_pitch_ready_users": len(pitch_user_ids),
            "new_opportunity_speaker_profiles": len(new_opp_profiles),
            "pitch_ready_speaker_profiles": len(pitch_profiles),
            "cancelled_pending_new_opportunity": cancelled_new_opportunity,
            "cancelled_pending_pitch_ready": cancelled_pitch_ready,
            **match_summary,
            **pitch_summary,
        }


def run_weekly_new_opportunity_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler."""

    async def _run() -> None:
        summary = await WeeklyNewOpportunityCronService().run_once()
        if any(
            summary.get(key)
            for key in (
                "new_opportunity_processed",
                "new_opportunity_errors",
                "pitch_emails_sent",
                "pitch_errors",
                "new_opportunity_speaker_profiles",
                "pitch_ready_speaker_profiles",
                "cancelled_pending_new_opportunity",
                "cancelled_pending_pitch_ready",
            )
        ):
            logger.info("Weekly notification cron: %s", summary)

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=3600)
    except Exception:
        logger.exception("Weekly notification cron top-level failure")
