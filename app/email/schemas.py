from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.email.enums import EmailEventType, SenderType


@dataclass(frozen=True)
class EmailEventConfig:
    event_type: EmailEventType
    sender: SenderType
    template_id: Optional[int] = None
    template_alias: Optional[str] = None
    defaults: Dict[str, Any] = field(default_factory=dict)

