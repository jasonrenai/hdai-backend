"""
Structured question metadata for the speaker onboarding agent.

Built from speaker_profile_chatbot_steps + live catalog options.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.speaker_profile_chatbot_steps import (
    CATALOG_STEPS,
    CREATE_STEP,
    POST_CREATE_STEP_ORDER,
    PRE_CREATE_ASK_IDENTITY,
    PRE_CREATE_POST_WELCOME,
    PRE_CREATE_PROMPT_WELCOME,
    PRE_CREATE_READY,
    SKIPPABLE_STEPS,
    STEP_QUESTIONS,
    STEP_UPSERT_FIELDS,
    _PREFERRED_SPEAKING_TIMES,
    _QUESTION_LOCATION,
    _IDENTITY_EMAIL_PHONE_QUESTION,
)


@dataclass
class QuestionDefinition:
    id: str
    question: str
    type: str  # text | textarea | multi_select | url | email | phone | composite
    required: bool
    fields: List[str] = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    validation: Dict[str, Any] = field(default_factory=dict)
    skippable: bool = False
    phase: str = "post_create"  # pre_create | create | post_create

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "type": self.type,
            "required": self.required,
            "fields": list(self.fields),
            "options": list(self.options),
            "validation": dict(self.validation),
            "skippable": self.skippable,
            "phase": self.phase,
        }


_PRE_CREATE_QUESTIONS: Dict[str, QuestionDefinition] = {
    PRE_CREATE_ASK_IDENTITY: QuestionDefinition(
        id=PRE_CREATE_ASK_IDENTITY,
        question="Please share your professional name, title, and company.",
        type="composite",
        required=True,
        fields=["full_name", "professional_title", "company"],
        validation={"minFields": 1},
        phase="pre_create",
    ),
    PRE_CREATE_PROMPT_WELCOME: QuestionDefinition(
        id=PRE_CREATE_PROMPT_WELCOME,
        question=_IDENTITY_EMAIL_PHONE_QUESTION,
        type="composite",
        required=True,
        fields=["email", "phone_number"],
        validation={},
        phase="pre_create",
    ),
    PRE_CREATE_POST_WELCOME: QuestionDefinition(
        id=PRE_CREATE_POST_WELCOME,
        question=_IDENTITY_EMAIL_PHONE_QUESTION,
        type="composite",
        required=True,
        fields=["email", "phone_number"],
        validation={},
        phase="pre_create",
    ),
    PRE_CREATE_READY: QuestionDefinition(
        id=PRE_CREATE_READY,
        question=_IDENTITY_EMAIL_PHONE_QUESTION,
        type="composite",
        required=True,
        fields=["full_name", "email", "phone_number"],
        validation={},
        phase="pre_create",
    ),
}


def _post_create_type(step: str) -> str:
    if step in CATALOG_STEPS or step == "preferred_speaking_time":
        return "multi_select"
    if step == "bio":
        return "textarea"
    if step == "video_links" or step == "social":
        return "url"
    if step in ("talk_description", "past_speaking_examples", "professional_memberships"):
        return "composite"
    if step in ("key_takeaways", "testimonial"):
        return "textarea"
    if step == "location":
        return "composite"
    return "text"


def _post_create_validation(step: str) -> Dict[str, Any]:
    if step == "bio":
        return {"minWords": 20, "maxWords": 150}
    if step == "location":
        return {"requiredFields": ["address_city", "address_state", "address_country"]}
    if step in CATALOG_STEPS or step == "preferred_speaking_time":
        return {"minSelections": 1}
    if step == "key_takeaways":
        return {"minItems": 1, "maxItems": 8}
    return {}


def build_question_catalog(
    catalog: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, QuestionDefinition]:
    """All question definitions keyed by id (pre-create + create + post-create)."""
    catalog = catalog or {}
    out: Dict[str, QuestionDefinition] = dict(_PRE_CREATE_QUESTIONS)

    out[CREATE_STEP] = QuestionDefinition(
        id=CREATE_STEP,
        question=STEP_QUESTIONS.get(CREATE_STEP, "Create your speaker profile."),
        type="composite",
        required=True,
        fields=sorted(STEP_UPSERT_FIELDS.get(CREATE_STEP, set())),
        phase="create",
    )

    for step in POST_CREATE_STEP_ORDER:
        options: List[str] = []
        if step in CATALOG_STEPS:
            options = list(catalog.get(step) or [])
        elif step == "preferred_speaking_time":
            options = list(_PREFERRED_SPEAKING_TIMES)

        q_text = STEP_QUESTIONS.get(step, "")
        if step == "location":
            q_text = _QUESTION_LOCATION

        out[step] = QuestionDefinition(
            id=step,
            question=q_text,
            type=_post_create_type(step),
            required=step not in SKIPPABLE_STEPS,
            fields=sorted(STEP_UPSERT_FIELDS.get(step, set())),
            options=options,
            validation=_post_create_validation(step),
            skippable=step in SKIPPABLE_STEPS,
            phase="post_create",
        )
    return out


def get_question(
    question_id: str,
    catalog: Optional[Dict[str, List[str]]] = None,
) -> Optional[QuestionDefinition]:
    return build_question_catalog(catalog).get(question_id)
