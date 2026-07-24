from app.helpers.Database import MongoDB
from bson import ObjectId
from pymongo import ReturnDocument
from app.schemas.Scraper import ScraperSchema
import os
from typing import Optional


class ScraperModel:
    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="Scrapers"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def create(self, data: dict) -> str:
        result = await self.collection.insert_one(data)
        return str(result.inserted_id)

    async def get_by_id(self, scraper_id: str, user_id: str = None) -> ScraperSchema | None:
        query = {"_id": ObjectId(scraper_id)}
        if user_id is not None:
            query["userId"] = user_id
        doc = await self.collection.find_one(query)
        if doc:
            return ScraperSchema(**doc)
        return None

    async def get_doc_by_id(self, scraper_id: str, user_id: str = None) -> Optional[dict]:
        """Raw document lookup (supports new fields like name/status)."""
        query = {"_id": ObjectId(scraper_id)}
        if user_id is not None:
            query["userId"] = user_id
        return await self.collection.find_one(query)

    async def get_list_summary(
        self, skip: int = 0, limit: int = 100
    ) -> list[dict]:
        """
        List Scrapers with only id, name, url, status, createdAt.
        Order by status: running → completed → pending → failed/other, then createdAt desc.
        """
        pipeline = [
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
            {"$project": {"name": 1, "url": 1, "status": 1, "createdAt": 1}},
            {"$skip": int(skip)},
            {"$limit": int(limit)},
        ]
        items: list[dict] = []
        async for doc in self.collection.aggregate(pipeline):
            items.append(
                {
                    "id": str(doc["_id"]),
                    "name": doc.get("name"),
                    "url": doc.get("url"),
                    "status": doc.get("status"),
                    "createdAt": doc.get("createdAt"),
                }
            )
        return items

    async def count_all(self) -> int:
        return await self.collection.count_documents({})

    async def get_list(self, user_id: str, skip: int = 0, limit: int = 100, sort_by: dict = None) -> list[ScraperSchema]:
        if sort_by is None:
            sort_by = {"createdAt": -1}
        cursor = (
            self.collection.find({"userId": user_id})
            .sort(list(sort_by.items()))
            .skip(skip)
            .limit(limit)
        )
        return [ScraperSchema(**doc) async for doc in cursor]

    async def count(self, user_id: str) -> int:
        return await self.collection.count_documents({"userId": user_id})

    async def update(self, scraper_id: str, user_id: str, update_data: dict) -> bool:
        result = await self.collection.update_one(
            {"_id": ObjectId(scraper_id), "userId": user_id},
            {"$set": update_data},
        )
        return result.modified_count > 0

    async def update_by_id(self, scraper_id: str, update_data: dict) -> bool:
        """Update Scrapers by id only (used by cron scrape jobs)."""
        result = await self.collection.update_one(
            {"_id": ObjectId(scraper_id)},
            {"$set": update_data},
        )
        return result.modified_count > 0

    async def claim_pending_jobs(self, limit: int = 10) -> list[dict]:
        """
        Atomically claim up to `limit` Scrapers with status \"pending\" (oldest first).
        Each claimed doc is set to status \"running\".
        """
        claimed: list[dict] = []
        for _ in range(limit):
            doc = await self._claim_one_pending()
            if doc is None:
                break
            claimed.append(doc)
        return claimed

    async def claim_all_pending_jobs(self) -> list[dict]:
        """Claim every Scrapers document with status \"pending\" (no cap)."""
        claimed: list[dict] = []
        while True:
            doc = await self._claim_one_pending()
            if doc is None:
                break
            claimed.append(doc)
        return claimed

    async def _claim_one_pending(self) -> Optional[dict]:
        return await self.collection.find_one_and_update(
            {"status": "pending"},
            {"$set": {"status": "running"}},
            sort=[("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def delete(self, scraper_id: str, user_id: str) -> bool:
        result = await self.collection.delete_one(
            {"_id": ObjectId(scraper_id), "userId": user_id}
        )
        return result.deleted_count > 0

    async def delete_by_id(self, scraper_id: str) -> bool:
        """Delete a Scrapers document by id only."""
        result = await self.collection.delete_one({"_id": ObjectId(scraper_id)})
        return result.deleted_count > 0
