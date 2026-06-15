"""
MongoDB model for AI-generated speaker bio document summaries.
One document per speaker profile (upserted by speaker_profile_id).
Stores processing status, errors, and timestamps. Speaker profile keeps url + summary only.
"""
import os
from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId

from app.helpers.Database import MongoDB


class BioDocumentSummaryModel:
    def __init__(
        self,
        db_name: Optional[str] = None,
        collection_name: str = "bio_document_summaries",
    ):
        self.collection = MongoDB.get_database(db_name or os.getenv("DB_NAME"))[
            collection_name
        ]

    @staticmethod
    def _normalize_doc(doc: Optional[dict]) -> Optional[dict]:
        if not doc:
            return None
        if doc.get("_id") is not None:
            doc["_id"] = str(doc["_id"])
        return doc

    async def get_by_profile_id(self, speaker_profile_id: str) -> Optional[dict]:
        doc = await self.collection.find_one({"speaker_profile_id": speaker_profile_id})
        return self._normalize_doc(doc)

    async def upsert_for_profile(
        self,
        speaker_profile_id: str,
        *,
        bio_document_url: Any = ...,
        status: Any = ...,
        summary: Any = ...,
        error: Any = ...,
        summarized_at: Any = ...,
    ) -> Optional[dict]:
        """Create or update the bio document summary record for a speaker profile."""
        pid = (speaker_profile_id or "").strip()
        if not pid:
            return None

        field_values = {
            "bio_document_url": bio_document_url,
            "status": status,
            "bio_document_summary": summary,
            "error": error,
            "summarized_at": summarized_at,
        }
        updates: Dict[str, Any] = {
            k: v for k, v in field_values.items() if v is not ...
        }
        now = datetime.utcnow()
        updates["updatedAt"] = now

        set_on_insert = {
            "speaker_profile_id": pid,
            "createdAt": now,
        }
        result = await self.collection.find_one_and_update(
            {"speaker_profile_id": pid},
            {"$set": updates, "$setOnInsert": set_on_insert},
            upsert=True,
            return_document=True,
        )
        return self._normalize_doc(result)

    async def clear_for_profile(self, speaker_profile_id: str) -> bool:
        """Remove bio document summary record when bio_document_url is cleared."""
        pid = (speaker_profile_id or "").strip()
        if not pid:
            return False
        result = await self.collection.delete_one({"speaker_profile_id": pid})
        return result.deleted_count > 0
