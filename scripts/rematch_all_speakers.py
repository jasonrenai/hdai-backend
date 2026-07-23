"""
Delete all matchedOpportunities docs, then rematch every speaker profile.

Run from project root:
  .venv/bin/python scripts/rematch_all_speakers.py

Uses send_matched_email=False so bulk rematch does not notify speakers.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.helpers.Database import MongoDB
from app.models.MatchedOpportunities import MatchedOpportunitiesModel
from app.models.SpeakerProfile import SpeakerProfileModel
from app.services.Opportunity import OpportunityService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("rematch_all_speakers")


async def main() -> None:
    connection_string = os.getenv("MONGODB_CONNECTION_STRING")
    db_name = os.getenv("DB_NAME")
    if not connection_string or not db_name:
        raise SystemExit("MONGODB_CONNECTION_STRING and DB_NAME are required")

    MongoDB.connect(connection_string)
    matched = MatchedOpportunitiesModel()
    profiles_model = SpeakerProfileModel()
    service = OpportunityService(matched_opportunities_model=matched)

    before_matched = await matched.collection.count_documents({})
    delete_result = await matched.collection.delete_many({})
    logger.info(
        "Cleared matchedOpportunities: before=%d deleted=%d",
        before_matched,
        delete_result.deleted_count,
    )

    profiles = await profiles_model.get_all_profiles()
    logger.info("Rematching %d speaker profile(s)", len(profiles))

    summary = []
    for i, profile in enumerate(profiles, start=1):
        speaker_id = str(profile.get("_id") or "")
        name = (profile.get("full_name") or "").strip() or "(no name)"
        if not speaker_id:
            continue
        logger.info("[%d/%d] Matching speaker_id=%s name=%s", i, len(profiles), speaker_id, name)
        try:
            entry_id = await service.start_matching_run(speaker_id)
            await service.run_matching_and_save(
                speaker_id,
                matched_entry_id=entry_id,
                send_matched_email=False,
            )
            doc = await matched.get_by_speaker_id(speaker_id)
            n = len((doc or {}).get("opportunities") or [])
            status = (doc or {}).get("status")
            logger.info(
                "[%d/%d] Done speaker_id=%s status=%s matches=%d",
                i,
                len(profiles),
                speaker_id,
                status,
                n,
            )
            summary.append({"speaker_id": speaker_id, "name": name, "status": status, "matches": n, "error": None})
        except Exception as e:
            logger.exception("Match failed for speaker_id=%s", speaker_id)
            summary.append({"speaker_id": speaker_id, "name": name, "status": "failed", "matches": 0, "error": str(e)})

    print("\n=== Rematch summary ===")
    for row in summary:
        err = f" error={row['error']}" if row["error"] else ""
        print(
            f"{row['speaker_id']} | {row['name']} | status={row['status']} | matches={row['matches']}{err}"
        )
    print(f"Total profiles: {len(summary)}")
    print(f"matchedOpportunities docs now: {await matched.collection.count_documents({})}")

    if MongoDB.client:
        MongoDB.client.close()


if __name__ == "__main__":
    asyncio.run(main())
