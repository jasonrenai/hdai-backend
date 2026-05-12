"""Class-based service to send matched opportunities to a speaker's email via Postmark."""

import os
import re
from typing import List

from postmarker.core import PostmarkClient

from app.email.enums import SenderType
from app.email.helpers import resolve_sender_email
from app.models.SpeakerProfile import SpeakerProfileModel
from app.services.Opportunity import OpportunityService


class MatchedOpportunitiesEmailService:
    """Sends matched opportunities (event_name + link) to the speaker's email from their profile."""

    def __init__(
        self,
        opportunity_service: OpportunityService = None,
        speaker_profile_model: SpeakerProfileModel = None,
    ):
        self.opportunity_service = opportunity_service or OpportunityService()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()

    def _get_postmark_client(self) -> PostmarkClient | None:
        """Build Postmark client from env. Returns None if config missing."""
        token = os.getenv("POSTMARK-SERVER-API-TOKEN", None) 
        if not token:
            return None
        return PostmarkClient(token)

    def _get_from_email(self) -> str:
        """From address: EMAIL_FROM_ALERTS or default alerts@speakerpitcher.ai."""
        return resolve_sender_email(SenderType.ALERTS)

    LOGO_URL = "https://kind-cliff-0e3c6e210.6.azurestaticapps.net/assets/images/logo.png"
    BRAND_NAME = "HD AI"

    def _build_html_body(self, opportunities: List[dict], full_name: str = "") -> str:
        """Build HTML email body with HD AI branding, logo and markdown-style content (event_name + link per opportunity)."""
        full_name = (full_name or "").strip()
        greeting = f"Hi {full_name}," if full_name else "Hi,"
        # Markdown-style structure rendered as HTML: heading + list of event_name and link
        rows = []
        for opp in opportunities:
            event_name = (opp.get("event_name") or "").strip() or "Opportunity"
            link = (opp.get("link") or "").strip()
            if link:
                rows.append(f"- **{event_name}** — [Open]({link})")
            else:
                rows.append(f"- **{event_name}**")
        markdown_list = "\n".join(rows)
        # Convert simple markdown to HTML (bold **x** -> <strong>, [text](url) -> <a>)
        list_html_parts = []
        for line in markdown_list.split("\n"):
            line = line.strip()
            if not line or not line.startswith("- "):
                continue
            line = line[2:]  # drop "- "
            line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
            list_html_parts.append(f"<li>{line}</li>")
        list_html = "\n".join(list_html_parts)
        return f"""<html>
<body style="font-family: sans-serif; color: #333;">
<div style="margin-bottom: 24px;">
  <p><img src="{self.LOGO_URL}" alt="{self.BRAND_NAME} Logo" style="max-width:200px;height:auto;" /></p>
  <p style="font-size: 18px; font-weight: 600; color: #111;">{self.BRAND_NAME}</p>
</div>
<p>{greeting}</p>
<p>{self.BRAND_NAME} has matched the following speaking opportunities for you.</p>
<h2 style="font-size: 16px; margin-top: 24px;">Your matched speaking opportunities</h2>
<ul>
{list_html}
</ul>
<hr style="border: none; border-top: 1px solid #eee; margin: 28px 0 16px;" />
<p style="font-size: 12px; color: #666;">This email was sent by <strong>{self.BRAND_NAME}</strong>. You received it because your speaker profile is set up with us.</p>
</body>
</html>"""

    async def send_matched_opportunities_email(self, speaker_profile_id: str) -> bool:
        """
        Get email from speaker profile, fetch matched opportunities, send one email
        with event_name and link per opportunity via Postmark. Uses POSTMARK-SERVER-API-TOKEN
        from env; From is EMAIL_FROM_ALERTS or alerts@speakerpitcher.ai.
        Returns True if email was sent, False otherwise (missing profile/email, no opportunities, or Postmark failure).
        """
        profile = await self.speaker_profile_model.get_profile(speaker_profile_id)
        if not profile:
            return False
        to_email = (profile.get("email") or "").strip()
        if not to_email:
            return False
        opportunities, status = await self.opportunity_service.get_matched_opportunities_by_speaker_id(
            speaker_profile_id
        )
        if status != "completed" or not opportunities:
            return False
        client = self._get_postmark_client()
        if not client:
            return False
        from_email = self._get_from_email()
        full_name = (profile.get("full_name") or "").strip()
        html_body = self._build_html_body(opportunities, full_name=full_name)
        try:
            client.emails.send(
                From=from_email,
                To=to_email,
                Subject=f"{self.BRAND_NAME} — Your matched speaking opportunities",
                HtmlBody=html_body,
            )
            return True
        except Exception:
            return False
