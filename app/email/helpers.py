import json
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


def normalize_template_model(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce Postmark TemplateModel values to JSON-friendly scalars (strings for simple types)."""
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            out[key] = ""
        elif isinstance(value, (dict, list)):
            out[key] = json.dumps(value)
        else:
            out[key] = str(value)
    return out
