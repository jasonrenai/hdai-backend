import os
from functools import lru_cache
from typing import Any, Dict, Optional

from app.email.constants import POSTMARK_TOKEN_ENV_KEYS, SENDER_EMAILS
from app.email.enums import SenderType

# Truthy values for EMAIL_SENDING_ENABLED. Default is enabled when unset (prod-safe).
_EMAIL_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def is_email_sending_enabled() -> bool:
    """
    When EMAIL_SENDING_ENABLED is false/0/no/off, skip Postmark sends (useful in local/dev).
    Unset or any other value defaults to True so production keeps sending.
    """
    raw = (os.getenv("EMAIL_SENDING_ENABLED") or "true").strip().lower()
    return raw in _EMAIL_ENABLED_VALUES


def get_postmark_server_token() -> str | None:
    for key in POSTMARK_TOKEN_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


@lru_cache(maxsize=8)
def resolve_sender_email(sender: SenderType) -> str:
    env_key_map = {
        SenderType.HELLO: "EMAIL_FROM_HELLO",
        SenderType.ALERTS: "EMAIL_FROM_ALERTS",
        SenderType.SUPPORT: "EMAIL_FROM_SUPPORT",
    }
    env_value = os.getenv(env_key_map[sender])
    if env_value:
        return env_value
    return SENDER_EMAILS[sender]


def normalize_template_model(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Coerce Postmark TemplateModel values: nested dicts/lists are preserved;
    leaf values become strings (bool/int/float kept as-is for JSON compatibility).
    """

    def norm(v: Any) -> Any:
        if v is None:
            return ""
        if isinstance(v, dict):
            return {str(k): norm(x) for k, x in v.items()}
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v
        return str(v)

    return {str(k): norm(val) for k, val in payload.items()}


def speaker_profile_notification_email(profile: Optional[dict]) -> Optional[str]:
    """Contact email on `speaker_profiles` (same id as opportunityActivity.speaker_id)."""
    if not profile:
        return None
    raw = profile.get("email")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None
