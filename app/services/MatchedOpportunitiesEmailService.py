"""Send matched opportunities using Postmark ALERT_NEW_OPPORTUNITY template (EmailService)."""

import logging
from typing import List, Optional

from app.email.enums import EmailEventType
from app.email.helpers import speaker_profile_notification_email
from app.email.notification_delivery import after_delay_timedelta
from app.models.OpportunityActivity import OpportunityActivityModel
from app.models.OpportunityEmailStatus import OpportunityEmailStatusModel
from app.email.opportunity_urls import opportunity_action_url, opportunity_app_url
from app.email.pitch_ready_notification import _deadline_from_metadata, _format_event_date
from app.models.SpeakerProfile import SpeakerProfileModel
from app.services.NotificationDeliveryService import NotificationDeliveryService
from app.services.Opportunity import OpportunityService

logger = logging.getLogger(__name__)


def _opportunity_row_for_template(opp: dict, speaker_profile_id: str) -> dict[str, str]:
    opportunity_id = str(opp.get("_id") or "").strip()
    return {
        "title": (opp.get("event_name") or opp.get("title") or "").strip(),
        "date": _format_event_date(opp),
        "location": (opp.get("location") or "").strip(),
        "deadline": _deadline_from_metadata(opp),
        "url": opportunity_action_url(opp),
        "opportunity_url": opportunity_app_url(speaker_profile_id, opportunity_id),
    }


class MatchedOpportunitiesEmailService:
    """Sends matched opportunities after a match run (Postmark New_opportunity template)."""

    def __init__(
        self,
        opportunity_service: OpportunityService = None,
        speaker_profile_model: SpeakerProfileModel = None,
        opportunity_activity_model: OpportunityActivityModel = None,
        opportunity_email_status_model: OpportunityEmailStatusModel = None,
        notification_delivery_service: NotificationDeliveryService = None,
    ):
        self.opportunity_service = opportunity_service or OpportunityService()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.opportunity_activity_model = opportunity_activity_model or OpportunityActivityModel()
        self.opportunity_email_status_model = (
            opportunity_email_status_model or OpportunityEmailStatusModel()
        )
        self.notification_delivery_service = (
            notification_delivery_service or NotificationDeliveryService()
        )

    async def _is_archived_for_speaker(self, speaker_profile_id: str, opportunity: dict) -> bool:
        opportunity_id = str(opportunity.get("_id") or "").strip()
        if not opportunity_id:
            return True
        activity = await self.opportunity_activity_model.get_one(speaker_profile_id, opportunity_id)
        return bool(activity and activity.get("isArchived"))

    async def send_matched_opportunities_email(
        self,
        speaker_profile_id: str,
        opportunity_documents: Optional[List[dict]] = None,
    ) -> bool:
        """
        Build template model { user_name, opportunities: [{ title, date, location, deadline, url, opportunity_url }, ...] }
        and send via EmailService (ALERT_NEW_OPPORTUNITY).

        If `opportunity_documents` is provided (e.g. right after a match run), use it; otherwise load from
        matchedOpportunities (status must be completed).

        Recipient: `email` on the speaker profile document.
        """
        from app.dependencies import get_email_service

        profile = await self.speaker_profile_model.get_profile(speaker_profile_id)
        if not profile:
            return False

        if not await self.notification_delivery_service.is_notification_enabled(
            profile, "new_opportunity"
        ):
            logger.info(
                "Matched opportunities email skipped: new_opportunity disabled speaker_profile_id=%s",
                speaker_profile_id,
            )
            return False

        if opportunity_documents is not None:
            opportunities = list(opportunity_documents)
            if not opportunities:
                return False
        else:
            opportunities, status = await self.opportunity_service.get_matched_opportunities_by_speaker_id(
                speaker_profile_id
            )
            if status != "completed" or not opportunities:
                return False

        eligible_opportunities: List[dict] = []
        for opp in opportunities:
            if not await self._is_archived_for_speaker(speaker_profile_id, opp):
                eligible_opportunities.append(opp)
        if not eligible_opportunities:
            return False

        opportunity_ids = [str(o.get("_id")) for o in eligible_opportunities if o.get("_id")]
        sent_map = await self.opportunity_email_status_model.get_sent_map_for_matched(
            speaker_profile_id,
            opportunity_ids,
        )
        unsent_opportunities = [
            opp for opp in eligible_opportunities if not sent_map.get(str(opp.get("_id")), False)
        ]
        if not unsent_opportunities:
            return False

        user_name = (profile.get("full_name") or "").strip() or "there"
        rows = [_opportunity_row_for_template(o, speaker_profile_id) for o in unsent_opportunities]
        to_email = speaker_profile_notification_email(profile)
        if not to_email:
            return False

        frequency = await self.notification_delivery_service.get_frequency_for_speaker(
            profile, "new_opportunity"
        )
        template_model = {
            "user_name": user_name,
            "opportunities": rows,
        }
        unsent_ids = [str(o.get("_id")) for o in unsent_opportunities if o.get("_id")]

        is_test = await self.notification_delivery_service.is_test_user(profile)
        delay = after_delay_timedelta(frequency=frequency, is_test_user=is_test)
        if delay.total_seconds() > 0:
            return await self.notification_delivery_service.enqueue_new_opportunity(
                speaker_profile_id=speaker_profile_id,
                to_email=to_email,
                template_model=template_model,
                opportunity_ids=unsent_ids,
                frequency=frequency,
                profile=profile,
            )

        try:
            sent = get_email_service().send_event_email(
                event_type=EmailEventType.ALERT_NEW_OPPORTUNITY,
                to_email=to_email,
                template_model=template_model,
            )
            if sent:
                await self.opportunity_email_status_model.mark_matched_sent_many(
                    speaker_profile_id,
                    unsent_ids,
                )
            return sent
        except Exception as e:
            logger.warning("Failed to send matched opportunities email: %s", e)
            return False
