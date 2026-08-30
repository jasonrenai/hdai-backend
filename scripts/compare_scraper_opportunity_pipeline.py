"""
Side-by-side opportunity-identification regression: legacy AI Content Scraper vs ScrapeNinja.

Runs OpportunityDiscoveryPipeline + filter_complete_opportunities on a fixed URL fixture
(twice per URL: SCRAPE_PROVIDER=legacy then scrapeninja). Does not write Opportunities.

Usage (from repo root):
  .venv/bin/python scripts/compare_scraper_opportunity_pipeline.py
  .venv/bin/python scripts/compare_scraper_opportunity_pipeline.py --limit 4

Writes docs/scraper-opportunity-pipeline-comparison.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("compare_scraper_opportunity_pipeline")

OUT_PATH = ROOT / "docs" / "scraper-opportunity-pipeline-comparison.json"

# role: improve = known thin/JS fail; control = already becomes opp; negative = should stay 0
FIXTURE: list[dict[str, str]] = [
    {
        "url": "https://2026.allthingsai.org/calling-the-worlds-best-ai-speakers",
        "role": "improve",
    },
    {
        "url": "https://worldsummit.ai/form-speakers-enquiries/",
        "role": "improve",
    },
    {
        "url": "https://www.enterpriseaiworld.com/Conference/2026/CallForSpeakers.aspx",
        "role": "control",
    },
    {
        "url": "https://reg.theaisummit.com/new-york-submit-speaker",
        "role": "control",
    },
    {
        "url": "https://www.marketingaiinstitute.com/events",
        "role": "negative",
    },
]


def _run_pipeline(url: str, provider: str) -> dict[str, Any]:
    os.environ["SCRAPE_PROVIDER"] = provider
    # Re-import not required — RapidAPIScraper reads env each scrape()
    from app.helpers.OpportunityDiscoveryPipeline import OpportunityDiscoveryPipeline
    from app.helpers.RapidAPIScraper import RapidAPIScraper
    from app.services.UrlScraperRapidAPI import filter_complete_opportunities

    scrape = RapidAPIScraper(delay_seconds=0).scrape(url)
    scrape_ok = bool(scrape.get("success"))
    data = scrape.get("data") or {}
    content = data.get("content") or ""
    row: dict[str, Any] = {
        "provider": provider,
        "scrape_ok": scrape_ok,
        "scrape_error": scrape.get("error"),
        "content_len": len(content),
        "scrape_path": data.get("scrapePath"),
        "name": data.get("name"),
        "opportunities_after_pipeline": 0,
        "complete_count": 0,
        "samples": [],
    }
    if not scrape_ok or not content.strip():
        return row

    pipe = OpportunityDiscoveryPipeline(
        delay_seconds=0,
        rapidapi_scraper=RapidAPIScraper(delay_seconds=0),
    )
    result = pipe.run(url, delay_seconds=1)
    opps = (result or {}).get("opportunities") or []
    complete = filter_complete_opportunities(opps)
    row["opportunities_after_pipeline"] = len(opps)
    row["complete_count"] = len(complete)
    row["samples"] = [
        {
            "event_name": (o.get("event_name") or o.get("title") or "")[:80],
            "link": (o.get("link") or o.get("url") or "")[:120],
            "isVerified": o.get("isVerified"),
        }
        for o in complete[:3]
    ]
    return row


def _diff(legacy: dict[str, Any], ninja: dict[str, Any]) -> str:
    lc = int(legacy.get("complete_count") or 0)
    nc = int(ninja.get("complete_count") or 0)
    if lc == 0 and nc > 0:
        return "gained"
    if lc > 0 and nc == 0:
        return "lost"
    if lc == nc:
        return "same"
    if nc > lc:
        return "gained"
    return "lost"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(FIXTURE))
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    fixtures = FIXTURE[: max(1, args.limit)]
    rows: list[dict[str, Any]] = []
    regressions = 0
    improvements = 0

    for i, item in enumerate(fixtures, 1):
        url = item["url"]
        role = item["role"]
        print("=" * 88)
        print(f"[{i}/{len(fixtures)}] role={role} {url}")
        legacy = _run_pipeline(url, "legacy")
        print(
            f"  legacy: scrape_ok={legacy['scrape_ok']} len={legacy['content_len']} "
            f"pipeline={legacy['opportunities_after_pipeline']} complete={legacy['complete_count']}"
        )
        ninja = _run_pipeline(url, "scrapeninja")
        print(
            f"  ninja:  scrape_ok={ninja['scrape_ok']} len={ninja['content_len']} path={ninja.get('scrape_path')} "
            f"pipeline={ninja['opportunities_after_pipeline']} complete={ninja['complete_count']}"
        )
        diff = _diff(legacy, ninja)
        print(f"  → diff={diff}")

        # Pass criteria
        fail_reason = None
        if role == "control" and legacy["complete_count"] >= 1 and ninja["complete_count"] < 1:
            fail_reason = "control regression: legacy had complete opp, scrapeninja has 0"
            regressions += 1
        if role == "improve" and ninja["scrape_ok"] and ninja["content_len"] >= 500:
            # content improved; complete_count may still be 0 due to verify rules
            if legacy["content_len"] < 500 and ninja["content_len"] >= 500:
                improvements += 1
        if role == "improve" and (not ninja["scrape_ok"] or ninja["content_len"] < 500):
            fail_reason = "improve URL still thin/failed under scrapeninja"
            regressions += 1

        rows.append(
            {
                "url": url,
                "role": role,
                "legacy": legacy,
                "scrapeninja": ninja,
                "diff": diff,
                "fail_reason": fail_reason,
            }
        )

    # Restore default provider for any follow-on imports in same process
    os.environ["SCRAPE_PROVIDER"] = "scrapeninja"

    summary = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tested": len(rows),
        "regressions": regressions,
        "content_improvements_on_improve_role": improvements,
        "diff_counts": {
            "gained": sum(1 for r in rows if r["diff"] == "gained"),
            "lost": sum(1 for r in rows if r["diff"] == "lost"),
            "same": sum(1 for r in rows if r["diff"] == "same"),
        },
    }
    out = {"summary": summary, "results": rows}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 88)
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_path}")

    if regressions:
        print(f"REGRESSION FAILED: {regressions}")
        return 1
    print("REGRESSION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
