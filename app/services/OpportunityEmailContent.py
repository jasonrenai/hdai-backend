import json
import os
from typing import Optional

from openai import OpenAI

from app.email.pitch_ready_notification import try_send_pitch_ready_email_after_content_created
from app.models.EmailContent import EmailContentModel
from app.models.Opportunity import OpportunityModel
from app.models.SpeakerProfile import SpeakerProfileModel


class OpportunityEmailContentService:
    def __init__(
        self,
        email_content_model: EmailContentModel = None,
        speaker_profile_model: SpeakerProfileModel = None,
        opportunity_model: OpportunityModel = None,
    ):
        self.email_content_model = email_content_model or EmailContentModel()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.opportunity_model = opportunity_model or OpportunityModel()

    async def generate_and_save_email_content(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
        user_suggestion_prompt: Optional[str] = None,
    ) -> dict:
        if not self.email_content_model.is_valid_object_id(speaker_profile_id):
            raise ValueError("Invalid speaker_profile_id")
        if not self.email_content_model.is_valid_object_id(opportunity_id):
            raise ValueError("Invalid opportunity_id")

        profile = await self.speaker_profile_model.get_profile(speaker_profile_id)
        if not profile:
            raise ValueError("Speaker profile not found")

        opportunity = await self.opportunity_model.get_by_id(opportunity_id)
        if not opportunity:
            raise ValueError("Opportunity not found")

        generated = self._generate_email_from_llm(profile, opportunity, user_suggestion_prompt)
        created = await self.email_content_model.create(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
            mail_title=generated["mail_title"],
            mail_content=generated["mail_content"],
        )

        try_send_pitch_ready_email_after_content_created(
            profile=profile,
            opportunity=opportunity,
            opportunity_id=opportunity_id,
            speaker_profile_id=speaker_profile_id,
            email_content_id=str(created.get("_id") or ""),
        )

        return created

    async def list_email_content(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
        page: int,
        limit: int,
    ) -> dict:
        if not self.email_content_model.is_valid_object_id(speaker_profile_id):
            raise ValueError("Invalid speaker_profile_id")
        if not self.email_content_model.is_valid_object_id(opportunity_id):
            raise ValueError("Invalid opportunity_id")

        skip = (page - 1) * limit
        items = await self.email_content_model.get_list_by_speaker_and_opportunity(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
            skip=skip,
            limit=limit,
        )
        total = await self.email_content_model.count_by_speaker_and_opportunity(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
        )
        return {
            "items": items,
            "total": total,
            "page": page,
            "limit": limit,
            "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
        }

    def _generate_email_from_llm(
        self,
        profile: dict,
        opportunity: dict,
        user_suggestion_prompt: Optional[str],
    ) -> dict:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

        client = OpenAI(api_key=api_key)

        speaker_payload = {
            "full_name": profile.get("full_name"),
            "professional_title": profile.get("professional_title"),
            "company": profile.get("company"),
            "past_speaking_examples": profile.get("past_speaking_examples", []),
            "talk_description": profile.get("talk_description", {}),
            "key_takeaways": profile.get("key_takeaways", []),
            "topics": profile.get("topics", []),
            "target_audiences": profile.get("target_audiences", []),
        }

        opportunity_payload = {
            "_id": str(opportunity.get("_id")),
            "event_name": opportunity.get("event_name"),
            "location": opportunity.get("location"),
            "start_date": opportunity.get("start_date"),
            "end_date": opportunity.get("end_date"),
            "speaking_format": opportunity.get("speaking_format"),
            "delivery_mode": opportunity.get("delivery_mode"),
            "target_audiences": opportunity.get("target_audiences", []),
            "topics": opportunity.get("topics", []),
            "metadata_description": (opportunity.get("metadata") or {}).get("description"),
            "link": opportunity.get("link"),
        }

        system_prompt = (
            "You are an expert executive communications assistant. "
            "Generate a professional outreach email for a speaker applying to speak at an event opportunity. "
            "The email should be concise, relevant, and persuasive while sounding natural and human. "
            "Use speaker experience and talk content to align with the opportunity. "
            "Return ONLY strict JSON with keys: mail_title, mail_content."
        )
        if user_suggestion_prompt and user_suggestion_prompt.strip():
            system_prompt += f" Additional user instruction: {user_suggestion_prompt.strip()}"

        user_prompt = (
            "Generate an email subject and body.\n"
            f"Speaker profile data: {json.dumps(speaker_payload, default=str)}\n"
            f"Opportunity data: {json.dumps(opportunity_payload, default=str)}"
        )

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout=30,
        )
        raw = (completion.choices[0].message.content or "").strip()
        payload = self._parse_generated_json(raw)
        mail_title = (payload.get("mail_title") or "").strip()
        mail_content = (payload.get("mail_content") or "").strip()
        if not mail_title or not mail_content:
            raise ValueError("Failed to generate valid email title/content")

        return {"mail_title": mail_title, "mail_content": mail_content}

    @staticmethod
    def _parse_generated_json(raw: str) -> dict:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    return {}
            return {}
