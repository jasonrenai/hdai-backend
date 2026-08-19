"""
MongoDB model for matched opportunities per speaker.
Collection: matchedOpportunities. One document per speaker: { speaker_id, opportunities, status, updatedAt }.
Status: "processing" | "completed".
"""
import os
from datetime import datetime
from typing import List

from bson import ObjectId
from app.helpers.Database import MongoDB


class MatchedOpportunitiesModel:
    """Model for matchedOpportunities collection: speaker_id -> list of opportunity ids and status."""

    def __init__(
        self,
        db_name: str = None,
        collection_name: str = "matchedOpportunities",
    ):
        db_name = db_name or os.getenv("DB_NAME")
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def delete_by_speaker_id(self, speaker_id: str) -> bool:
        """Delete all documents for this speaker_id. Returns True if at least one was deleted or no doc existed."""
        if not speaker_id:
            return False
        result = await self.collection.delete_many({"speaker_id": str(speaker_id)})
        return True

    async def create_processing_entry(self, speaker_id: str) -> str | None:
        """
        Insert a new document with status 'processing' and empty opportunities.
        Returns the inserted document _id as string, or None on failure.
        """
        if not speaker_id:
            return None
        doc = {
            "speaker_id": str(speaker_id),
            "opportunities": [],
            "status": "processing",
            "updatedAt": datetime.utcnow(),
        }
        result = await self.collection.insert_one(doc)
        return str(result.inserted_id) if result.inserted_id else None

    async def update_entry_completed(self, entry_id: str, opportunity_ids: List[str]) -> bool:
        """Update document by _id to status 'completed' and set opportunities. Returns True on success."""
        if not entry_id:
            return False
        try:
            oid = ObjectId(entry_id)
        except Exception:
            return False
        result = await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": "completed",
                    "opportunities": [str(oid) for oid in (opportunity_ids or [])],
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        return result.matched_count > 0

    async def upsert_by_speaker_id(self, speaker_id: str, opportunity_ids: List[str]) -> bool:
        """Insert or replace document for this speaker_id with the given opportunity ids. Returns True on success."""
        if not speaker_id:
            return False
        doc = {
            "speaker_id": str(speaker_id),
            "opportunities": [str(oid) for oid in (opportunity_ids or [])],
            "status": "completed",
            "updatedAt": datetime.utcnow(),
        }
        await self.collection.update_one(
            {"speaker_id": str(speaker_id)},
            {"$set": doc},
            upsert=True,
        )
        return True

    async def get_by_speaker_id(self, speaker_id: str) -> dict | None:
        """Get document by speaker_id. Returns { _id, speaker_id, opportunities, status?, updatedAt } or None."""
        if not speaker_id:
            return None
        doc = await self.collection.find_one({"speaker_id": str(speaker_id)})
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc

    async def list_all(self) -> List[dict]:
        """All matchedOpportunities docs (speaker_id + opportunity ids). Used by expiry cron."""
        cursor = self.collection.find({}, {"speaker_id": 1, "opportunities": 1, "status": 1})
        out: List[dict] = []
        async for doc in cursor:
            if doc.get("_id") is not None:
                doc["_id"] = str(doc["_id"])
            out.append(doc)
        return out
