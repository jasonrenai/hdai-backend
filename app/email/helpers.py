import os
from typing import Any, Dict

from app.email.constants import POSTMARK_TOKEN_ENV_KEYS, SENDER_EMAILS
from app.email.enums import SenderType


def get_postmark_server_token() -> str | None:
    for key in POSTMARK_TOKEN_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


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


def _normalize_scalar_or_nested(value: Any, key: str) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        nested = value.get(key)
        if nested is None:
            return ""
        return str(nested)
    return str(value)


def normalize_template_model(payload: Dict[str, Any]) -> Dict[str, str]:
    model = {
        "subject": _normalize_scalar_or_nested(payload.get("subject"), "subject"),
        "preheader": _normalize_scalar_or_nested(payload.get("preheader"), "preheader"),
        "badge": _normalize_scalar_or_nested(payload.get("badge"), "badge"),
        "title": _normalize_scalar_or_nested(payload.get("title"), "title"),
        "user_name": _normalize_scalar_or_nested(payload.get("user_name"), "user_name"),
        "intro": _normalize_scalar_or_nested(payload.get("intro"), "intro"),
        "body": _normalize_scalar_or_nested(payload.get("body"), "body"),
        "cta_url": _normalize_scalar_or_nested(payload.get("cta_url"), "cta_url"),
        "cta_text": _normalize_scalar_or_nested(payload.get("cta_text"), "cta_text"),
        "secondary_note": _normalize_scalar_or_nested(payload.get("secondary_note"), "secondary_note"),
    }
    return model

