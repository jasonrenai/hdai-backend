import os
from datetime import datetime
from typing import List

from bson import ObjectId

from app.helpers.Database import MongoDB


def opportunity_dedupe_key(opp: dict) -> tuple[str, str] | None:
    """
    Identity for duplicate detection: (stripped link, normalized event name).
    Aligns with SpeakingOpportunityExtractor._deduplicate_opportunities (event_name lower[:100]).
    """
    link = (opp.get("link") or opp.get("url") or "").strip()
    event_name = (opp.get("event_name") or opp.get("title") or "").strip().lower()[:100]
    if not link or not event_name:
        return None
    return (link, event_name)


class OpportunityModel:
    """Model for Opportunities - each opportunity stored at root level."""

    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="Opportunities"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def insert_many(self, opportunities: list[dict]) -> list[str]:
        """Insert multiple opportunities as root-level documents."""
        if not opportunities:
            return []
        now = datetime.utcnow()
        payload: list[dict] = []
        for opp in opportunities:
            doc = dict(opp)
            doc.setdefault("createdAt", now)
            payload.append(doc)
        result = await self.collection.insert_many(payload)
        return [str(oid) for oid in result.inserted_ids]

    async def find_existing_dedupe_keys(self, opportunities: list[dict]) -> set[tuple[str, str]]:
        """Keys (link, event_name_norm) already present in the collection for the given links."""
        unique_links: set[str] = set()
        for o in opportunities:
            link = (o.get("link") or o.get("url") or "").strip()
            if link:
                unique_links.add(link)
        if not unique_links:
            return set()
        existing: set[tuple[str, str]] = set()
        cursor = self.collection.find(
            {"link": {"$in": list(unique_links)}},
            projection={"link": 1, "event_name": 1, "title": 1},
        )
        async for doc in cursor:
            k = opportunity_dedupe_key(doc)
            if k:
                existing.add(k)
        return existing

    async def get_list(self, skip: int = 0, limit: int = 10, sort_by: dict = None) -> list[dict]:
        """Get opportunities with pagination. Returns list of documents."""
        if sort_by is None:
            sort_by = {"_id": -1}
        cursor = (
            self.collection.find({})
            .sort(list(sort_by.items()))
            .skip(skip)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def count(self) -> int:
        """Get total count of opportunities."""
        return await self.collection.count_documents({})

    async def delete_by_id(self, opportunity_id: str) -> bool:
        """Delete an opportunity by ID. Returns True if deleted."""
        result = await self.collection.delete_one({"_id": ObjectId(opportunity_id)})
        return result.deleted_count > 0

    async def get_by_id(self, opportunity_id: str) -> dict | None:
        """Get a single opportunity by ID."""
        doc = await self.collection.find_one({"_id": ObjectId(opportunity_id)})
        return doc

    async def get_by_ids(self, opportunity_ids: List[str]) -> List[dict]:
        """Get opportunities by list of IDs. Returns list in same order as ids; skips invalid/not-found ids."""
        if not opportunity_ids:
            return []
        oids = []
        for sid in opportunity_ids:
            try:
                oids.append(ObjectId(sid))
            except Exception:
                continue
        if not oids:
            return []
        cursor = self.collection.find({"_id": {"$in": oids}})
        docs = await cursor.to_list(length=len(oids))
        id_to_doc = {str(d["_id"]): d for d in docs}
        result = []
        for sid in opportunity_ids:
            if sid in id_to_doc:
                result.append(id_to_doc[sid])
        return result
