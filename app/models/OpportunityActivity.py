import os
from datetime import datetime
from typing import Any, Optional

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
