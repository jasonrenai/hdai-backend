from datetime import datetime
from typing import Any, Optional

from app.models.NotificationSettings import NotificationSettingsModel
from app.schemas.NotificationSettings import (
    DEFAULT_NOTIFICATION_SETTINGS,
    NOTIFICATION_SETTING_FIELDS,
)


class NotificationSettingsService:
    def __init__(self, model: Optional[NotificationSettingsModel] = None):
        self.model = model or NotificationSettingsModel()

    def _serialize(self, doc: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "user_id": str(doc.get("user_id") or ""),
        }
        for key in NOTIFICATION_SETTING_FIELDS:
            out[key] = bool(doc.get(key, DEFAULT_NOTIFICATION_SETTINGS[key]))
        for ts_key in ("createdOn", "updatedOn"):
            value = doc.get(ts_key)
            if isinstance(value, datetime):
                out[ts_key] = value.isoformat()
            elif value is not None:
                out[ts_key] = value
        return out

    async def get_or_create_for_user(self, user_id: str) -> dict[str, Any]:
        doc = await self.model.get_or_create(user_id)
        return {"success": True, "data": self._serialize(doc), "error": ""}

    async def update_for_user(self, user_id: str, updates: dict[str, bool]) -> dict[str, Any]:
        if not updates:
            return {
                "success": False,
                "data": None,
                "error": "At least one notification setting must be provided.",
            }
        doc = await self.model.update(user_id, updates)
        return {"success": True, "data": self._serialize(doc), "error": ""}
