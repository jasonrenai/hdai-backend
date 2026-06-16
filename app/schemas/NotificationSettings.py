from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationSettingsResponse(BaseModel):
    user_id: str
    new_opportunity: bool = True
    pitch_ready: bool = True
    submission_reminder: bool = True
    deadline_approaching: bool = True
    createdOn: Optional[datetime] = None
    updatedOn: Optional[datetime] = None


class NotificationSettingsUpdateSchema(BaseModel):
    new_opportunity: Optional[bool] = None
    pitch_ready: Optional[bool] = None
    submission_reminder: Optional[bool] = None
    deadline_approaching: Optional[bool] = None

    def non_empty_updates(self) -> dict[str, bool]:
        updates: dict[str, bool] = {}
        for key in (
            "new_opportunity",
            "pitch_ready",
            "submission_reminder",
            "deadline_approaching",
        ):
            value = getattr(self, key)
            if value is not None:
                updates[key] = value
        return updates


NOTIFICATION_SETTING_FIELDS = (
    "new_opportunity",
    "pitch_ready",
    "submission_reminder",
    "deadline_approaching",
)

DEFAULT_NOTIFICATION_SETTINGS: dict[str, bool] = {
    "new_opportunity": True,
    "pitch_ready": True,
    "submission_reminder": True,
    "deadline_approaching": True,
}
