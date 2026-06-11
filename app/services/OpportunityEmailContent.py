import json
import os
from typing import Optional

from openai import OpenAI

from app.email.pitch_ready_notification import try_send_pitch_ready_email_after_content_created
from app.models.EmailContent import EmailContentModel
from app.models.Opportunity import OpportunityModel
from app.models.SpeakerProfile import SpeakerProfileModel
from app.schemas.Opportunity import EmailAuthorityType

# Shared for all authority prompts: avoid echoing empty, placeholder, or test profile data.
_SYSTEM_PROMPT_SKIP_PLACEHOLDER_DATA = (
    "If any field in the provided data is missing, empty, or looks like generic placeholder text "
    "(for example 'key takeaway 1' / 'key takeaway 2'), do not mention, list, or build on it—"
    "use only clearly real, specific details and keep the email natural. "
    "Strictly ignore any content in the speaker profile that is clearly for testing or QA "
    "(for example text about 'test', 'testing', dummy/sample/lorem-style filler, or obvious sandbox data)—"
    "never quote it or build the pitch on it."
)

_SYSTEM_PROMPT_ASSOCIATION_MEMBERSHIP = (
    "You are an expert executive communications assistant. "
    "Generate a professional outreach email for a speaker applying to speak at an event opportunity. "
    "Use the ASSOCIATION / MEMBERSHIP AUTHORITY angle: credibility comes from belonging to the same "
    "professional world as the organizer and audience — shared industry, peer community, or member ecosystem. "
    "Emphasize alignment with the association's mission, member value, and collective professional identity; "
    "sound like an insider peer, not a distant vendor. "
    "Best suited tone for industry associations, professional societies, and member-driven organizations "
    "(e.g. PRSA, IABC-style contexts). "
    "The email should be concise, warm, and persuasive while sounding natural and human. "
    f"{_SYSTEM_PROMPT_SKIP_PLACEHOLDER_DATA} "
    "Return ONLY strict JSON with keys: mail_title, mail_content."
)

_SYSTEM_PROMPT_EXPERIENCE_EXPERTISE = (
    "You are an expert executive communications assistant. "
    "Generate a professional outreach email for a speaker applying to speak at an event opportunity. "
    "Use the EXPERIENCE / EXPERTISE AUTHORITY angle: credibility comes from having done this work at scale — "
    "senior roles, high-stakes forums, repeat delivery, and depth of practice. "
    "Emphasize track record, relevance to strategic and leadership audiences, and command of the topic "
    "without sounding boastful. "
    "Best suited tone for corporate conferences, leadership summits, and marketing or innovation events. "
    "The email should be concise, relevant, and persuasive while sounding natural and human. "
    f"{_SYSTEM_PROMPT_SKIP_PLACEHOLDER_DATA} "
    "Return ONLY strict JSON with keys: mail_title, mail_content."
)

_SYSTEM_PROMPT_CASE_STUDY_RESULTS = (
    "You are an expert executive communications assistant. "
    "Generate a professional outreach email for a speaker applying to speak at an event opportunity. "
    "Use the CASE STUDY / RESULTS AUTHORITY angle: credibility comes from proof — measurable outcomes, "
    "before-and-after impact, concrete examples, and tactics the audience can apply. "
    "Emphasize evidence, clarity of results, and practical takeaways for performance-driven attendees. "
    "Best suited tone for tactical conferences, workshops, and audiences who care about execution and ROI. "
    "The email should be concise, specific, and persuasive while sounding natural and human. "
    f"{_SYSTEM_PROMPT_SKIP_PLACEHOLDER_DATA} "
    "Return ONLY strict JSON with keys: mail_title, mail_content."
)


def _system_prompt_for_authority_type(authority_type: EmailAuthorityType) -> str:
    if authority_type == "association_membership":
        return _SYSTEM_PROMPT_ASSOCIATION_MEMBERSHIP
    if authority_type == "experience_expertise":
        return _SYSTEM_PROMPT_EXPERIENCE_EXPERTISE
    return _SYSTEM_PROMPT_CASE_STUDY_RESULTS


def _build_email_submission_response_fields(opportunity: dict) -> dict:
    meta = opportunity.get("metadata") if isinstance(opportunity.get("metadata"), dict) else {}
    submission_info = (
        opportunity.get("submissionInfo") if isinstance(opportunity.get("submissionInfo"), dict) else {}
    )

    submission_email = (
        str(submission_info.get("submissionEmail") or "").strip()
        or str(meta.get("submission_email") or "").strip()
    )
    contact_email = (
        str(submission_info.get("contactEmail") or "").strip()
        or str(meta.get("contact_email") or "").strip()
    )
    recipient_email = submission_email or contact_email

    requires_email_submission = bool(submission_email)
    submission_note = ""
    if requires_email_submission:
        submission_note = f"This opportunity requires an email submission to {submission_email}."

    return {
        "recipient_email": recipient_email,
        "event_contact": contact_email,
        "requires_email_submission": requires_email_submission,
        "submission_note": submission_note,
    }


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
        authority_type: EmailAuthorityType = "experience_expertise",
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

        generated = self._generate_email_from_llm(
            profile,
            opportunity,
            user_suggestion_prompt,
            authority_type=authority_type,
        )
        submission_fields = _build_email_submission_response_fields(opportunity)
        created = await self.email_content_model.create(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
            mail_title=generated["mail_title"],
            mail_content=generated["mail_content"],
            **submission_fields,
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
        opportunity = None
        for item in items:
            event_contact = item.get("event_contact")
            if event_contact is None or isinstance(event_contact, dict):
                if opportunity is None:
                    opportunity = await self.opportunity_model.get_by_id(opportunity_id)
                if opportunity:
                    item.update(_build_email_submission_response_fields(opportunity))
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
        authority_type: EmailAuthorityType = "experience_expertise",
    ) -> dict:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

        client = OpenAI(api_key=api_key)

        speaker_payload = {
            "full_name": profile.get("full_name"),
            "professional_title": profile.get("professional_title"),
            "company": profile.get("company"),
            "email": profile.get("email"),
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

        system_prompt = _system_prompt_for_authority_type(authority_type)
        if user_suggestion_prompt and user_suggestion_prompt.strip():
            system_prompt += f" Additional user instruction: {user_suggestion_prompt.strip()}"
        speaker_email = str(profile.get("email") or "").strip()
        if speaker_email:
            system_prompt += (
                " Include the speaker profile email in the generated mail_content naturally, "
                "typically in the closing/signature, using this exact email when provided."
            )

        user_prompt = (
            "Generate an email subject and body.\n"
            f"Speaker profile data: {json.dumps(speaker_payload, default=str)}\n"
            f"Opportunity data: {json.dumps(opportunity_payload, default=str)}\n"
            f"Speaker profile email: {speaker_email}"
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
