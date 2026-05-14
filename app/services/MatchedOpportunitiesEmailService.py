"""Send matched opportunities using Postmark ALERT_NEW_OPPORTUNITY template (EmailService)."""

import logging
from typing import List, Optional

from app.email.enums import EmailEventType
from app.email.helpers import speaker_profile_notification_email
from app.email.opportunity_urls import opportunity_action_url
from app.email.pitch_ready_notification import _deadline_from_metadata, _format_event_date
from app.models.SpeakerProfile import SpeakerProfileModel
from app.services.Opportunity import OpportunityService

logger = logging.getLogger(__name__)


def _opportunity_row_for_template(opp: dict) -> dict[str, str]:
    return {
        "title": (opp.get("event_name") or opp.get("title") or "").strip(),
        "date": _format_event_date(opp),
        "location": (opp.get("location") or "").strip(),
        "deadline": _deadline_from_metadata(opp),
        "url": opportunity_action_url(opp),
    }


class MatchedOpportunitiesEmailService:
    """Sends matched opportunities after a match run (Postmark New_opportunity template)."""

    def __init__(
        self,
        opportunity_service: OpportunityService = None,
        speaker_profile_model: SpeakerProfileModel = None,
    ):
        self.opportunity_service = opportunity_service or OpportunityService()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()

    async def send_matched_opportunities_email(
        self,
        speaker_profile_id: str,
        opportunity_documents: Optional[List[dict]] = None,
    ) -> bool:
        """
        Build template model { user_name, opportunities: [{ title, date, location, deadline, url }, ...] }
        and send via EmailService (ALERT_NEW_OPPORTUNITY).

        If `opportunity_documents` is provided (e.g. right after a match run), use it; otherwise load from
        matchedOpportunities (status must be completed).

        Recipient: `email` on the speaker profile document.
        """
        from app.dependencies import get_email_service

        profile = await self.speaker_profile_model.get_profile(speaker_profile_id)
        if not profile:
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

        user_name = (profile.get("full_name") or "").strip() or "there"
        rows = [_opportunity_row_for_template(o) for o in opportunities]
        to_email = speaker_profile_notification_email(profile)
        if not to_email:
            return False

        try:
            return get_email_service().send_event_email(
                event_type=EmailEventType.ALERT_NEW_OPPORTUNITY,
                to_email=to_email,
                template_model={
                    "user_name": user_name,
                    "opportunities": rows,
                },
            )
        except Exception as e:
            logger.warning("Failed to send matched opportunities email: %s", e)
            return False
