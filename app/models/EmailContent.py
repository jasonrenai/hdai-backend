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
            "recipient_email": recipient_email,
            "event_contact": event_contact,
            "requires_email_submission": requires_email_submission,
            "submission_note": submission_note,
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

    @staticmethod
    def is_valid_object_id(value: str) -> bool:
        return ObjectId.is_valid(value)
