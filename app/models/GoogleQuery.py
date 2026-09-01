import os
import re
from datetime import datetime, timedelta
from typing import Optional, Sequence

from bson import ObjectId
from pymongo import ReturnDocument

from app.helpers.Database import MongoDB


class GoogleQueryModel:
    """Model for GoogleQueries - stores query, status, urls, and processing metadata."""

    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="GoogleQueries"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def create(self, data: dict) -> str:
        result = await self.collection.insert_one(data)
        return str(result.inserted_id)

    async def get_by_id(self, google_query_id: str, user_id: str | None = None) -> dict | None:
        query = {"_id": ObjectId(google_query_id)}
        if user_id is not None:
            query["userId"] = user_id
        return await self.collection.find_one(query)

    async def update_by_id(self, google_query_id: str, update_data: dict) -> bool:
        result = await self.collection.update_one(
            {"_id": ObjectId(google_query_id)},
            {"$set": update_data},
        )
        return result.modified_count > 0

    async def set_related_topics(self, google_query_id: str, topics: list[str]) -> bool:
        """Persist catalog topic tags for a GoogleQuery."""
        return await self.update_by_id(
            google_query_id,
            {
                "relatedTopics": list(topics or []),
                "updatedAt": datetime.utcnow(),
            },
        )

    @staticmethod
    def _build_filter(
        user_id: str | None = None,
        search: str | None = None,
        related_topics: Optional[Sequence[str]] = None,
        topics: Optional[Sequence[str]] = None,
    ) -> dict:
        """Build Mongo filter for list/count (text search + relatedTopics + topic)."""
        clauses: list[dict] = []
        if user_id is not None:
            clauses.append({"userId": user_id})

        search_text = (search or "").strip()
        if search_text:
            clauses.append(
                {"query": {"$regex": re.escape(search_text), "$options": "i"}}
            )

        related = [str(t).strip() for t in (related_topics or []) if str(t or "").strip()]
        if related:
            clauses.append({"relatedTopics": {"$in": related}})

        topic_values = [str(t).strip() for t in (topics or []) if str(t or "").strip()]
        if topic_values:
            clauses.append({"topic": {"$in": topic_values}})

        if not clauses:
            return {}
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    async def get_list(
        self,
        user_id: str | None = None,
        skip: int = 0,
        limit: int = 100,
        sort_by: dict | None = None,
        search: str | None = None,
        related_topics: Optional[Sequence[str]] = None,
        topics: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        """
        Get GoogleQueries with pagination.
        Default order by status: running → completed → pending → failed/other,
        then createdAt desc within each status.
        Optional case-insensitive ``search`` on query text, ``related_topics`` ($in),
        and user-provided ``topics`` ($in on topic field).
        """
        query = self._build_filter(
            user_id=user_id,
            search=search,
            related_topics=related_topics,
            topics=topics,
        )

        # Explicit custom order when no override sort is provided.
        if sort_by is None:
            pipeline = [
                {"$match": query},
                {
                    "$addFields": {
                        "_statusOrder": {
                            "$switch": {
                                "branches": [
                                    {"case": {"$eq": ["$status", "running"]}, "then": 0},
                                    {"case": {"$eq": ["$status", "completed"]}, "then": 1},
                                    {"case": {"$eq": ["$status", "pending"]}, "then": 2},
                                    {"case": {"$eq": ["$status", "failed"]}, "then": 3},
                                ],
                                "default": 4,
                            }
                        }
                    }
                },
                {"$sort": {"_statusOrder": 1, "createdAt": -1}},
                {"$project": {"_statusOrder": 0}},
                {"$skip": int(skip)},
                {"$limit": int(limit)},
            ]
            cursor = self.collection.aggregate(pipeline)
            return [doc async for doc in cursor]

        cursor = (
            self.collection.find(query)
            .sort(list(sort_by.items()))
            .skip(skip)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def count(
        self,
        user_id: str | None = None,
        search: str | None = None,
        related_topics: Optional[Sequence[str]] = None,
        topics: Optional[Sequence[str]] = None,
    ) -> int:
        """Total count with the same filters as get_list."""
        query = self._build_filter(
            user_id=user_id,
            search=search,
            related_topics=related_topics,
            topics=topics,
        )
        return await self.collection.count_documents(query)

    async def delete_by_id(self, google_query_id: str, user_id: str | None = None) -> bool:
        """Delete a GoogleQuery by _id. Optionally restrict to user_id (own record only)."""
        query = {"_id": ObjectId(google_query_id)}
        if user_id is not None:
            query["userId"] = user_id
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0

    async def reclaim_stale_running_jobs(self, stale_minutes: int = 120) -> int:
        """
        Reset ``running`` jobs whose updatedAt is older than ``stale_minutes`` back to
        ``pending``. Used at cron start so a killed/redeployed worker does not leave
        jobs stuck forever. Active jobs keep updating updatedAt during scrape.
        """
        stale_minutes = max(1, int(stale_minutes))
        cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
        result = await self.collection.update_many(
            {
                "status": "running",
                "$or": [
                    {"updatedAt": {"$lt": cutoff}},
                    {"updatedAt": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "status": "pending",
                    "error": None,
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        return int(result.modified_count)

    async def claim_pending_jobs(self, limit: int = 10) -> list[dict]:
        """
        Atomically claim up to `limit` documents with status \"pending\" (oldest by createdAt first).
        Each claimed doc is set to status \"running\" so concurrent workers do not duplicate work.
        """
        claimed: list[dict] = []
        for _ in range(limit):
            doc = await self._claim_one_pending()
            if doc is None:
                break
            claimed.append(doc)
        return claimed

    async def claim_all_pending_jobs(self) -> list[dict]:
        """
        Deprecated for cron use — prefer claim_pending_jobs(limit=1) in a loop so only one
        job is ``running`` at a time. Kept for callers that still need a bulk claim.
        """
        claimed: list[dict] = []
        while True:
            doc = await self._claim_one_pending()
            if doc is None:
                break
            claimed.append(doc)
        return claimed

    async def _claim_one_pending(self) -> dict | None:
        return await self.collection.find_one_and_update(
            {"status": "pending"},
            {
                "$set": {
                    "status": "running",
                    "updatedAt": datetime.utcnow(),
                    "error": None,
                }
            },
            sort=[("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )
