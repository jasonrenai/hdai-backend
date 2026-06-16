import json
import os
from typing import List

from openai import OpenAI

from app.models.ApplicationContent import ApplicationContentModel
from app.models.Opportunity import OpportunityModel
from app.models.SpeakerProfile import SpeakerProfileModel

_SYSTEM_PROMPT_SKIP_PLACEHOLDER_DATA = (
    "If any field in the provided data is missing, empty, or looks like generic placeholder text "
    "(for example 'key takeaway 1' / 'key takeaway 2'), do not mention, list, or build on it—"
    "use only clearly real, specific details and keep the copy natural. "
    "Strictly ignore any content in the speaker profile that is clearly for testing or QA "
    "(for example text about 'test', 'testing', dummy/sample/lorem-style filler, or obvious sandbox data)—"
    "never quote it or build the application on it."
)

_SYSTEM_PROMPT_APPLICATION = (
    "You are an expert executive communications assistant. "
    "Generate call-for-speakers application copy for a speaker applying to speak at an event opportunity. "
    "Generate all fields in speaker voice for speaker profile application content. "
    "Write abstract, takeaways, presentation_type, speaking_history, and an opportunity-adapted bio the speaker can "
    "paste directly into the submission form. "
    "Tailor the copy to the opportunity's event theme, audience, topics, speaking format, and delivery mode. "
    "Align presentation_type with the opportunity speaking format when possible. "
    "The copy should be concise, specific, and persuasive while sounding natural and human. "
    "Do not use placeholders such as [Name] or bracketed optional fields. "
    f"{_SYSTEM_PROMPT_SKIP_PLACEHOLDER_DATA} "
    "Return ONLY strict JSON with keys: presentation_type, abstract, takeaways, speaking_history, bio. "
    "takeaways must be a JSON array of strings."
)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(parts)
    return str(value).strip()


def _display_name(profile: dict) -> str:
    salutation = str(profile.get("name_salutation") or "").strip()
    full_name = str(profile.get("full_name") or "").strip()
    if salutation and full_name:
        return f"{salutation} {full_name}"
    return full_name


def _profile_static_fields(profile: dict) -> dict:
    talk_description = profile.get("talk_description")
    talk_title = ""
    if isinstance(talk_description, dict):
        talk_title = _as_text(talk_description.get("title"))
    return {
        "name": _display_name(profile),
        "title": _as_text(profile.get("professional_title")),
        "company": _as_text(profile.get("company")),
        "email": _as_text(profile.get("email")),
        "session_title": talk_title,
    }


class OpportunityApplicationContentService:
    def __init__(
        self,
        application_content_model: ApplicationContentModel = None,
        speaker_profile_model: SpeakerProfileModel = None,
        opportunity_model: OpportunityModel = None,
    ):
        self.application_content_model = application_content_model or ApplicationContentModel()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.opportunity_model = opportunity_model or OpportunityModel()

    async def generate_and_save_application_content(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
    ) -> dict:
        if not self.application_content_model.is_valid_object_id(speaker_profile_id):
            raise ValueError("Invalid speaker_profile_id")
        if not self.application_content_model.is_valid_object_id(opportunity_id):
            raise ValueError("Invalid opportunity_id")

        profile = await self.speaker_profile_model.get_profile(speaker_profile_id)
        if not profile:
            raise ValueError("Speaker profile not found")

        opportunity = await self.opportunity_model.get_by_id(opportunity_id)
        if not opportunity:
            raise ValueError("Opportunity not found")

        static_fields = _profile_static_fields(profile)
        generated = self._generate_application_from_llm(
            profile,
            opportunity,
        )

        if not static_fields.get("session_title"):
            raise ValueError("Speaker profile is missing a talk title")

        created = await self.application_content_model.create(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
            **static_fields,
            **generated,
        )
        return created

    async def list_application_content(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
        page: int,
        limit: int,
    ) -> dict:
        if not self.application_content_model.is_valid_object_id(speaker_profile_id):
            raise ValueError("Invalid speaker_profile_id")
        if not self.application_content_model.is_valid_object_id(opportunity_id):
            raise ValueError("Invalid opportunity_id")

        skip = (page - 1) * limit
        items = await self.application_content_model.get_list_by_speaker_and_opportunity(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
            skip=skip,
            limit=limit,
        )
        total = await self.application_content_model.count_by_speaker_and_opportunity(
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

    def _generate_application_from_llm(
        self,
        profile: dict,
        opportunity: dict,
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
            "bio": profile.get("bio"),
            "speaking_formats": profile.get("speaking_formats", []),
            "past_speaking_examples": profile.get("past_speaking_examples", []),
            "talk_description": profile.get("talk_description", {}),
            "key_takeaways": profile.get("key_takeaways", []),
            "topics": profile.get("topics", []),
            "target_audiences": profile.get("target_audiences", []),
        }
        bio_document_summary = _as_text(profile.get("bio_document_summary"))
        if bio_document_summary:
            speaker_payload["bio_document_summary"] = bio_document_summary

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

        system_prompt = _SYSTEM_PROMPT_APPLICATION

        user_prompt = (
            "Generate call-for-speakers application copy for this speaker and opportunity.\n"
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

        presentation_type = _as_text(payload.get("presentation_type"))
        abstract = _as_text(payload.get("abstract"))
        takeaways = self._normalize_takeaways(payload.get("takeaways"))
        speaking_history = _as_text(payload.get("speaking_history"))
        bio = _as_text(payload.get("bio"))

        if not all([presentation_type, abstract, takeaways, speaking_history, bio]):
            raise ValueError("Failed to generate valid application content")

        return {
            "presentation_type": presentation_type,
            "abstract": abstract,
            "takeaways": takeaways,
            "speaking_history": speaking_history,
            "bio": bio,
        }

    @staticmethod
    def _normalize_takeaways(value) -> List[str]:
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return items
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

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
