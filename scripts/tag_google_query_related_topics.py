"""
Backfill relatedTopics on GoogleQueries (catalog tags from speaker TOPICS).

Heuristic keyword/synonym match first; OpenAI only when heuristic finds nothing.

Run from project root:
  python scripts/tag_google_query_related_topics.py --dry-run
  python scripts/tag_google_query_related_topics.py --limit 50
  python scripts/tag_google_query_related_topics.py --force

Requires .env: MONGODB_CONNECTION_STRING, DB_NAME; OPENAI_API_KEY when AI fallback is needed.
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("tag_google_query_related_topics")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tag GoogleQueries with relatedTopics from the speaker topic catalog."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve topics and log results without writing to Mongo.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max documents to process (0 = no limit).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retag all queries, including those that already have relatedTopics.",
    )
    args = parser.parse_args()

    connection_string = os.getenv("MONGODB_CONNECTION_STRING")
    db_name = os.getenv("DB_NAME")
    if not connection_string or not db_name:
        logger.error("Missing MONGODB_CONNECTION_STRING or DB_NAME in environment")
        sys.exit(1)

    from app.helpers.Database import MongoDB
    from app.helpers.GoogleQueryTopicTagger import resolve_related_topics_with_source
    from app.models.GoogleQuery import GoogleQueryModel

    MongoDB.connect(connection_string)
    try:
        model = GoogleQueryModel()
        if args.force:
            query_filter: dict = {}
        else:
            query_filter = {
                "$or": [
                    {"relatedTopics": {"$exists": False}},
                    {"relatedTopics": None},
                    {"relatedTopics": []},
                ]
            }

        cursor = model.collection.find(query_filter).sort("createdAt", 1)
        limit = max(0, int(args.limit or 0))

        summary = {
            "scanned": 0,
            "tagged": 0,
            "skipped_empty_query": 0,
            "unchanged": 0,
            "heuristic": 0,
            "ai": 0,
            "none": 0,
            "dry_run": bool(args.dry_run),
        }

        async for doc in cursor:
            if limit and summary["scanned"] >= limit:
                break
            summary["scanned"] += 1
            google_query_id = str(doc["_id"])
            query_text = (doc.get("query") or "").strip()
            if not query_text:
                summary["skipped_empty_query"] += 1
                logger.warning("Skip empty query id=%s", google_query_id)
                continue

            topics, source = resolve_related_topics_with_source(query_text)
            summary[source] = summary.get(source, 0) + 1

            existing = doc.get("relatedTopics") if isinstance(doc.get("relatedTopics"), list) else []
            if existing == topics and not args.force:
                summary["unchanged"] += 1
                logger.info(
                    "Unchanged id=%s source=%s relatedTopics=%s",
                    google_query_id,
                    source,
                    topics,
                )
                continue

            logger.info(
                "%s id=%s source=%s relatedTopics=%s query=%s",
                "Would tag" if args.dry_run else "Tagged",
                google_query_id,
                source,
                topics,
                query_text[:120],
            )
            if not args.dry_run:
                await model.collection.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "relatedTopics": topics,
                            "updatedAt": datetime.utcnow(),
                        }
                    },
                )
            summary["tagged"] += 1

        logger.info("Finished: %s", summary)
        print(summary)
    finally:
        if MongoDB.client:
            MongoDB.client.close()


if __name__ == "__main__":
    asyncio.run(main())
