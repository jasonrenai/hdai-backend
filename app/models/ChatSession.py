from app.helpers.Database import MongoDB
from bson import ObjectId
from datetime import datetime
import os
from typing import List, Optional, Dict, Any


class ChatSessionModel:
    """
    Chat session for speaker profile chatbot.
    Stores conversation history per speaker_profile_id.
    """

    def __init__(self, db_name: Optional[str] = None, collection_name: str = "chatSessions"):
        self.collection = MongoDB.get_database(db_name or os.getenv("DB_NAME"))[collection_name]

    async def create_session(
        self,
        speaker_profile_id: str,
        messages: List[Dict[str, Any]],
    ) -> dict:
        """
        Create a new chat session for a speaker profile.
        messages: list of {"role": "user"|"assistant", "content": str}
        """
        doc = {
            "speaker_profile_id": speaker_profile_id,
            "conversation": messages or [],
            "speakerpitcher_welcome_sent": False,
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        return doc

    async def append_messages(
        self,
        chat_session_id: str,
        messages: List[Dict[str, Any]],
    ) -> Optional[dict]:
        """
        Append messages to an existing chat session's conversation.
        """
        if not messages:
            return await self.get_by_id(chat_session_id)
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$push": {"conversation": {"$each": messages}},
                "$set": {"updatedAt": datetime.utcnow()},
            },
        )
        return await self.get_by_id(chat_session_id)

    async def update_onboarding_steps_done(
        self,
        chat_session_id: str,
        steps_done: List[str],
    ) -> Optional[dict]:
        """Persist completed onboarding step ids for chatbot flow control."""
        if not chat_session_id:
            return None
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "onboarding_steps_done": list(steps_done or []),
                    "updatedAt": datetime.utcnow(),
                },
            },
        )
        return await self.get_by_id(chat_session_id)

    async def update_speaker_profile_id(
        self,
        chat_session_id: str,
        speaker_profile_id: str,
    ) -> Optional[dict]:
        """Update speaker_profile_id on an existing chat session (e.g. when profile is created in a follow-up call)."""
        if not speaker_profile_id or not str(speaker_profile_id).strip():
            return await self.get_by_id(chat_session_id)
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "speaker_profile_id": str(speaker_profile_id).strip(),
                    "updatedAt": datetime.utcnow(),
                },
            },
        )
        return await self.get_by_id(chat_session_id)

    async def update_pending_identity(
        self,
        chat_session_id: str,
        identity: Optional[Dict[str, Any]],
    ) -> Optional[dict]:
        """Store parsed name/title/company between pre-create chat turns."""
        if not chat_session_id:
            return None
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "pending_identity": identity,
                    "updatedAt": datetime.utcnow(),
                },
            },
        )
        return await self.get_by_id(chat_session_id)

    async def update_conversation_status(
        self,
        chat_session_id: str,
        status: str,
    ) -> Optional[dict]:
        """Set conversation_status: IN_PROGRESS | COMPLETED | QUIT."""
        if not chat_session_id:
            return None
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "conversation_status": (status or "IN_PROGRESS").upper(),
                    "updatedAt": datetime.utcnow(),
                },
            },
        )
        return await self.get_by_id(chat_session_id)

    async def update_skipped_questions(
        self,
        chat_session_id: str,
        skipped: List[str],
    ) -> Optional[dict]:
        if not chat_session_id:
            return None
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "skipped_questions": list(skipped or []),
                    "updatedAt": datetime.utcnow(),
                },
            },
        )
        return await self.get_by_id(chat_session_id)

    async def update_pending_confirmation(
        self,
        chat_session_id: str,
        pending: Optional[Dict[str, Any]],
    ) -> Optional[dict]:
        """Store or clear pending field conflict confirmation."""
        if not chat_session_id:
            return None
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "pending_confirmation": pending,
                    "updatedAt": datetime.utcnow(),
                },
            },
        )
        return await self.get_by_id(chat_session_id)

    async def update_speakerpitcher_welcome_sent(
        self,
        chat_session_id: str,
        sent: bool = True,
    ) -> Optional[dict]:
        """Mark that the one-time SpeakerPitcher joining welcome was already sent."""
        if not chat_session_id:
            return None
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        await self.collection.update_one(
            {"_id": oid},
            {
                "$set": {
                    "speakerpitcher_welcome_sent": bool(sent),
                    "updatedAt": datetime.utcnow(),
                },
            },
        )
        return await self.get_by_id(chat_session_id)

    async def get_by_id(self, chat_session_id: str) -> Optional[dict]:
        """Get chat session by id."""
        try:
            oid = ObjectId(chat_session_id)
        except Exception:
            return None
        doc = await self.collection.find_one({"_id": oid})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return doc

    async def get_by_profile_id(self, speaker_profile_id: str) -> List[dict]:
        """Get all chat sessions for a speaker profile (newest first)."""
        cursor = (
            self.collection.find({"speaker_profile_id": speaker_profile_id})
            .sort("createdAt", -1)
        )
        sessions = await cursor.to_list(length=None)
        for s in sessions:
            if "_id" in s:
                s["_id"] = str(s["_id"])
        return sessions

    async def delete_by_speaker_profile_id(self, speaker_profile_id: str) -> int:
        """Remove all chat sessions linked to a speaker profile. Returns deleted count."""
        if not speaker_profile_id or not str(speaker_profile_id).strip():
            return 0
        result = await self.collection.delete_many(
            {"speaker_profile_id": str(speaker_profile_id).strip()}
        )
        return int(result.deleted_count)

