"""
Process pending GoogleQueries (e.g. documents inserted via JSON with status \"pending\").

Uses the same pipeline as the API background task after POST /google-query-scraper/search:
SERP -> top URLs -> RapidAPI + LLM -> Opportunities in Mongo (with link+event_name dedup) and vector store.

Each invocation claims up to N jobs atomically (status pending -> running) so overlapping runs do not
process the same document twice. Jobs in one batch run one after another.

Run from project root:
  python scripts/process_pending_google_queries.py
  python scripts/process_pending_google_queries.py --limit 25

Requires .env: MONGODB_CONNECTION_STRING, DB_NAME, RAPIDAPI_KEY (Real-Time Web Search), plus RapidAPI scraper, OpenAI, Pinecone, etc.
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("process_pending_google_queries")


async def main():
    parser = argparse.ArgumentParser(description="Process pending GoogleQueries batch.")
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum number of pending GoogleQueries to claim and process this run (default: 25).",
    )
    args = parser.parse_args()

    connection_string = os.getenv("MONGODB_CONNECTION_STRING")
    db_name = os.getenv("DB_NAME")
    if not connection_string or not db_name:
        logger.error("Missing MONGODB_CONNECTION_STRING or DB_NAME in environment")
        sys.exit(1)

    from app.helpers.Database import MongoDB
    from app.services.GoogleQueryScraper import GoogleQueryScraperService

    MongoDB.connect(connection_string)
    try:
        service = GoogleQueryScraperService()
        summary = await service.process_pending_batch(limit=args.limit)
        logger.info("Batch finished: %s", summary)
        print(summary)
    finally:
        if MongoDB.client:
            MongoDB.client.close()


if __name__ == "__main__":
    asyncio.run(main())
