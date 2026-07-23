"""
Remediate existing Opportunities that have known pipeline issues:
- Weak/missing submissionInfo (contact_found / not_found / contact page as sourceUrl)
- Meetup.com attend-only still marked qualified
- Empty target_audiences

Does NOT re-run full SERP/discovery. Rescrapes the opportunity link (and source page if needed),
reuses OpportunitySubmissionResolver, TargetAudienceCatalog, and qualification clauses.

Run from project root:
  python scripts/remediate_opportunities.py --dry-run --limit 10
  python scripts/remediate_opportunities.py --limit 25
  python scripts/remediate_opportunities.py --all
  python scripts/remediate_opportunities.py --ids 65abc...,66def...
  python scripts/remediate_opportunities.py --skip-pinecone --limit 50

Requires .env: MONGODB_CONNECTION_STRING, DB_NAME, RAPIDAPI_KEY, OPENAI_API_KEY;
PINECONE_API_KEY + PINECONE_INDEX unless --skip-pinecone.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("remediate_opportunities")


def _is_meetup_host(url: str) -> bool:
    host = (urlparse((url or "").strip()).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host == "meetup.com" or host.endswith(".meetup.com")


def _urls_same_page(a: str, b: str) -> bool:
    def key(u: str) -> Tuple[str, str]:
        p = urlparse((u or "").strip())
        return (p.netloc or "").lower(), (p.path or "").rstrip("/").lower()

    return key(a) == key(b)


def _source_url_looks_like_contact(source_url: str) -> bool:
    low = (source_url or "").lower()
    if not low:
        return False
    path = (urlparse(low).path or "").lower()
    return "/contact" in path or path.rstrip("/").endswith("contact-us")


def _audiences_empty(opp: Dict[str, Any]) -> bool:
    audiences = opp.get("target_audiences")
    return not (isinstance(audiences, list) and any(str(a).strip() for a in audiences if a))


def _submission_status(opp: Dict[str, Any]) -> str:
    info = opp.get("submissionInfo")
    if not isinstance(info, dict):
        return ""
    return str(info.get("status") or "").strip().lower()


def _needs_remediation(opp: Dict[str, Any]) -> bool:
    link = (opp.get("link") or opp.get("url") or "").strip()
    if _audiences_empty(opp):
        return True
    status = _submission_status(opp)
    if status in ("not_found", "contact_found", ""):
        return True
    if _is_meetup_host(link):
        return True
    info = opp.get("submissionInfo") if isinstance(opp.get("submissionInfo"), dict) else {}
    source_url = str(info.get("sourceUrl") or "")
    if status != "found" and _source_url_looks_like_contact(source_url):
        return True
    if status == "found" and _source_url_looks_like_contact(source_url):
        # Form may have been missed; contact page wrongly stored as sourceUrl
        return True
    return False


def _broken_opportunities_query() -> dict:
    """Mongo filter for opportunities that likely need remediation."""
    return {
        "$or": [
            {"target_audiences": {"$exists": False}},
            {"target_audiences": None},
            {"target_audiences": []},
            {"target_audiences": {"$not": {"$type": "array"}}},
            {"submissionInfo.status": {"$in": ["not_found", "contact_found"]}},
            {"submissionInfo.status": {"$exists": False}},
            {"submissionInfo": {"$exists": False}},
            {"link": {"$regex": r"meetup\.com", "$options": "i"}},
            {
                "submissionInfo.sourceUrl": {
                    "$regex": r"/contact",
                    "$options": "i",
                }
            },
        ]
    }


def _snapshot_embed_fields(opp: Dict[str, Any]) -> Tuple:
    meta = opp.get("metadata") if isinstance(opp.get("metadata"), dict) else {}
    return (
        tuple(opp.get("topics") or []),
        tuple(opp.get("aipredictedTopics") or []),
        (opp.get("speaking_format") or ""),
        (opp.get("delivery_mode") or ""),
        tuple(opp.get("target_audiences") or []),
        str(meta.get("description") or ""),
    )


def _scrape_page(scraper, url: str) -> Tuple[str, list]:
    """Return (content, urls). Empty content on failure."""
    url = (url or "").strip()
    if not url:
        return "", []
    try:
        result = scraper.scrape(url)
    except Exception as e:
        logger.warning("Scrape failed url=%s err=%s", url[:120], e)
        return "", []
    if not result.get("success"):
        logger.warning(
            "Scrape unsuccessful url=%s err=%s",
            url[:120],
            result.get("error") or "unknown",
        )
        return "", []
    data = result.get("data") or {}
    content = str(data.get("content") or "").strip()
    urls = data.get("urls") or []
    return content, urls if isinstance(urls, list) else []


def _delete_pinecone_opportunity(store, opportunity_id: str) -> bool:
    """Delete a vector from the opportunities namespace when an opp becomes unqualified."""
    try:
        if not store.is_configured():
            return False
        index = store._get_index()
        index.delete(ids=[opportunity_id], namespace=store._namespace)
        logger.info("Pinecone deleted opportunity_id=%s", opportunity_id)
        return True
    except Exception as e:
        logger.warning("Pinecone delete failed for %s: %s", opportunity_id, e)
        return False


async def _fetch_candidates(collection, args) -> List[Dict[str, Any]]:
    from bson import ObjectId

    if args.ids:
        raw_ids = [s.strip() for s in args.ids.split(",") if s.strip()]
        oids = []
        for rid in raw_ids:
            try:
                oids.append(ObjectId(rid))
            except Exception:
                logger.warning("Skipping invalid ObjectId: %s", rid)
        if not oids:
            return []
        cursor = collection.find({"_id": {"$in": oids}})
        docs = await cursor.to_list(length=len(oids))
        return docs

    if getattr(args, "all", False):
        # Every opportunity in the collection (no broken-only filter).
        cursor = collection.find({}).sort("createdAt", -1)
        return await cursor.to_list(length=None)

    query = _broken_opportunities_query()
    limit = max(1, int(args.limit))
    cursor = collection.find(query).sort("createdAt", -1).limit(limit)
    return await cursor.to_list(length=limit)


def _remediate_one(
    opp: Dict[str, Any],
    *,
    scraper,
    resolver,
    audience_catalog: List[str],
) -> Dict[str, Any]:
    """Mutate a working copy of the opportunity and return it."""
    from app.helpers.OpportunityQualifier import qualify_opportunities_batch
    from app.helpers.OpportunitySubmissionResolver import (
        submission_info_has_submission_path,
        sync_submission_info_to_metadata,
    )
    from app.helpers.TargetAudienceCatalog import resolve_target_audiences

    working = deepcopy(opp)
    # Drop Mongo-only fields that should not be re-written as nested junk
    working.pop("_id", None)

    link = (working.get("link") or working.get("url") or "").strip()
    content, urls = _scrape_page(scraper, link)

    if link:
        resolver.resolve_submission_info(
            working,
            source_url=link,
            source_page_content=content,
            source_page_links=urls,
        )

    info = working.get("submissionInfo") if isinstance(working.get("submissionInfo"), dict) else {}
    if not submission_info_has_submission_path(info):
        source = working.get("source") if isinstance(working.get("source"), dict) else {}
        source_url = (source.get("source_url") or "").strip()
        if source_url and link and not _urls_same_page(source_url, link):
            src_content, src_urls = _scrape_page(scraper, source_url)
            if src_content or src_urls:
                resolver.resolve_submission_info(
                    working,
                    source_url=source_url,
                    source_page_content=src_content or content,
                    source_page_links=(src_urls or []) + (urls or []),
                )
                if src_content:
                    content = src_content

    if _audiences_empty(working):
        event_name = (working.get("event_name") or working.get("title") or "").strip()
        working["target_audiences"] = resolve_target_audiences(
            [],
            allowed=audience_catalog,
            page_snippet=content[:4000],
            event_name=event_name,
            force_ai_if_empty=True,
        )

    sync_submission_info_to_metadata(working)
    qualify_opportunities_batch(
        [working],
        scraper=scraper,
        source_page_url=link,
        source_page_content=content,
    )
    return working


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remediate existing Opportunities (submissionInfo, Meetup, target_audiences)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended changes without writing to Mongo or Pinecone.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Max opportunities to process when not using --ids or --all (default: 25).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process every Opportunity in the collection (ignores --limit and the broken-only filter).",
    )
    parser.add_argument(
        "--ids",
        type=str,
        default="",
        help="Comma-separated Mongo ObjectIds to process (overrides the broken-opps query).",
    )
    parser.add_argument(
        "--skip-pinecone",
        action="store_true",
        help="Update Mongo only; skip Pinecone upsert/delete.",
    )
    args = parser.parse_args()

    connection_string = os.getenv("MONGODB_CONNECTION_STRING")
    db_name = os.getenv("DB_NAME")
    if not connection_string or not db_name:
        logger.error("Missing MONGODB_CONNECTION_STRING or DB_NAME in environment")
        sys.exit(1)

    from bson import ObjectId

    from app.helpers.Database import MongoDB
    from app.helpers.OpportunitySubmissionResolver import OpportunitySubmissionResolver
    from app.helpers.PineconeOpportunityStore import PineconeOpportunityStore
    from app.helpers.RapidAPIScraper import RapidAPIScraper
    from app.helpers.TargetAudienceCatalog import load_target_audience_names_from_db
    from app.models.Opportunity import OpportunityModel

    MongoDB.connect(connection_string)
    summary = {
        "scanned": 0,
        "eligible": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "pinecone_upserts": 0,
        "pinecone_deletes": 0,
        "dry_run": bool(args.dry_run),
    }

    try:
        audience_catalog = await load_target_audience_names_from_db()
        logger.info("Audience catalog size=%d", len(audience_catalog))

        model = OpportunityModel()
        docs = await _fetch_candidates(model.collection, args)
        summary["scanned"] = len(docs)
        logger.info(
            "Fetched %d candidate document(s) (all=%s limit=%s ids=%s)",
            len(docs),
            bool(args.all),
            args.limit,
            bool(args.ids),
        )

        # With --all, still remediate every doc (force eligibility).
        force_all = bool(args.all) or bool(args.ids)

        scraper = RapidAPIScraper()
        resolver = OpportunitySubmissionResolver(rapidapi_scraper=scraper)
        pinecone = None if args.skip_pinecone else PineconeOpportunityStore()

        for doc in docs:
            oid = doc.get("_id")
            oid_str = str(oid)
            if not force_all and not _needs_remediation(doc):
                summary["skipped"] += 1
                logger.info("Skip (no remediation needed) id=%s", oid_str)
                continue

            summary["eligible"] += 1
            before_qualified = bool(doc.get("isQualified"))
            before_embed = _snapshot_embed_fields(doc)
            before_status = _submission_status(doc)
            before_audiences = list(doc.get("target_audiences") or [])

            try:
                # Blocking scrape/LLM work in thread to keep event loop free for Motor
                fixed = await asyncio.to_thread(
                    _remediate_one,
                    doc,
                    scraper=scraper,
                    resolver=resolver,
                    audience_catalog=audience_catalog,
                )
            except Exception as e:
                summary["errors"] += 1
                logger.exception("Remediation failed id=%s err=%s", oid_str, e)
                continue

            after_qualified = bool(fixed.get("isQualified"))
            after_embed = _snapshot_embed_fields(fixed)
            after_status = _submission_status(fixed)
            after_audiences = list(fixed.get("target_audiences") or [])

            logger.info(
                "id=%s event=%s submission %s -> %s audiences %s -> %s qualified %s -> %s reason=%s",
                oid_str,
                (fixed.get("event_name") or "")[:60],
                before_status or "(none)",
                after_status or "(none)",
                before_audiences,
                after_audiences,
                before_qualified,
                after_qualified,
                (fixed.get("reasonForUnqualify") or "")[:120],
            )

            set_fields = {
                "submissionInfo": fixed.get("submissionInfo") or {},
                "metadata": fixed.get("metadata") if isinstance(fixed.get("metadata"), dict) else {},
                "target_audiences": after_audiences,
                "isQualified": after_qualified,
                "reasonForUnqualify": fixed.get("reasonForUnqualify"),
                "updatedAt": datetime.utcnow(),
            }

            if args.dry_run:
                logger.info("[dry-run] would $set id=%s fields=%s", oid_str, list(set_fields.keys()))
                continue

            try:
                await model.collection.update_one(
                    {"_id": oid if isinstance(oid, ObjectId) else ObjectId(oid_str)},
                    {"$set": set_fields},
                )
                summary["updated"] += 1
            except Exception as e:
                summary["errors"] += 1
                logger.exception("Mongo update failed id=%s err=%s", oid_str, e)
                continue

            if args.skip_pinecone or pinecone is None:
                continue

            if after_qualified:
                if (not before_qualified) or (before_embed != after_embed):
                    ok = pinecone.upsert_opportunity(oid_str, {**doc, **set_fields, "link": fixed.get("link") or doc.get("link")})
                    if ok:
                        summary["pinecone_upserts"] += 1
            elif before_qualified and not after_qualified:
                if _delete_pinecone_opportunity(pinecone, oid_str):
                    summary["pinecone_deletes"] += 1

        logger.info("Remediation finished: %s", summary)
        print(summary)
    finally:
        if MongoDB.client:
            MongoDB.client.close()


if __name__ == "__main__":
    asyncio.run(main())
