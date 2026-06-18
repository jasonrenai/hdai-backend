"""MongoDB model for delayed new_opportunity / pitch_ready notification sends."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from app.helpers.Database import MongoDB


class PendingNotificationEmailModel:
    def __init__(
        self,
        db_name: str | None = None,
        collection_name: str = "pending_notification_emails",
    ):
        db_name = db_name or os.getenv("DB_NAME")
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def enqueue(self, doc: dict[str, Any]) -> dict[str, Any]:
        now = datetime.utcnow()
        row = {
            **doc,
            "status": "pending",
            "createdAt": now,
            "updatedAt": now,
        }
        result = await self.collection.insert_one(row)
        row["_id"] = result.inserted_id
        return row

    async def find_due_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        now = datetime.utcnow()
        cursor = (
            self.collection.find({"status": "pending", "send_at": {"$lte": now}})
            .sort("send_at", 1)
            .limit(limit)
        )
        out: list[dict[str, Any]] = []
        async for doc in cursor:
            if doc.get("_id") is not None:
                doc["_id"] = str(doc["_id"])
            out.append(doc)
        return out

    async def mark_sent(self, pending_id: str) -> None:
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            oid = ObjectId(pending_id)
        except (InvalidId, TypeError):
            return
        await self.collection.update_one(
            {"_id": oid},
            {"$set": {"status": "sent", "updatedAt": datetime.utcnow()}},
        )

    async def mark_cancelled(self, pending_id: str, *, reason: str = "") -> None:
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            oid = ObjectId(pending_id)
        except (InvalidId, TypeError):
            return
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": "cancelled",
                    "cancel_reason": reason,
                    "updatedAt": datetime.utcnow(),
                }
            },
        )

    async def has_sent_for_pitch(
        self,
        speaker_id: str,
        opportunity_id: str,
    ) -> bool:
        doc = await self.collection.find_one(
            {
                "slug": "pitch_ready",
                "speaker_id": str(speaker_id),
                "opportunity_id": str(opportunity_id),
                "status": "sent",
            },
            {"_id": 1},
        )
        return doc is not None
