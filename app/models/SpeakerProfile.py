"""
MongoDB model for Speaker Profile (progressive onboarding + final save).
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from bson import ObjectId
from pymongo import ReturnDocument

from app.helpers.Database import MongoDB
from app.config.speaker_profile_steps import get_next_step


def _user_id_query_filter(user_id: Optional[str]) -> dict:
    """
    Match speaker_profiles where user_id equals the given id as stored either as a string
    or as ObjectId (legacy / mixed data).
    """
    uid = (user_id or "").strip()
    if not uid:
        return {"_id": {"$in": []}}  # matches nothing
    try:
        oid = ObjectId(uid)
        return {"$or": [{"user_id": uid}, {"user_id": oid}]}
    except Exception:
        return {"user_id": uid}


# Profile field names (for updates)
PROFILE_FIELDS = [
    "full_name", "professional_title", "company", "email", "topics", "speaking_formats", "delivery_mode", "linkedin_url",
    "past_speaking_examples", "video_links", "talk_description", "key_takeaways", "target_audiences",
    # Editable after creation (not part of verify-step)
    "name_salutation", "bio", "twitter", "facebook", "instagram", "address_city", "address_state", "address_country",
    "geography_preferences",
    "phone_country_code", "phone_number", "professional_memberships", "preferred_speaking_time", "testimonial",
    "profile_picture", "headshot_picture", "bio_document_url",
    # Status (set when all mandatory fields are filled)
    "isCompleted",
]


class SpeakerProfileModel:
    def __init__(
        self,
        db_name: Optional[str] = None,
        collection_name: str = "speaker_profiles",
    ):
        self.collection = MongoDB.get_database(db_name or os.getenv("DB_NAME"))[
            collection_name
        ]

    @staticmethod
    def _normalize_professional_memberships_for_response(doc: dict) -> dict:
        if not isinstance(doc, dict):
            return doc
        memberships = doc.get("professional_memberships")
        if not isinstance(memberships, list):
            return doc
        normalized = []
        for item in memberships:
            if isinstance(item, dict):
                normalized.append(
                    {
                        "title": item.get("title") if item.get("title") is not None else "",
                        "organization": item.get("organization") if item.get("organization") is not None else "",
                        "start_date": item.get("start_date") if "start_date" in item else None,
                        "end_date": item.get("end_date") if "end_date" in item else None,
                        "is_current": item.get("is_current") if "is_current" in item else None,
                    }
                )
            elif isinstance(item, str):
                normalized.append(
                    {
                        "title": "",
                        "organization": item.strip(),
                        "start_date": None,
                        "end_date": None,
                        "is_current": None,
                    }
                )
        doc["professional_memberships"] = normalized
        return doc

    async def count(self) -> int:
        """Total documents in the speaker_profiles collection."""
        return await self.collection.count_documents({})

    async def count_by_user_id(self, user_id: Optional[str]) -> int:
        """Count speaker profiles linked to this user (string or legacy ObjectId user_id)."""
        uid = (user_id or "").strip()
        if not uid:
            return 0
        return await self.collection.count_documents(_user_id_query_filter(uid))

    async def create_profile(
        self,
        full_name: str,
        user_id: Optional[str] = None,
    ) -> dict:
        """
        Create a new profile document after first valid step (full_name).
        Sets current_step to the next step and completed_steps to ["full_name"].
        """
        next_step = get_next_step("full_name")
        next_step_name = next_step.step_name if next_step else "topics"
        doc = {
            "full_name": full_name.strip(),
            "current_step": next_step_name,
            "completed_steps": ["full_name"],
            "conversation": [],
            "user_id": user_id,
            "createdAt": datetime.utcnow(),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def append_conversation(
        self,
        profile_id: str,
        agent_content: Any,
        user_content: Any,
    ) -> Optional[dict]:
        """
        Append one agent message (question) and one user message (answer) to the profile's conversation array.
        content can be str or list (e.g. for topics/target_audiences).
        """
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return None
        result = await self.collection.update_one(
            {"_id": oid},
            {
                "$push": {
                    "conversation": {
                        "$each": [
                            {"role": "agent", "content": agent_content},
                            {"role": "user", "content": user_content},
                        ]
                    }
                }
            },
        )
        if result.matched_count == 0:
            return None
        return await self.get_profile(profile_id)

    async def update_last_assistant_message(self, profile_id: str, message: str) -> Optional[dict]:
        """Store the last AI-generated assistant message so it can be used as the agent content for the next step's conversation entry."""
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return None
        result = await self.collection.update_one(
            {"_id": oid},
            {"$set": {"last_assistant_message": message, "updatedAt": datetime.utcnow()}},
        )
        if result.matched_count == 0:
            return None
        return await self.get_profile(profile_id)

    async def update_step(
        self,
        profile_id: str,
        updates: Dict[str, Any],
        next_step_name: Optional[str],
        completed_steps: List[str],
        last_assistant_message: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Update profile with new field values and progress.
        Only allowed profile fields are written; current_step and completed_steps are set.
        Optionally set last_assistant_message (AI-generated transition shown before the next step).
        """
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return None
        allowed_updates = {k: v for k, v in updates.items() if k in PROFILE_FIELDS}
        allowed_updates["current_step"] = next_step_name
        allowed_updates["completed_steps"] = completed_steps
        allowed_updates["updatedAt"] = datetime.utcnow()
        if last_assistant_message is not None:
            allowed_updates["last_assistant_message"] = last_assistant_message
        result = await self.collection.update_one(
            {"_id": oid},
            {"$set": allowed_updates},
        )
        if result.matched_count == 0:
            return None
        return await self.get_profile(profile_id)

    async def update_profile(self, profile_id: str, updates: Dict[str, Any]) -> Optional[dict]:
        """
        Update profile with given field values. Only PROFILE_FIELDS are applied.
        Does not change user_id, conversation, current_step, completed_steps, etc.
        """
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return None
        allowed = {k: v for k, v in updates.items() if k in PROFILE_FIELDS}
        if not allowed:
            return await self.get_profile(profile_id)
        allowed["updatedAt"] = datetime.utcnow()
        result = await self.collection.update_one(
            {"_id": oid},
            {"$set": allowed},
        )
        if result.matched_count == 0:
            return None
        return await self.get_profile(profile_id)

    async def get_profile(self, profile_id: str) -> Optional[dict]:
        """Return profile document by id, or None if not found."""
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return None
        doc = await self.collection.find_one({"_id": oid})
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
            doc = self._normalize_professional_memberships_for_response(doc)
        return doc

    async def delete_profile(self, profile_id: str) -> bool:
        """Delete speaker profile by id. Returns True if a document was removed."""
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return False
        result = await self.collection.delete_one({"_id": oid})
        return result.deleted_count > 0

    async def delete_profile_for_user(self, profile_id: str, user_id: str) -> bool:
        """
        Delete a speaker profile only if it exists and its user_id matches the given user
        (compares string forms so string/ObjectId storage both match).
        """
        uid = (user_id or "").strip()
        if not uid:
            return False
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return False
        doc = await self.collection.find_one({"_id": oid})
        if not doc:
            return False
        owner = doc.get("user_id")
        if owner is None:
            return False
        if str(owner) != uid:
            return False
        result = await self.collection.delete_one({"_id": oid})
        return result.deleted_count > 0

    async def get_profile_by_id_and_user(self, profile_id: str, user_id: str) -> Optional[dict]:
        """Return profile document by id and user_id, or None if not found."""
        try:
            oid = ObjectId(profile_id)
        except Exception:
            return None
        doc = await self.collection.find_one({"user_id": user_id})
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc

    async def get_profile_by_email(self, email: str) -> Optional[dict]:
        """Return profile document by email (case-insensitive)."""
        if not email or not isinstance(email, str):
            return None
        doc = await self.collection.find_one({"email": {"$regex": f"^{email.strip().lower()}$", "$options": "i"}})
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
            doc = self._normalize_professional_memberships_for_response(doc)
        return doc

    async def get_all_profiles(self) -> List[dict]:
        """Return all speaker profiles, newest first. For admin use."""
        cursor = self.collection.find({}).sort("createdAt", -1)
        docs = await cursor.to_list(length=None)
        for doc in docs:
            if doc and "_id" in doc:
                doc["_id"] = str(doc["_id"])
                self._normalize_professional_memberships_for_response(doc)
        return docs

    async def assign_profiles_to_user(
        self, profile_ids: List[str], user_id: str
    ) -> Dict[str, int]:
        """
        Set user_id on existing speaker_profiles matched by _id.
        Returns matched_count and modified_count from update_many.
        """
        uid = str(user_id).strip()
        if not uid or not profile_ids:
            return {"matched": 0, "modified": 0}
        oids: List[ObjectId] = []
        for pid in profile_ids:
            if not pid or not str(pid).strip():
                continue
            try:
                oids.append(ObjectId(str(pid).strip()))
            except Exception:
                continue
        if not oids:
            return {"matched": 0, "modified": 0}
        now = datetime.utcnow()
        result = await self.collection.update_many(
            {"_id": {"$in": oids}},
            {"$set": {"user_id": uid, "updatedAt": now}},
        )
        return {
            "matched": result.matched_count,
            "modified": result.modified_count,
        }

    async def get_profiles_by_user_id(self, user_id: str) -> List[dict]:
        """Return all speaker profiles for the given user_id, newest first (no limit)."""
        uid = (user_id or "").strip()
        if not uid:
            return []
        cursor = self.collection.find(_user_id_query_filter(uid)).sort("createdAt", -1)
        docs = await cursor.to_list(length=None)
        for doc in docs:
            if doc and "_id" in doc:
                doc["_id"] = str(doc["_id"])
                self._normalize_professional_memberships_for_response(doc)
        return docs

    async def get_profiles_by_user_ids(
        self, user_ids: List[str]
    ) -> Dict[str, List[dict]]:
        """
        Return speaker profiles grouped by user_id (newest first within each group).
        Skips empty/None ids. Users with no profiles are omitted from the dict.
        """
        ids = [str(uid).strip() for uid in user_ids if uid and str(uid).strip()]
        if not ids:
            return {}
        in_values: List[Any] = []
        for u in ids:
            in_values.append(u)
            try:
                in_values.append(ObjectId(u))
            except Exception:
                pass
        cursor = self.collection.find({"user_id": {"$in": in_values}})
        docs = await cursor.to_list(length=None)
        grouped: Dict[str, List[dict]] = {}
        for doc in docs:
            if not doc:
                continue
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
                self._normalize_professional_memberships_for_response(doc)
            uid = doc.get("user_id")
            if uid is None:
                continue
            grouped.setdefault(str(uid), []).append(doc)
        for uid, lst in grouped.items():
            lst.sort(
                key=lambda d: d.get("createdAt") or datetime.min,
                reverse=True,
            )
        return grouped

    async def create_speaker_profile(self, user_id: str, profile_data: dict) -> dict:
        """
        Insert a new speaker profile (full save). profile_data must include all profile fields.
        Adds user_id and createdAt.
        """
        doc = {
            **profile_data,
            "user_id": user_id,
            "createdAt": datetime.utcnow(),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    def _sanitize_chatbot_profile_data(self, data: dict) -> dict:
        """Keep only allowed profile fields; exclude conversation, completed_steps, last_assistant_message, current_step."""
        exclude = {"conversation", "completed_steps", "last_assistant_message", "current_step"}
        return {k: v for k, v in data.items() if k in PROFILE_FIELDS and k not in exclude}

    async def create_chatbot_profile(self, profile_data: dict, user_id: Optional[str] = None) -> dict:
        """
        Create speaker profile from chatbot flow. profile_data must include at least email.
        Other fields (mandatory/optional) are added via updates as user provides them.
        No conversation, completed_steps, last_assistant_message, current_step.
        """
        sanitized = self._sanitize_chatbot_profile_data(profile_data)
        doc = {
            **sanitized,
            "user_id": user_id,
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        if isinstance(doc["_id"], ObjectId):
            doc["_id"] = str(doc["_id"])
        return doc

    async def update_chatbot_profile(self, email: str, profile_data: dict) -> Optional[dict]:
        """
        Update speaker profile by email. Only allowed profile fields; excludes conversation, etc.
        """
        if not email or not isinstance(email, str):
            return None
        sanitized = self._sanitize_chatbot_profile_data(profile_data)
        if not sanitized:
            return await self.get_profile_by_email(email)
        sanitized["updatedAt"] = datetime.utcnow()
        result = await self.collection.find_one_and_update(
            {"email": {"$regex": f"^{email.strip().lower()}$", "$options": "i"}},
            {"$set": sanitized},
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            return None
        result["_id"] = str(result["_id"])
        return result
