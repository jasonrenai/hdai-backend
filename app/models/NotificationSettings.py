"""MongoDB model for per-user email notification preferences."""

import os
from datetime import datetime
from typing import Any, Optional

from pymongo import ReturnDocument

from app.helpers.Database import MongoDB
from app.schemas.NotificationSettings import default_email_notifications


class NotificationSettingsModel:
    def __init__(
        self,
        db_name: str | None = None,
        collection_name: str = "notification_settings",
    ):
        db_name = db_name or os.getenv("DB_NAME")
        self.collection = MongoDB.get_database(db_name)[collection_name]

    @staticmethod
    def _normalize_user_id(user_id: str) -> str:
        return str(user_id or "").strip()

    async def get_by_user_id(self, user_id: str) -> Optional[dict[str, Any]]:
        uid = self._normalize_user_id(user_id)
        if not uid:
            return None
        return await self.collection.find_one({"user_id": uid})

    async def get_or_create(self, user_id: str) -> dict[str, Any]:
        uid = self._normalize_user_id(user_id)
        if not uid:
            raise ValueError("user_id is required")

        existing = await self.get_by_user_id(uid)
        if existing:
            return existing

        now = datetime.utcnow()
        doc = {
            "user_id": uid,
            "email_notifications": default_email_notifications(),
            "createdOn": now,
            "updatedOn": now,
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def create_for_user_if_missing(self, user_id: str) -> tuple[bool, dict[str, Any]]:
        """Insert default notification settings if none exist. Returns (created, doc)."""
        uid = self._normalize_user_id(user_id)
        if not uid:
            raise ValueError("user_id is required")

        existing = await self.get_by_user_id(uid)
        if existing:
            return False, existing

        now = datetime.utcnow()
        doc = {
            "user_id": uid,
            "email_notifications": default_email_notifications(),
            "createdOn": now,
            "updatedOn": now,
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return True, doc

    async def update(
        self,
        user_id: str,
        email_notifications: list[dict[str, Any]],
    ) -> dict[str, Any]:
        uid = self._normalize_user_id(user_id)
        if not uid:
            raise ValueError("user_id is required")
        if not email_notifications:
            return await self.get_or_create(uid)

        await self.get_or_create(uid)
        now = datetime.utcnow()
        doc = await self.collection.find_one_and_update(
            {"user_id": uid},
            {"$set": {"email_notifications": email_notifications, "updatedOn": now}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise RuntimeError("Failed to update notification settings")
        return doc
