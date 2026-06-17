import os
from datetime import datetime
from typing import List, Optional

from bson import ObjectId

from app.helpers.Database import MongoDB


class ApplicationContentModel:
    """Model for generated speaker opportunity application form content."""

    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="ApplicationContent"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def create(
        self,
        speaker_profile_id: str,
        opportunity_id: str,
        name: str,
        title: str,
        company: str,
        email: str,
        presentation_type: str,
        session_title: str,
        abstract: str,
        takeaways: List[str],
        bio: str,
        speaking_history: str,
        linkedin_url: Optional[str] = None,
        twitter: Optional[str] = None,
        facebook: Optional[str] = None,
        video_links: Optional[List[str]] = None,
    ) -> dict:
        doc = {
            "speaker_profile_id": speaker_profile_id,
            "opportunity_id": opportunity_id,
            "name": name,
            "title": title,
            "company": company,
            "email": email,
            "presentation_type": presentation_type,
            "session_title": session_title,
            "abstract": abstract,
            "takeaways": takeaways,
            "bio": bio,
            "speaking_history": speaking_history,
            "linkedin_url": linkedin_url,
            "twitter": twitter,
            "facebook": facebook,
            "video_links": video_links,
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
