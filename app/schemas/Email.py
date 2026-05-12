from typing import Any, Dict, Optional

from pydantic import BaseModel, EmailStr, Field

from app.email.enums import EmailEventType, SenderType


class EmailEventTestRequest(BaseModel):
    to_email: EmailStr
    user_name: str = ""
    cta_url: Optional[str] = None
    sender: Optional[SenderType] = None
    template_model: Dict[str, Any] = Field(default_factory=dict)


class SendSpecificEmailRequest(BaseModel):
    to_email: EmailStr
    user_name: str = ""
    cta_url: Optional[str] = None
    template_model: Dict[str, Any] = Field(default_factory=dict)


class SendEventByTypeRequest(EmailEventTestRequest):
    event_type: EmailEventType

