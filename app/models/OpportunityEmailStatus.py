"""MongoDB model for one-time email send flags per speaker+opportunity."""

import os
from datetime import datetime
from typing import Dict, List

from app.helpers.Database import MongoDB


class OpportunityEmailStatusModel:
    """Model for `opportunity_email_status` collection."""

    def __init__(
        self,
        db_name: str = None,
        collection_name: str = "opportunity_email_status",
    ):
        db_name = db_name or os.getenv("DB_NAME")
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def get_sent_map_for_matched(
        self,
        speaker_id: str,
        opportunity_ids: List[str],
    ) -> Dict[str, bool]:
        """Return map of opportunity_id -> matched_email_sent."""
        if not speaker_id or not opportunity_ids:
            return {}
        ids = [str(v) for v in opportunity_ids if v]
        if not ids:
            return {}
        cursor = self.collection.find(
            {"speaker_id": str(speaker_id), "opportunity_id": {"$in": ids}},
            {"opportunity_id": 1, "matched_email_sent": 1},
        )
        out: Dict[str, bool] = {}
        async for doc in cursor:
            opportunity_id = str(doc.get("opportunity_id") or "").strip()
            if opportunity_id:
                out[opportunity_id] = bool(doc.get("matched_email_sent"))
        return out

    async def is_submission_sent(self, speaker_id: str, opportunity_id: str) -> bool:
        if not speaker_id or not opportunity_id:
            return False
        doc = await self.collection.find_one(
            {"speaker_id": str(speaker_id), "opportunity_id": str(opportunity_id)},
            {"submission_email_sent": 1},
        )
        return bool(doc and doc.get("submission_email_sent"))

    async def mark_submission_sent(self, speaker_id: str, opportunity_id: str) -> None:
        if not speaker_id or not opportunity_id:
            return
        now = datetime.utcnow()
        await self.collection.update_one(
            {"speaker_id": str(speaker_id), "opportunity_id": str(opportunity_id)},
            {
                "$set": {
                    "submission_email_sent": True,
                    "updatedAt": now,
                },
                "$setOnInsert": {
                    "matched_email_sent": False,
                    "deadline_email_sent": False,
                    "createdAt": now,
                },
            },
            upsert=True,
        )

    async def is_deadline_sent(self, speaker_id: str, opportunity_id: str) -> bool:
        if not speaker_id or not opportunity_id:
            return False
        doc = await self.collection.find_one(
            {"speaker_id": str(speaker_id), "opportunity_id": str(opportunity_id)},
            {"deadline_email_sent": 1},
        )
        return bool(doc and doc.get("deadline_email_sent"))

    async def mark_deadline_sent(self, speaker_id: str, opportunity_id: str) -> None:
        if not speaker_id or not opportunity_id:
            return
        now = datetime.utcnow()
        await self.collection.update_one(
            {"speaker_id": str(speaker_id), "opportunity_id": str(opportunity_id)},
            {
                "$set": {
                    "deadline_email_sent": True,
                    "updatedAt": now,
                },
                "$setOnInsert": {
                    "matched_email_sent": False,
                    "submission_email_sent": False,
                    "createdAt": now,
                },
            },
            upsert=True,
        )

    async def mark_matched_sent_many(self, speaker_id: str, opportunity_ids: List[str]) -> None:
        """Set matched_email_sent=true for each speaker+opportunity (upsert)."""
        if not speaker_id or not opportunity_ids:
            return
        now = datetime.utcnow()
        for opportunity_id in [str(v) for v in opportunity_ids if v]:
            await self.collection.update_one(
                {
                    "speaker_id": str(speaker_id),
                    "opportunity_id": str(opportunity_id),
                },
                {
                    "$set": {
                        "matched_email_sent": True,
                        "updatedAt": now,
                    },
                    "$setOnInsert": {
                        "submission_email_sent": False,
                        "deadline_email_sent": False,
                        "createdAt": now,
                    },
                },
                upsert=True,
            )
