"""GetOnboardingState / SelectNextQuestion — backend-owned workflow position."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.onboarding_agent.question_schema import (
    QuestionDefinition,
    build_question_catalog,
    get_question,
)
from app.services.speaker_profile_chatbot_steps import (
    POST_CREATE_STEP_ORDER,
    PRE_CREATE_ASK_IDENTITY,
    PRE_CREATE_POST_WELCOME,
    PRE_CREATE_PROMPT_WELCOME,
    PRE_CREATE_READY,
    derive_expected_step,
    step_satisfied,
)


@dataclass
class OnboardingState:
    current_question_id: str
    current_question: Optional[QuestionDefinition]
    completed_questions: List[str]
    skipped_questions: List[str]
    conversation_status: str
    progress: int
    has_profile: bool
    profile: Optional[dict]
    pending_identity: Optional[Dict[str, Any]] = None
    pending_confirmation: Optional[Dict[str, Any]] = None
    pre_create_subphase: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "currentQuestion": self.current_question_id,
            "currentQuestionMeta": self.current_question.to_dict() if self.current_question else None,
            "completedQuestions": list(self.completed_questions),
            "skippedQuestions": list(self.skipped_questions),
            "conversationStatus": self.conversation_status,
            "progress": self.progress,
            "hasProfile": self.has_profile,
            "pendingIdentity": self.pending_identity,
            "pendingConfirmation": self.pending_confirmation,
            "preCreateSubphase": self.pre_create_subphase,
        }


def _progress_percent(
    *,
    has_profile: bool,
    steps_done: List[str],
    skipped: List[str],
    profile: Optional[dict],
) -> int:
    if not has_profile:
        return 5
    total = len(POST_CREATE_STEP_ORDER)
    if total == 0:
        return 100
    done = 0
    for step in POST_CREATE_STEP_ORDER:
        if step_satisfied(profile, steps_done, step) or step in skipped:
            done += 1
    return min(100, 10 + int(90 * done / total))


def derive_pre_create_question(pending_identity: Optional[Dict[str, Any]]) -> str:
    """
    Pre-create step from accumulated pending_identity — NOT from chat history.

    Order: name + title + company → email/phone → ready to create.
    Stay on ask_identity until all three identity fields are present (unless
    contact collection already started via email/phone).
    """
    pending = pending_identity if isinstance(pending_identity, dict) else {}
    full_name = str(pending.get("full_name") or "").strip()
    title = str(pending.get("professional_title") or "").strip()
    company = str(pending.get("company") or "").strip()
    email = str(pending.get("email") or "").strip()
    phone = str(pending.get("phone_number") or "").strip()

    if not full_name:
        return PRE_CREATE_ASK_IDENTITY
    # Once contact fields exist, finish email/phone even if title/company were skipped earlier
    if email and phone:
        return PRE_CREATE_READY
    if email or phone:
        return PRE_CREATE_POST_WELCOME
    if not title or not company:
        return PRE_CREATE_ASK_IDENTITY
    return PRE_CREATE_PROMPT_WELCOME


def get_onboarding_state(
    *,
    profile: Optional[dict],
    session: Optional[dict],
    history: List[Dict[str, Any]],
    message: str,
    catalog: Optional[Dict[str, List[str]]] = None,
) -> OnboardingState:
    session = session or {}
    steps_done: List[str] = list(session.get("onboarding_steps_done") or [])
    skipped: List[str] = list(session.get("skipped_questions") or [])
    status = (session.get("conversation_status") or "IN_PROGRESS").upper()
    has_profile = bool(profile and (profile.get("_id") or profile.get("email")))
    pending_identity = session.get("pending_identity")
    pending_confirmation = session.get("pending_confirmation")

    pre_create_subphase = None
    if not has_profile:
        pre_create_subphase = derive_pre_create_question(
            pending_identity if isinstance(pending_identity, dict) else None
        )
        current_id = pre_create_subphase
    else:
        current_id = derive_expected_step(profile, steps_done, has_profile=True)

    questions = build_question_catalog(catalog)
    current_q = questions.get(current_id) or get_question(current_id, catalog)

    return OnboardingState(
        current_question_id=current_id,
        current_question=current_q,
        completed_questions=steps_done,
        skipped_questions=skipped,
        conversation_status=status,
        progress=_progress_percent(
            has_profile=has_profile,
            steps_done=steps_done,
            skipped=skipped,
            profile=profile,
        ),
        has_profile=has_profile,
        profile=profile,
        pending_identity=pending_identity if isinstance(pending_identity, dict) else None,
        pending_confirmation=pending_confirmation if isinstance(pending_confirmation, dict) else None,
        pre_create_subphase=pre_create_subphase,
    )


def select_next_question(
    *,
    profile: Optional[dict],
    steps_done: List[str],
    has_profile: bool,
    catalog: Optional[Dict[str, List[str]]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
    pending_identity: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the next question id. Never decided by the LLM."""
    if not has_profile:
        return derive_pre_create_question(pending_identity)
    return derive_expected_step(profile, steps_done, has_profile=True)
