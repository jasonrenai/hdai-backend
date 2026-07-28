"""
Verify existing Opportunities one-by-one in batches:
scrape each opportunity link, run the LLM speaking-opportunity gate
(EventDetailEnricherAgent.verify_and_refresh_from_page_content), then set:
  isVerified = True  → page is a valid speaking opportunity
  isVerified = False → academic CFP, sponsorship, attend-only, scrape fail, etc.

Does NOT overwrite opportunity fields — only isVerified (+ reasonForUnverify, verifiedAt).

By default only processes opportunities that:
  - have never been verified (isVerified missing), AND
  - are qualified (isQualified=true)
Skips isVerified true/false (already verified) and isQualified=false (expired / unused).
Use --include-verified to re-check already-verified docs.

Run from project root (venv active):
  python scripts/verify_opportunities.py --dry-run --limit 5
  python scripts/verify_opportunities.py --batch-size 20 --limit 100
  python scripts/verify_opportunities.py --all
  python scripts/verify_opportunities.py --ids 65abc...,66def...
  python scripts/verify_opportunities.py --include-verified --all

Requires .env: MONGODB_CONNECTION_STRING, DB_NAME, RAPIDAPI_KEY, OPENAI_API_KEY
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("verify_opportunities")
# Surface scrape + LLM agent progress in the same run
for _name in (
    "app.helpers.RapidAPIScraper",
    "app.agents.EventDetailEnricherAgent",
):
    logging.getLogger(_name).setLevel(logging.INFO)

RAPIDAPI_DELAY_SECONDS = 5


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM-verify Opportunities and set isVerified")
    p.add_argument("--dry-run", action="store_true", help="Do not write to Mongo")
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max opportunities to process (ignored with --all unless also set for safety)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Process all matching opportunities (no limit)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Fetch this many docs per Mongo batch, then process one-by-one",
    )
    p.add_argument(
        "--ids",
        type=str,
        default="",
        help="Comma-separated opportunity ObjectIds to verify",
    )
    p.add_argument(
        "--include-verified",
        action="store_true",
        help="Also re-check docs that already have isVerified true or false (default: only missing)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=RAPIDAPI_DELAY_SECONDS,
        help="Seconds between RapidAPI scrapes (default 5)",
    )
    return p.parse_args()


def _base_query(args: argparse.Namespace) -> dict:
    """
    Always require isQualified=true (expired / unused opps are skipped).

    Default: only never-verified (isVerified missing/null).
    --include-verified: also re-check already-verified true/false docs.

    Semantics:
      isVerified=true  → already verified, is a speaking opportunity
      isVerified=false → already verified, not a speaking opportunity
      isQualified=false → expired / unused — never process
    """
    clauses: list[dict] = [{"isQualified": True}]
    if not args.include_verified:
        clauses.append(
            {
                "$or": [
                    {"isVerified": {"$exists": False}},
                    {"isVerified": None},
                ]
            }
        )
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _log_summary(summary: dict, *, prefix: str = "Running totals") -> None:
    logger.info(
        "%s | scanned=%d updated=%d speaking(true)=%d not_speaking(false)=%d errors=%d dry_run_skips=%d",
        prefix,
        summary["scanned"],
        summary["updated"],
        summary["verified_true"],
        summary["verified_false"],
        summary["errors"],
        summary["dry_run_skipped_writes"],
    )


def _verify_one(opp: Dict[str, Any], *, scraper, enricher, oid_str: str) -> tuple[bool, str]:
    """
    Scrape opportunity link and run LLM verify gate.
    Returns (is_verified, reason). Does not mutate Mongo fields beyond the boolean decision.
    """
    from copy import deepcopy

    link = (opp.get("link") or opp.get("url") or "").strip()
    event_name = (opp.get("event_name") or opp.get("title") or "").strip()
    if not link:
        logger.warning("[%s] No link/url — marking isVerified=false", oid_str)
        return False, "Opportunity has no link/url to scrape."

    logger.info("[%s] Step 1/3 SCRAPE start event=%s url=%s", oid_str, event_name[:80], link[:120])
    t0 = time.monotonic()
    try:
        result = scraper.scrape(link)
    except Exception as e:
        logger.warning("[%s] Scrape raised after %.1fs: %s", oid_str, time.monotonic() - t0, e)
        return False, f"Scrape failed: {e}"

    scrape_s = time.monotonic() - t0
    if not result.get("success"):
        err = result.get("error") or "unknown"
        logger.warning("[%s] Scrape unsuccessful after %.1fs: %s", oid_str, scrape_s, err)
        return False, f"Scrape unsuccessful: {err}"

    data = result.get("data") or {}
    content = str(data.get("content") or "").strip()
    page_name = str(data.get("name") or "").strip()
    if not content:
        logger.warning("[%s] Scrape returned empty content after %.1fs", oid_str, scrape_s)
        return False, "Scraped page content is empty."

    logger.info(
        "[%s] Step 1/3 SCRAPE ok in %.1fs content_chars=%d page_name=%s",
        oid_str,
        scrape_s,
        len(content),
        (page_name or "(none)")[:80],
    )

    working = deepcopy(opp)
    working.pop("_id", None)

    logger.info("[%s] Step 2/3 LLM verify start (speaking vs CFP/sponsorship/etc.)", oid_str)
    t1 = time.monotonic()
    ok, reason, _updated = enricher.verify_and_refresh_from_page_content(
        working,
        content,
        name=page_name,
        description=str(data.get("description") or "").strip(),
    )
    llm_s = time.monotonic() - t1
    if ok:
        logger.info("[%s] Step 2/3 LLM verify PASS in %.1fs → speaking opportunity", oid_str, llm_s)
        return True, ""

    reason_s = (reason or "").strip() or "LLM determined this is not a speaking opportunity."
    logger.info(
        "[%s] Step 2/3 LLM verify FAIL in %.1fs → not speaking | reason=%s",
        oid_str,
        llm_s,
        reason_s[:200],
    )
    return False, reason_s


async def _process(
    collection,
    docs: List[Dict[str, Any]],
    *,
    scraper,
    enricher,
    dry_run: bool,
    summary: dict,
    batch_label: str = "",
) -> None:
    from bson import ObjectId

    total_in_batch = len(docs)
    for i, doc in enumerate(docs, start=1):
        oid = doc.get("_id")
        oid_str = str(oid)
        event_name = (doc.get("event_name") or doc.get("title") or "").strip()
        link = (doc.get("link") or doc.get("url") or "").strip()
        summary["scanned"] += 1
        label = f"{batch_label} item {i}/{total_in_batch}" if batch_label else f"item {i}/{total_in_batch}"

        logger.info(
            "──── %s | id=%s | event=%s | isQualified=%s | isVerified(before)=%s ────",
            label,
            oid_str,
            event_name[:80] or "(no name)",
            doc.get("isQualified"),
            doc.get("isVerified", "<missing>"),
        )
        logger.info("[%s] link=%s", oid_str, link[:160] or "(no link)")

        try:
            is_verified, reason = await asyncio.to_thread(
                _verify_one,
                doc,
                scraper=scraper,
                enricher=enricher,
                oid_str=oid_str,
            )
        except Exception as e:
            summary["errors"] += 1
            logger.exception("[%s] Verify crashed: %s", oid_str, e)
            _log_summary(summary)
            continue

        summary["verified_true" if is_verified else "verified_false"] += 1

        update = {
            "isVerified": is_verified,
            "verifiedAt": datetime.utcnow(),
            "reasonForUnverify": None if is_verified else (reason or "Not a speaking opportunity"),
        }

        if dry_run:
            summary["dry_run_skipped_writes"] += 1
            logger.info(
                "[%s] Step 3/3 DRY-RUN skip write | would set isVerified=%s reason=%s",
                oid_str,
                is_verified,
                (reason or "")[:160],
            )
            _log_summary(summary)
            continue

        try:
            logger.info("[%s] Step 3/3 Mongo $set isVerified=%s", oid_str, is_verified)
            result = await collection.update_one({"_id": ObjectId(oid_str)}, {"$set": update})
            summary["updated"] += 1
            logger.info(
                "[%s] DONE isVerified=%s matched=%d modified=%d reason=%s",
                oid_str,
                is_verified,
                result.matched_count,
                result.modified_count,
                (reason or "(speaking opportunity)")[:160],
            )
        except Exception as e:
            summary["errors"] += 1
            logger.exception("[%s] Mongo update failed: %s", oid_str, e)

        _log_summary(summary)


async def main() -> None:
    args = _parse_args()
    connection_string = os.getenv("MONGODB_CONNECTION_STRING")
    db_name = os.getenv("DB_NAME")
    if not connection_string or not db_name:
        logger.error("Missing MONGODB_CONNECTION_STRING or DB_NAME in environment")
        sys.exit(1)

    from bson import ObjectId

    from app.agents.EventDetailEnricherAgent import EventDetailEnricherAgent
    from app.helpers.Database import MongoDB
    from app.helpers.RapidAPIScraper import RapidAPIScraper
    from app.models.Opportunity import OpportunityModel

    logger.info("========== verify_opportunities START ==========")
    logger.info(
        "Config: db=%s dry_run=%s all=%s limit=%s batch_size=%s delay=%.1fs include_verified=%s ids=%s",
        db_name,
        bool(args.dry_run),
        bool(args.all),
        args.limit,
        args.batch_size,
        float(args.delay),
        bool(args.include_verified),
        bool(args.ids),
    )
    logger.info(
        "Filters: isQualified=true; isVerified %s",
        "any (re-check)" if args.include_verified else "missing only (skip already verified true/false)",
    )

    MongoDB.connect(connection_string)
    logger.info("MongoDB connected")
    model = OpportunityModel()
    collection = model.collection

    scraper = RapidAPIScraper(delay_seconds=max(0.0, float(args.delay)))
    enricher = EventDetailEnricherAgent(rapidapi_scraper=scraper)
    logger.info("RapidAPI scraper + EventDetailEnricherAgent ready")

    summary = {
        "scanned": 0,
        "updated": 0,
        "verified_true": 0,
        "verified_false": 0,
        "errors": 0,
        "dry_run_skipped_writes": 0,
        "dry_run": bool(args.dry_run),
    }

    batch_size = max(1, int(args.batch_size))
    max_to_process: Optional[int] = None if args.all else max(1, int(args.limit))
    job_t0 = time.monotonic()

    try:
        if args.ids:
            raw_ids = [s.strip() for s in args.ids.split(",") if s.strip()]
            oids = []
            for rid in raw_ids:
                try:
                    oids.append(ObjectId(rid))
                except Exception:
                    logger.warning("Skipping invalid ObjectId: %s", rid)
            if not oids:
                logger.error("No valid ids provided")
                sys.exit(1)
            logger.info("Fetching %d opportunity id(s) from Mongo…", len(oids))
            cursor = collection.find({"_id": {"$in": oids}})
            docs = await cursor.to_list(length=len(oids))
            before = len(docs)
            docs = [d for d in docs if d.get("isQualified") is True]
            if not args.include_verified:
                docs = [d for d in docs if d.get("isVerified") is None]
            skipped = before - len(docs)
            if skipped:
                logger.info(
                    "Skipped %d id(s) (need isQualified=true%s)",
                    skipped,
                    "" if args.include_verified else " and isVerified missing",
                )
            logger.info("Will process %d opportunity(ies) by id", len(docs))
            if not docs:
                logger.warning("Nothing to process after filters")
            else:
                await _process(
                    collection,
                    docs,
                    scraper=scraper,
                    enricher=enricher,
                    dry_run=bool(args.dry_run),
                    summary=summary,
                    batch_label="ids",
                )
        else:
            query = _base_query(args)
            match_count = await collection.count_documents(query)
            logger.info(
                "Mongo match count for filters=%d | will process %s",
                match_count,
                "all matches" if max_to_process is None else f"up to {max_to_process}",
            )
            if match_count == 0:
                logger.warning("No opportunities match filters — nothing to do")

            last_id = None
            processed = 0
            batch_num = 0
            while True:
                if max_to_process is not None and processed >= max_to_process:
                    logger.info("Reached --limit=%d — stopping", max_to_process)
                    break
                batch_query = dict(query)
                if last_id is not None:
                    id_clause = {"_id": {"$gt": last_id}}
                    if batch_query:
                        batch_query = {"$and": [batch_query, id_clause]}
                    else:
                        batch_query = id_clause

                take = batch_size
                if max_to_process is not None:
                    take = min(batch_size, max_to_process - processed)

                logger.info("Fetching batch size=%d from Mongo…", take)
                docs = await (
                    collection.find(batch_query)
                    .sort([("_id", 1)])
                    .limit(take)
                    .to_list(length=take)
                )
                if not docs:
                    logger.info("No more documents — finished all batches")
                    break

                batch_num += 1
                logger.info(
                    "======== BATCH %d | %d doc(s) | first_id=%s | last_id=%s ========",
                    batch_num,
                    len(docs),
                    str(docs[0].get("_id")),
                    str(docs[-1].get("_id")),
                )
                await _process(
                    collection,
                    docs,
                    scraper=scraper,
                    enricher=enricher,
                    dry_run=bool(args.dry_run),
                    summary=summary,
                    batch_label=f"batch {batch_num}",
                )
                processed += len(docs)
                last_id = docs[-1].get("_id")
                logger.info(
                    "Batch %d complete | processed_so_far=%d | elapsed=%.0fs",
                    batch_num,
                    processed,
                    time.monotonic() - job_t0,
                )
                if len(docs) < take:
                    logger.info("Last partial batch — done")
                    break
    finally:
        if MongoDB.client:
            MongoDB.client.close()
            logger.info("MongoDB connection closed")

    elapsed = time.monotonic() - job_t0
    logger.info("========== verify_opportunities FINISHED in %.0fs ==========", elapsed)
    _log_summary(summary, prefix="FINAL")
    print("\n=== Verify summary ===")
    for k, v in summary.items():
        print(f"{k}={v}")
    print(f"elapsed_seconds={elapsed:.0f}")


if __name__ == "__main__":
    asyncio.run(main())
