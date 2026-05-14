from typing import Any

from dataclasses import dataclass

from app.email.enums import EmailEventType, SenderType


@dataclass(frozen=True)
class EmailEventConfig:
    """Maps a logical email event to sender and default Postmark template model keys."""

    event_type: EmailEventType
    sender: SenderType
    default_template_model: dict[str, Any]
