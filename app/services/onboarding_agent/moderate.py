"""ModerateInput — jailbreak / abuse gating.

Hard regex jailbreak stays as a cheap pre-check. Analyze may also set promptInjection;
call refuse_prompt_injection() when that flag is true after Analyze.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.speaker_profile_chatbot_steps import (
    looks_like_prompt_injection,
    prompt_injection_refusal_message,
)

ModerationAction = Literal["CONTINUE", "WARN", "REFUSE", "REDIRECT"]


@dataclass
class ModerationResult:
    action: ModerationAction
    message: str = ""
    reason: str = ""


def moderate_input(
    message: str,
    *,
    ask_identity: bool = False,
) -> ModerationResult:
    text = (message or "").strip()
    if not text:
        return ModerationResult(action="CONTINUE")

    if looks_like_prompt_injection(text):
        return refuse_prompt_injection(ask_identity=ask_identity)

    # Obvious abuse / threats — soft refuse and stay on onboarding.
    lowered = text.lower()
    abuse_markers = (
        "kill yourself",
        "kill you",
        "i will find you",
        "bomb threat",
    )
    if any(m in lowered for m in abuse_markers):
        return ModerationResult(
            action="WARN",
            message=(
                "I'm here to help you build your speaker profile. "
                "Let's keep the conversation focused on that."
            ),
            reason="ABUSE",
        )

    return ModerationResult(action="CONTINUE")


def refuse_prompt_injection(*, ask_identity: bool = False) -> ModerationResult:
    """Shared refuse path for regex pre-check and Analyze promptInjection flag."""
    return ModerationResult(
        action="REFUSE",
        message=prompt_injection_refusal_message(ask_identity=ask_identity),
        reason="PROMPT_INJECTION",
    )
