from datetime import datetime
from typing import Any, Optional

from app.models.NotificationSettings import NotificationSettingsModel
from app.schemas.NotificationSettings import (
    EMAIL_NOTIFICATION_CATALOG,
    email_notification_to_document,
    normalize_email_notifications,
    validate_email_notification_item,
)


class NotificationSettingsService:
    def __init__(self, model: Optional[NotificationSettingsModel] = None):
        self.model = model or NotificationSettingsModel()

    def _merge_updates(
        self,
        current: list[dict[str, Any]],
        updates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_slug = {item["slug"]: dict(item) for item in current}
        for update in updates:
            slug = update["slug"]
            existing = by_slug[slug]
            if "enabled" in update:
                existing["enabled"] = update["enabled"]
            if "frequency" in update:
                existing["frequency"] = update["frequency"]
            existing["name"] = EMAIL_NOTIFICATION_CATALOG[slug]["name"]
            by_slug[slug] = existing
        return [by_slug[slug] for slug in EMAIL_NOTIFICATION_CATALOG]

    def _serialize(self, doc: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_email_notifications(doc)
        email_notifications = [validate_email_notification_item(item) for item in normalized]

        out: dict[str, Any] = {
            "user_id": str(doc.get("user_id") or ""),
            "email_notifications": email_notifications,
        }
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

    async def update_for_user(
        self,
        user_id: str,
        updates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not updates:
            return {
                "success": False,
                "data": None,
                "error": "At least one notification setting must be provided.",
            }

        doc = await self.model.get_or_create(user_id)
        current = normalize_email_notifications(doc)
        merged = self._merge_updates(current, updates)
        validated = [
            email_notification_to_document(validate_email_notification_item(item))
            for item in merged
        ]

        doc = await self.model.update(user_id, validated)
        return {"success": True, "data": self._serialize(doc), "error": ""}
