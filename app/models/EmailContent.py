import os
from datetime import datetime
from typing import List

from bson import ObjectId

from app.helpers.Database import MongoDB


class EmailContentModel:
    """Model for generated speaker opportunity outreach emails."""

    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="EmailContent"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def create(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
        mail_title: str,
        mail_content: str,
        authority_type: str = "profile_fit",
        recipient_email: str = "",
        event_contact: str = "",
        requires_email_submission: bool = False,
        submission_note: str = "",
    ) -> dict:
        doc = {
            "speaker_profile_id": speaker_profile_id,
            "opportunity_id": opportunity_id,
            "mail_title": mail_title,
            "mail_content": mail_content,
            "authority_type": authority_type,
            "recipient_email": recipient_email,
            "event_contact": event_contact,
            "requires_email_submission": requires_email_submission,
            "submission_note": submission_note,
            "pitch_ready_notification_sent": False,
            "createdAt": datetime.utcnow(),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        return doc

    async def get_list_by_speaker_and_opportunity(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
        skip: int,
        limit: int,
    ) -> List[dict]:
        cursor = (
            self.collection.find(
                {
                    "speaker_profile_id": speaker_profile_id,
                    "opportunity_id": opportunity_id,
                }
            )
            .sort("createdAt", -1)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        for doc in docs:
            if doc.get("_id"):
                doc["_id"] = str(doc["_id"])
        return docs

    async def count_by_speaker_and_opportunity(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
    ) -> int:
        return await self.collection.count_documents(
            {
                "speaker_profile_id": speaker_profile_id,
                "opportunity_id": opportunity_id,
            }
        )

    async def list_unsent_pitch_ready_by_speaker_id(
        self,
        speaker_profile_id: str,
        *,
        limit: int = 200,
    ) -> List[dict]:
        """EmailContent rows not yet included in a pitch-ready notification email."""
        sid = str(speaker_profile_id or "").strip()
        if not sid:
            return []
        cursor = (
            self.collection.find(
                {
                    "speaker_profile_id": sid,
                    "$or": [
                        {"pitch_ready_notification_sent": False},
                        {"pitch_ready_notification_sent": {"$exists": False}},
                    ],
                }
            )
            .sort("createdAt", 1)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        for doc in docs:
            if doc.get("_id"):
                doc["_id"] = str(doc["_id"])
        return docs

    async def mark_pitch_ready_notification_sent(self, email_content_id: str) -> None:
        if not self.is_valid_object_id(email_content_id):
            return
        await self.collection.update_one(
            {"_id": ObjectId(email_content_id)},
            {"$set": {"pitch_ready_notification_sent": True}},
        )

    @staticmethod
    def is_valid_object_id(value: str) -> bool:
        return ObjectId.is_valid(value)
