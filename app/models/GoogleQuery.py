import os
from datetime import datetime

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

    async def get_list(
        self, user_id: str | None = None, skip: int = 0, limit: int = 100, sort_by: dict | None = None
    ) -> list[dict]:
        """
        Get GoogleQueries with pagination.
        Default order by status: running → completed → pending → failed/other,
        then createdAt desc within each status.
        """
        query: dict = {}
        if user_id is not None:
            query["userId"] = user_id

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

    async def count(self, user_id: str | None = None) -> int:
        """Total count. Optionally filter by user_id."""
        query = {}
        if user_id is not None:
            query["userId"] = user_id
        return await self.collection.count_documents(query)

    async def delete_by_id(self, google_query_id: str, user_id: str | None = None) -> bool:
        """Delete a GoogleQuery by _id. Optionally restrict to user_id (own record only)."""
        query = {"_id": ObjectId(google_query_id)}
        if user_id is not None:
            query["userId"] = user_id
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0

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

