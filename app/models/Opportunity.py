import os
from datetime import date, datetime
from typing import Any, List, Optional, Sequence
from urllib.parse import urlparse, urlunparse

import certifi
from bson import ObjectId
from pymongo import MongoClient

from app.helpers.Database import MongoDB

# ISO date strings compare lexicographically; "deadline not found" is treated as still open (future).
# Deadline lives under submissionInfo.deadline (string "YYYY-MM-DD" or "deadline not found").
_DEADLINE_FIELD = "submissionInfo.deadline"
_DEADLINE_ISO_PREFIX = {"$regex": r"^\d{4}-\d{2}-\d{2}"}
_DEADLINE_NOT_FOUND = {"$regex": r"^deadline not found$", "$options": "i"}


def deadline_time_filter_query(time_filter: Optional[str]) -> dict:
    """
    Build Mongo filter for submissionInfo.deadline (string "YYYY-MM-DD" or "deadline not found").

    - none / empty: no deadline constraint (all opportunities)
    - future: deadline >= today, or deadline is "deadline not found"
    - past: parseable deadline < today
    """
    key = (time_filter or "").strip().lower()
    if not key or key in ("none", "all"):
        return {}
    today_str = date.today().isoformat()
    field = _DEADLINE_FIELD
    if key == "future":
        return {
            "$or": [
                {
                    "$and": [
                        {field: _DEADLINE_ISO_PREFIX},
                        {field: {"$gte": today_str}},
                    ]
                },
                {field: _DEADLINE_NOT_FOUND},
            ]
        }
    if key == "past":
        return {
            "$and": [
                {field: _DEADLINE_ISO_PREFIX},
                {field: {"$lt": today_str}},
            ]
        }
    raise ValueError("filter must be one of: future, past, none")


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


def normalize_opportunity_url(url: str) -> str:
    """Normalize URL for link-level duplicate checks."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        path = (p.path or "").rstrip("/")
        return urlunparse((p.scheme.lower(), (p.netloc or "").lower(), path, "", p.query, ""))
    except Exception:
        return raw.rstrip("/")


def _url_match_variants(url: str) -> list[str]:
    """Variants to match against stored link/source_url values."""
    raw = (url or "").strip()
    if not raw:
        return []
    variants = {raw, raw.rstrip("/")}
    norm = normalize_opportunity_url(raw)
    if norm:
        variants.add(norm)
        variants.add(norm.rstrip("/"))
        if "://" in norm and norm.count("/") == 2:
            variants.add(norm + "/")
    return [v for v in variants if v]


class OpportunityModel:
    """Model for Opportunities - each opportunity stored at root level."""

    def __init__(self, db_name=os.getenv("DB_NAME"), collection_name="Opportunities"):
        self.db_name = db_name
        self.collection_name = collection_name
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
                unique_links.update(_url_match_variants(link))
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

    async def find_urls_already_known(self, urls: Sequence[str]) -> set[str]:
        """
        Return the subset of input URLs that already appear as opportunity.link
        or source.source_url (resource-saving pre-scrape skip).
        """
        if not urls:
            return set()
        variant_to_original: dict[str, str] = {}
        all_variants: list[str] = []
        for url in urls:
            raw = (url or "").strip()
            if not raw:
                continue
            for v in _url_match_variants(raw):
                variant_to_original.setdefault(v, raw)
                all_variants.append(v)
        if not all_variants:
            return set()

        known_originals: set[str] = set()
        cursor = self.collection.find(
            {
                "$or": [
                    {"link": {"$in": all_variants}},
                    {"source.source_url": {"$in": all_variants}},
                ]
            },
            projection={"link": 1, "source.source_url": 1},
        )
        async for doc in cursor:
            src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
            for field in ((doc.get("link") or "").strip(), (src.get("source_url") or "").strip()):
                field = str(field).strip()
                if not field:
                    continue
                if field in variant_to_original:
                    known_originals.add(variant_to_original[field])
                for v in _url_match_variants(field):
                    if v in variant_to_original:
                        known_originals.add(variant_to_original[v])
        return known_originals

    @staticmethod
    def find_urls_already_known_sync(urls: Sequence[str]) -> set[str]:
        """Sync variant for thread-pool scrape pipeline (hop pre-check)."""
        if not urls:
            return set()
        connection_string = os.getenv("MONGODB_CONNECTION_STRING")
        db_name = os.getenv("DB_NAME")
        if not connection_string or not db_name:
            return set()

        variant_to_original: dict[str, str] = {}
        all_variants: list[str] = []
        for url in urls:
            raw = (url or "").strip()
            if not raw:
                continue
            for v in _url_match_variants(raw):
                variant_to_original.setdefault(v, raw)
                all_variants.append(v)
        if not all_variants:
            return set()

        client = MongoClient(connection_string, tlsCAFile=certifi.where())
        try:
            col = client[db_name]["Opportunities"]
            known_originals: set[str] = set()
            cursor = col.find(
                {
                    "$or": [
                        {"link": {"$in": all_variants}},
                        {"source.source_url": {"$in": all_variants}},
                    ]
                },
                projection={"link": 1, "source.source_url": 1},
            )
            for doc in cursor:
                src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
                for field in ((doc.get("link") or "").strip(), (src.get("source_url") or "").strip()):
                    field = str(field).strip()
                    if not field:
                        continue
                    if field in variant_to_original:
                        known_originals.add(variant_to_original[field])
                    for v in _url_match_variants(field):
                        if v in variant_to_original:
                            known_originals.add(variant_to_original[v])
            return known_originals
        finally:
            client.close()

    @staticmethod
    def find_existing_dedupe_keys_sync(candidates: Sequence[dict]) -> set[tuple[str, str]]:
        """Sync dedupe-key lookup for hop candidates before scrape."""
        if not candidates:
            return set()
        connection_string = os.getenv("MONGODB_CONNECTION_STRING")
        db_name = os.getenv("DB_NAME")
        if not connection_string or not db_name:
            return set()

        unique_links: set[str] = set()
        for c in candidates:
            link = (c.get("link") or c.get("url") or "").strip()
            if link:
                unique_links.update(_url_match_variants(link))
        if not unique_links:
            return set()

        client = MongoClient(connection_string, tlsCAFile=certifi.where())
        try:
            col = client[db_name]["Opportunities"]
            existing: set[tuple[str, str]] = set()
            cursor = col.find(
                {"link": {"$in": list(unique_links)}},
                projection={"link": 1, "event_name": 1, "title": 1},
            )
            for doc in cursor:
                k = opportunity_dedupe_key(doc)
                if k:
                    existing.add(k)
            return existing
        finally:
            client.close()

    async def get_list(
        self,
        skip: int = 0,
        limit: int = 10,
        sort_by: dict = None,
        query: Optional[dict] = None,
        sort_by_deadline: Optional[str] = "asc",
    ) -> list[dict]:
        """
        Get opportunities with pagination.
        sort_by_deadline: asc (default) | desc. ISO deadlines sort first;
        'deadline not found' / missing / non-date values are always last.
        """
        if sort_by is None:
            sort_by = {"_id": -1}
        filt: dict[str, Any] = dict(query or {})
        deadline_dir = (sort_by_deadline or "asc").strip().lower()
        if deadline_dir not in ("asc", "desc"):
            raise ValueError("sort_by_deadline must be asc or desc")

        deadline_order = 1 if deadline_dir == "asc" else -1
        deadline_expr = {"$ifNull": [f"${_DEADLINE_FIELD}", ""]}
        is_iso_date = {
            "$regexMatch": {
                "input": deadline_expr,
                "regex": r"^\d{4}-\d{2}-\d{2}",
            }
        }
        sort_spec: dict[str, int] = {
            "_deadlineMissing": 1,  # dated rows first; not-found / invalid always last
            "_deadlineSort": deadline_order,
        }
        for field, direction in sort_by.items():
            if field not in sort_spec:
                sort_spec[field] = direction

        pipeline: list[dict[str, Any]] = [
            {"$match": filt},
            {
                "$addFields": {
                    "_deadlineMissing": {"$cond": [is_iso_date, 0, 1]},
                    "_deadlineSort": {
                        "$cond": [
                            is_iso_date,
                            {"$substrBytes": [deadline_expr, 0, 10]},
                            "",
                        ]
                    },
                }
            },
            {"$sort": sort_spec},
            {"$project": {"_deadlineMissing": 0, "_deadlineSort": 0}},
            {"$skip": int(skip)},
            {"$limit": int(limit)},
        ]
        cursor = self.collection.aggregate(pipeline)
        return [doc async for doc in cursor]

    async def count(self, query: Optional[dict] = None) -> int:
        """Get total count of opportunities (optionally filtered)."""
        return await self.collection.count_documents(dict(query or {}))

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

    async def find_matching_for_speaker(
        self,
        *,
        topics: Sequence[str],
        speaking_formats: Sequence[str],
        delivery_modes: Sequence[str],
        target_audiences: Sequence[str],
    ) -> List[dict]:
        """
        Find opportunities that overlap speaker criteria:
        - topics: at least one shared value (array)
        - speaking_format: equals one of speaker speaking_formats (string)
        - delivery_mode: equals one of speaker delivery modes (string)
        - target_audiences: at least one shared value (array)
        - isVerified: must be true (LLM-confirmed speaking opportunity)

        Deadline (submissionInfo.deadline as YYYY-MM-DD):
        - include if deadline >= today
        - include if deadline missing / empty / \"deadline not found\"
        - exclude if parseable deadline is before today
        """
        topics = [t for t in topics if t]
        speaking_formats = [f for f in speaking_formats if f]
        delivery_modes = [d for d in delivery_modes if d]
        target_audiences = [a for a in target_audiences if a]
        if not (topics or speaking_formats or delivery_modes or target_audiences):
            return []

        and_clauses: list[dict] = [{"isVerified": True}]
        if topics:
            and_clauses.append({"topics": {"$in": list(topics)}})
        if speaking_formats:
            and_clauses.append({"speaking_format": {"$in": list(speaking_formats)}})
        if delivery_modes:
            and_clauses.append({"delivery_mode": {"$in": list(delivery_modes)}})
        if target_audiences:
            and_clauses.append({"target_audiences": {"$in": list(target_audiences)}})

        today_str = date.today().isoformat()
        and_clauses.append(
            {
                "$or": [
                    {
                        "$and": [
                            {_DEADLINE_FIELD: _DEADLINE_ISO_PREFIX},
                            {_DEADLINE_FIELD: {"$gte": today_str}},
                        ]
                    },
                    {_DEADLINE_FIELD: _DEADLINE_NOT_FOUND},
                    {_DEADLINE_FIELD: {"$exists": False}},
                    {_DEADLINE_FIELD: None},
                    {_DEADLINE_FIELD: ""},
                    {"submissionInfo": {"$exists": False}},
                    {"submissionInfo": None},
                ]
            }
        )

        cursor = self.collection.find({"$and": and_clauses}).sort([("createdAt", -1)])
        return [doc async for doc in cursor]
