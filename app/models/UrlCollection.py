from pymongo import ReturnDocument

from app.helpers.Database import MongoDB
from bson import ObjectId
import os


class UrlCollectionModel:
    """Model for UrlCollection - stores url, createdAt, sourceName, description."""

    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="UrlCollections"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def create(self, data: dict) -> str:
        result = await self.collection.insert_one(data)
        return str(result.inserted_id)

    async def get_by_id(self, url_collection_id: str, user_id: str = None) -> dict | None:
        query = {"_id": ObjectId(url_collection_id)}
        if user_id is not None:
            query["userId"] = user_id
        doc = await self.collection.find_one(query)
        return doc

    async def update_by_id(self, url_collection_id: str, update_data: dict) -> bool:
        """Update UrlCollection by ID. Returns True if modified."""
        result = await self.collection.update_one(
            {"_id": ObjectId(url_collection_id)},
            {"$set": update_data},
        )
        return result.modified_count > 0

    async def get_pending(self, limit: int = 5, sort_by: dict = None) -> list[dict]:
        """Get UrlCollection entries with status \"pending\" (or no status for backward compatibility). Oldest first. For cron job."""
        if sort_by is None:
            sort_by = {"createdAt": 1}
        query = {"$or": [{"status": "pending"}, {"status": {"$exists": False}}]}
        cursor = (
            self.collection.find(query)
            .sort(list(sort_by.items()))
            .limit(limit)
        )
        return [doc async for doc in cursor]

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
            {"$set": {"status": "running"}},
            sort=[("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def get_list(self, user_id: str = None, skip: int = 0, limit: int = 100, sort_by: dict = None) -> list[dict]:
        """Get UrlCollection entries with pagination. Optionally filter by user_id."""
        if sort_by is None:
            sort_by = {"createdAt": -1}
        query = {}
        if user_id is not None:
            query["userId"] = user_id
        cursor = (
            self.collection.find(query)
            .sort(list(sort_by.items()))
            .skip(skip)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def count(self, user_id: str = None) -> int:
        """Get total count. Optionally filter by user_id."""
        query = {}
        if user_id is not None:
            query["userId"] = user_id
        return await self.collection.count_documents(query)

    async def delete_by_id(self, url_collection_id: str, user_id: str = None) -> bool:
        """Delete by ID. Optionally require user_id match."""
        query = {"_id": ObjectId(url_collection_id)}
        if user_id is not None:
            query["userId"] = user_id
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0
