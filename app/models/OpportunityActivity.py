import os
from datetime import datetime, timedelta
from typing import Any, List, Optional

from bson import ObjectId

from app.helpers.Database import MongoDB


class OpportunityActivityModel:
    """Per-speaker, per-opportunity flags: wishlist, applied, accepted, expired, outcomes. Collection: opportunityActivity."""

    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="opportunityActivity"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    @staticmethod
    def is_valid_object_id(value: str) -> bool:
        return bool(value) and ObjectId.is_valid(value)

    async def get_one(self, speaker_id: str, opportunity_id: str) -> Optional[dict]:
        if not speaker_id or not opportunity_id:
            return None
        doc = await self.collection.find_one(
            {"speaker_id": str(speaker_id), "opportunityId": str(opportunity_id)}
        )
        if doc and doc.get("_id") is not None:
            doc["_id"] = str(doc["_id"])
        return doc

    async def upsert_fields(
        self,
        speaker_id: str,
        opportunity_id: str,
        set_fields: dict[str, Any],
    ) -> dict:
        """Merge set_fields into an existing doc or create one. Returns the document after update."""
        now = datetime.utcnow()
        filter_q = {"speaker_id": str(speaker_id), "opportunityId": str(opportunity_id)}
        update_doc = {**set_fields, "updatedAt": now}

        existing = await self.collection.find_one(filter_q)
        if existing:
            await self.collection.update_one(filter_q, {"$set": update_doc})
        else:
            base = {
                "speaker_id": str(speaker_id),
                "opportunityId": str(opportunity_id),
                "isWishlist": False,
                "isApplied": False,
                "isAccepted": False,
                "isExpired": False,
                **set_fields,
                "updatedAt": now,
            }
            await self.collection.insert_one(base)

        doc = await self.collection.find_one(filter_q)
        if doc and doc.get("_id") is not None:
            doc["_id"] = str(doc["_id"])
        return doc

    async def find_wishlist_pending_submission(
        self,
        *,
        cooldown_minutes: int,
        limit: int = 50,
    ) -> List[dict]:
        """
        Wishlisted, not applied, not expired. Optionally only rows where no reminder was sent
        since `cooldown_minutes` ago (0 = no time filter — every cron tick may match again).
        """
        q: dict[str, Any] = {
            "isWishlist": True,
            "isApplied": False,
            "isExpired": False,
        }
        if cooldown_minutes > 0:
            cutoff = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
            q["$or"] = [
                {"lastSubmissionReminderSentAt": {"$exists": False}},
                {"lastSubmissionReminderSentAt": {"$lt": cutoff}},
            ]
        cursor = self.collection.find(q).limit(limit)
        out: List[dict] = []
        async for doc in cursor:
            if doc.get("_id") is not None:
                doc["_id"] = str(doc["_id"])
            out.append(doc)
        return out

    async def mark_last_submission_reminder_sent(
        self,
        speaker_id: str,
        opportunity_id: str,
    ) -> None:
        await self.collection.update_one(
            {"speaker_id": str(speaker_id), "opportunityId": str(opportunity_id)},
            {"$set": {"lastSubmissionReminderSentAt": datetime.utcnow()}},
            upsert=False,
        )

    async def find_wishlist_for_deadline_approaching(
        self,
        *,
        cooldown_minutes: int,
        limit: int = 100,
    ) -> List[dict]:
        """
        Same wishlist / not applied / not expired filter as submission reminders.
        Cooldown uses lastDeadlineApproachingSentAt so daily job does not spam the same row.
        """
        q: dict[str, Any] = {
            "isWishlist": True,
            "isApplied": False,
            "isExpired": False,
        }
        if cooldown_minutes > 0:
            cutoff = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
            q["$or"] = [
                {"lastDeadlineApproachingSentAt": {"$exists": False}},
                {"lastDeadlineApproachingSentAt": {"$lt": cutoff}},
            ]
        cursor = self.collection.find(q).limit(limit)
        out: List[dict] = []
        async for doc in cursor:
            if doc.get("_id") is not None:
                doc["_id"] = str(doc["_id"])
            out.append(doc)
        return out

    async def mark_last_deadline_approaching_sent(
        self,
        speaker_id: str,
        opportunity_id: str,
    ) -> None:
        await self.collection.update_one(
            {"speaker_id": str(speaker_id), "opportunityId": str(opportunity_id)},
            {"$set": {"lastDeadlineApproachingSentAt": datetime.utcnow()}},
            upsert=False,
        )
