"""
Pydantic schemas for Speaker Profile onboarding: init, verify-step, and final save.
"""
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from typing import Any, List, Literal, Optional, Union


# --- Talk description (structured; stored as profile.talk_description) ---

class TalkDescriptionObject(BaseModel):
    """LLM-derived title and overview from the talk-description step."""
    title: str = ""
    overview: str = ""


# --- Past speaking example (structured; stored in profile.past_speaking_examples) ---

class PastSpeakingExampleItem(BaseModel):
    """One past engagement: organization, optional event name, and date only (no topics/audience)."""
    model_config = ConfigDict(extra="ignore")

    organization_name: str = ""
    event_name: str = ""
    date_month_year: str = Field(default="", description="e.g. March 2024")

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_string(cls, data: Any):
        if isinstance(data, str):
            return {
                "organization_name": data.strip(),
                "event_name": "",
                "date_month_year": "",
            }
        return data


class ProfessionalMembershipItem(BaseModel):
    """One professional membership or affiliation (stored as JSON objects in MongoDB)."""
    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", description="Credential or membership title (e.g. Certified Member)")
    organization: str = Field(default="", description="Professional body or organization name")
    start_date: Optional[str] = Field(default=None, description="Membership start date (e.g. 2024-01-01)")
    end_date: Optional[str] = Field(default=None, description="Membership end date; null when currently active")
    is_current: Optional[bool] = Field(default=None, description="Whether the membership is currently active")

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_string(cls, data: Any):
        if isinstance(data, str):
            s = data.strip()
            return {"title": "", "organization": s, "start_date": None, "end_date": None, "is_current": None}
        return data


def _coerce_professional_memberships_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, list):
        out: List[ProfessionalMembershipItem] = []
        for x in v:
            if isinstance(x, ProfessionalMembershipItem):
                out.append(x)
            elif isinstance(x, dict):
                out.append(
                    ProfessionalMembershipItem(
                        title=str(x.get("title") or "").strip(),
                        organization=str(x.get("organization") or "").strip(),
                        start_date=str(x.get("start_date") or "").strip() or None,
                        end_date=str(x.get("end_date") or "").strip() or None,
                        is_current=x.get("is_current") if x.get("is_current") is not None else None,
                    )
                )
            elif isinstance(x, str) and x.strip():
                out.append(ProfessionalMembershipItem(organization=x.strip()))
        # Drop rows that are entirely empty
        kept = [
            m
            for m in out
            if (
                str(m.title).strip()
                or str(m.organization).strip()
                or str(m.start_date or "").strip()
                or str(m.end_date or "").strip()
                or m.is_current is not None
            )
        ]
        return kept or None
    return v


def _coerce_talk_description_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, TalkDescriptionObject):
        return v
    if isinstance(v, dict):
        return TalkDescriptionObject(
            title=str(v.get("title") or "").strip(),
            overview=str(v.get("overview") or "").strip(),
        )
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        first_line = s.split("\n", 1)[0].strip()
        rest = s[len(first_line) :].lstrip() if "\n" in s else ""
        overview = rest if rest else s
        return TalkDescriptionObject(title=first_line[:300], overview=overview[:2000])
    return v


def _coerce_string_list_field(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else None
    if isinstance(v, list):
        out = [str(x).strip() for x in v if str(x).strip()]
        return out or None
    return v


# --- Topic item (from speakerTopics collection; stored in profile.topics) ---

class SpeakerTopicItem(BaseModel):
    """One topic option or selected topic (_id, name, slug). API/DB use '_id'; Pydantic field is 'id'."""
    id: str = Field(..., alias="_id")
    name: str = ""
    slug: str = ""
    type: Optional[str] = None 
    class Config:
        populate_by_name = True


# --- Target audience item (from speakerTargetAudeince collection; stored in profile.target_audiences) ---

class SpeakerTargetAudienceItem(BaseModel):
    """One target audience option or selected audience (_id, name, slug). API/DB use '_id'; Pydantic field is 'id'."""
    id: str = Field(..., alias="_id")
    name: str = ""
    slug: str = ""
    type: Optional[str] = None 

    class Config:
        populate_by_name = True


# --- POST /init response (first step metadata) ---

class InitStepResponse(BaseModel):
    """First step returned by POST /init."""
    step_name: str
    form_type: str
    question: str
    allowed_values: Optional[List[Any]] = None  # List[str] for enum steps; List[SpeakerTopicItem] for topics
    multi_select: bool = False


# --- POST /verify-step request ---

class VerifyStepRequest(BaseModel):
    """Request body for POST /verify-step."""
    step: str = Field(..., description="Current step name")
    answer: Union[str, List[str], List[dict]] = Field(..., description="User answer (string, list of strings, or for topics selection: list of topic objects)")
    source: Literal["selection", "text"] = Field(..., description="'selection' or 'text'")
    retry_count: int = Field(0, ge=0, description="How many times user has retried this step")
    profile_id: Optional[str] = Field(None, description="Set after first valid step (full_name); required for subsequent steps")
    user_id: Optional[str] = Field(None, description="Logged-in user id; when present, linked to profile on create (full_name step)")


# --- POST /verify-step response (chat-style) ---

class NextStepPayload(BaseModel):
    """Next step metadata (next_step or repeat_step)."""
    step_name: str
    form_type: str
    question: str
    allowed_values: Optional[List[Any]] = None  # List[str] for enum steps; list of topic objects for topics step
    multi_select: bool = False


# Success: assistant_message, normalized_answer, next_step, is_last_step, optional profile_id
class VerifyStepSuccessResponse(BaseModel):
    """Chat-style success response."""
    assistant_message: str
    normalized_answer: Union[str, List[str], List[dict], dict]  # dict for social URLs step; topic objects when step is topics
    next_step: dict  # NextStepPayload shape
    is_last_step: bool
    profile_id: Optional[str] = None  # Present after first valid step; FE should store and send on subsequent steps


# Failure: assistant_message, repeat_step (conversational tone, no technical errors)
class VerifyStepInvalidResponse(BaseModel):
    """Chat-style validation failure; same step is repeated."""
    assistant_message: str
    repeat_step: dict  # NextStepPayload shape


# --- POST /speaker-profile request (full profile body) ---

class SpeakerProfileCreateSchema(BaseModel):
    """Full speaker profile payload for POST /speaker-profile."""
    full_name: str = Field(..., min_length=1)
    professional_title: Optional[str] = Field(default=None, description="Job title as used professionally")
    company: Optional[str] = Field(default=None, description="Company or organization name")
    email: str = Field(..., min_length=1)
    topics: List[SpeakerTopicItem] = Field(..., min_length=1)  # array of topic objects from speakerTopics
    speaking_formats: List[str] = Field(...)
    delivery_mode: List[str] = Field(...)
    linkedin_url: str = Field(...)  # validated as URL in service or via HttpUrl
    past_speaking_examples: Optional[List[PastSpeakingExampleItem]] = Field(default=None)
    video_links: List[str] = Field(...)
    talk_description: Union[str, TalkDescriptionObject] = Field(...)
    key_takeaways: Optional[Union[str, List[str]]] = Field(default=None)
    target_audiences: List[SpeakerTargetAudienceItem] = Field(..., min_length=1)  # array of audience objects from speakerTargetAudeince
    # Optional fields editable after profile creation (not part of verify-step)
    name_salutation: Optional[str] = Field(default=None, description="E.g. Mr, Dr., Mrs., Ms.")
    bio: Optional[str] = Field(default=None, description="Speaker bio (text area)")
    twitter: Optional[str] = Field(default=None, description="Twitter URL or handle")
    facebook: Optional[str] = Field(default=None, description="Facebook URL")
    instagram: Optional[str] = Field(default=None, description="Instagram URL or handle")
    address_city: Optional[str] = Field(default=None, description="City")
    address_state: Optional[str] = Field(default=None, description="State/Region")
    address_country: Optional[str] = Field(default=None, description="Country")
    phone_country_code: Optional[str] = Field(default=None, description="Phone country code (e.g. +1, +44, +91)")
    phone_number: Optional[str] = Field(default=None, description="Phone number (without country code)")
    professional_memberships: Optional[List[ProfessionalMembershipItem]] = Field(
        default=None,
        description="Professional memberships: title, organization, start_date, end_date, and is_current per row (JSON objects in DB)",
    )
    preferred_speaking_time: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="One or more of: 10-minute, 20-minute, 30-minute, 40-minute, 1 hour",
    )
    testimonial: Optional[Union[str, List[str]]] = Field(default=None, description="Testimonials as strings or list of quotes")
    profile_picture: Optional[str] = Field(default=None, description="URL or path to profile image")
    headshot_picture: Optional[str] = Field(default=None, description="URL or path to headshot image")

    @field_validator("talk_description", mode="before")
    @classmethod
    def _v_talk_description(cls, v: Any) -> Any:
        return _coerce_talk_description_value(v)

    @field_validator("professional_memberships", mode="before")
    @classmethod
    def _v_professional_memberships(cls, v: Any) -> Any:
        return _coerce_professional_memberships_value(v)

    @field_validator("key_takeaways", "testimonial", mode="before")
    @classmethod
    def _v_str_list_fields(cls, v: Any) -> Any:
        return _coerce_string_list_field(v)

    @field_validator("preferred_speaking_time", mode="before")
    @classmethod
    def _v_preferred_speaking_time(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            return out or None
        if isinstance(v, str) and v.strip():
            return v.strip()
        return v

    @model_validator(mode="after")
    def _require_talk_description_nonempty(self) -> "SpeakerProfileCreateSchema":
        td = self.talk_description
        if isinstance(td, TalkDescriptionObject):
            if not str(td.title).strip() and not str(td.overview).strip():
                raise ValueError("talk_description must include a title or overview")
        elif isinstance(td, str) and not td.strip():
            raise ValueError("talk_description is required")
        return self


# --- PUT /speaker-profile/{profile_id} request (partial update; all fields optional) ---

class SpeakerProfileUpdateSchema(BaseModel):
    """Request body for PUT /speaker-profile/{profile_id}. All fields optional; only provided fields are updated."""
    full_name: Optional[str] = Field(default=None, min_length=1)
    professional_title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = Field(default=None, min_length=1)
    topics: Optional[List[SpeakerTopicItem]] = Field(default=None, min_length=1)
    speaking_formats: Optional[List[str]] = Field(default=None)
    delivery_mode: Optional[List[str]] = Field(default=None)
    linkedin_url: Optional[str] = Field(default=None)
    past_speaking_examples: Optional[List[PastSpeakingExampleItem]] = Field(default=None)
    video_links: Optional[List[str]] = Field(default=None)
    talk_description: Optional[Union[str, TalkDescriptionObject]] = Field(default=None)
    key_takeaways: Optional[Union[str, List[str]]] = Field(default=None)
    target_audiences: Optional[List[SpeakerTargetAudienceItem]] = Field(default=None, min_length=1)
    name_salutation: Optional[str] = None
    bio: Optional[str] = None
    twitter: Optional[str] = None
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_country: Optional[str] = None
    geography_preferences: Optional[List[str]] = Field(
        default=None,
        description=(
            "Preferred speaking geographies. Known values include US regions "
            "('Northeast', 'Southeast', 'Midwest', 'West Coast', 'Anywhere in USA') "
            "and international options ('International – Virtual only', "
            "'International – In-Person only'). Geography gates apply only to "
            "strictly in-person opportunities."
        ),
    )
    phone_country_code: Optional[str] = None
    phone_number: Optional[str] = None
    professional_memberships: Optional[List[ProfessionalMembershipItem]] = None
    preferred_speaking_time: Optional[Union[str, List[str]]] = None
    testimonial: Optional[Union[str, List[str]]] = None
    profile_picture: Optional[str] = None
    headshot_picture: Optional[str] = None
    bio_document_url: Optional[str] = Field(
        default=None,
        description="URL to optional bio document (PDF, DOC, DOCX) uploaded via /api/v1/common/upload-file",
    )

    @field_validator("talk_description", mode="before")
    @classmethod
    def _v_talk_description_u(cls, v: Any) -> Any:
        return _coerce_talk_description_value(v)

    @field_validator("professional_memberships", mode="before")
    @classmethod
    def _v_professional_memberships_u(cls, v: Any) -> Any:
        return _coerce_professional_memberships_value(v)

    @field_validator("key_takeaways", "testimonial", mode="before")
    @classmethod
    def _v_str_list_fields_u(cls, v: Any) -> Any:
        return _coerce_string_list_field(v)

    @field_validator("preferred_speaking_time", mode="before")
    @classmethod
    def _v_preferred_speaking_time_u(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            return out or None
        if isinstance(v, str) and v.strip():
            return v.strip()
        return v

    class Config:
        populate_by_name = True


# --- POST /speaker-profile/create-speaker-profile request (form-style create; all fields optional like update) ---

class SpeakerProfileCreateFormSchema(BaseModel):
    """Form-style payload for creating a new speaker profile without conversational onboarding."""
    full_name: str = Field(..., min_length=2, max_length=50)
    professional_title: Optional[str] = None
    company: Optional[str] = None
    email: EmailStr = Field(...)
    user_id: Optional[str] = Field(
        default=None,
        description="If set, link the profile to this existing user; otherwise a new user is created.",
    )
    topics: Optional[List[SpeakerTopicItem]] = Field(default=None, min_length=1)
    speaking_formats: Optional[List[str]] = Field(default=None)
    delivery_mode: Optional[List[str]] = Field(default=None)
    linkedin_url: Optional[str] = Field(default=None)
    past_speaking_examples: Optional[List[PastSpeakingExampleItem]] = Field(default=None)
    video_links: Optional[List[str]] = Field(default=None)
    talk_description: Optional[Union[str, TalkDescriptionObject]] = Field(default=None)
    key_takeaways: Optional[Union[str, List[str]]] = Field(default=None)
    target_audiences: Optional[List[SpeakerTargetAudienceItem]] = Field(default=None, min_length=1)
    name_salutation: Optional[str] = None
    bio: Optional[str] = None
    twitter: Optional[str] = None
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_country: Optional[str] = None
    geography_preferences: Optional[List[str]] = Field(
        default=None,
        description=(
            "Preferred speaking geographies. Known values include US regions "
            "('Northeast', 'Southeast', 'Midwest', 'West Coast', 'Anywhere in USA') "
            "and international options ('International – Virtual only', "
            "'International – In-Person only'). Geography gates apply only to "
            "strictly in-person opportunities."
        ),
    )
    phone_country_code: Optional[str] = None
    phone_number: Optional[str] = None
    professional_memberships: Optional[List[ProfessionalMembershipItem]] = None
    preferred_speaking_time: Optional[Union[str, List[str]]] = None
    testimonial: Optional[Union[str, List[str]]] = None
    profile_picture: Optional[str] = None
    headshot_picture: Optional[str] = None
    bio_document_url: Optional[str] = Field(
        default=None,
        description="URL to optional bio document (PDF, DOC, DOCX) uploaded via /api/v1/common/upload-file",
    )

    @field_validator("talk_description", mode="before")
    @classmethod
    def _v_talk_description_f(cls, v: Any) -> Any:
        return _coerce_talk_description_value(v)

    @field_validator("professional_memberships", mode="before")
    @classmethod
    def _v_professional_memberships_f(cls, v: Any) -> Any:
        return _coerce_professional_memberships_value(v)

    @field_validator("key_takeaways", "testimonial", mode="before")
    @classmethod
    def _v_str_list_fields_f(cls, v: Any) -> Any:
        return _coerce_string_list_field(v)

    @field_validator("preferred_speaking_time", mode="before")
    @classmethod
    def _v_preferred_speaking_time_f(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            return out or None
        if isinstance(v, str) and v.strip():
            return v.strip()
        return v

    class Config:
        populate_by_name = True
