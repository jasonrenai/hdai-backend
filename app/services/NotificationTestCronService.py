"""TEMP QA: 1-min cron for submission/deadline emails on notification test users only."""

from __future__ import annotations

import logging
from typing import Any

from app.services.DeadlineApproachingCronService import DeadlineApproachingCronService
from app.services.SubmissionReminderCronService import SubmissionReminderCronService

logger = logging.getLogger(__name__)


class NotificationTestCronService:
    def __init__(
        self,
        submission_service: SubmissionReminderCronService | None = None,
        deadline_service: DeadlineApproachingCronService | None = None,
    ):
        self.submission_service = submission_service or SubmissionReminderCronService()
        self.deadline_service = deadline_service or DeadlineApproachingCronService()

    async def run_once(self) -> dict[str, Any]:
        submission = await self.submission_service.run_once(
            test_users_only=True,
            cooldown_minutes=0,
        )
        deadline = await self.deadline_service.run_once(
            test_users_only=True,
            cooldown_minutes=0,
        )
        return {
            "submission_reminder": submission,
            "deadline_approaching": deadline,
        }


def run_notification_test_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler (Motor must run on the FastAPI event loop)."""

    async def _run() -> None:
        summary = await NotificationTestCronService().run_once()
        sub = summary["submission_reminder"]
        dead = summary["deadline_approaching"]
        if (
            sub.get("sent")
            or sub.get("errors")
            or dead.get("sent")
            or dead.get("errors")
            or sub.get("candidates")
            or dead.get("candidates")
        ):
            logger.info("Notification test cron: %s", summary)

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=300)
    except Exception:
        logger.exception("Notification test cron top-level failure")
