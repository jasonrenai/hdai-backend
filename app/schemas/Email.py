from typing import Any, Dict, Optional

from pydantic import BaseModel, EmailStr, Field

from app.email.enums import EmailEventType, SenderType


class EmailEventTestRequest(BaseModel):
    to_email: EmailStr
    user_name: str = ""
    subject: Optional[str] = None
    cta_url: Optional[str] = None
    sender: Optional[SenderType] = None
    template_model_overrides: Dict[str, Any] = Field(default_factory=dict)


class SendSpecificEmailRequest(BaseModel):
    to_email: EmailStr
    user_name: str = ""
    cta_url: Optional[str] = None
    subject: Optional[str] = None


class SendEventByTypeRequest(EmailEventTestRequest):
    event_type: EmailEventType

