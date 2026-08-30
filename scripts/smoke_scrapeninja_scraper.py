"""
Smoke-test the default ScrapeNinja RapidAPIScraper on known hard URLs.

Usage (from repo root):
  .venv/bin/python scripts/smoke_scrapeninja_scraper.py
  .venv/bin/python scripts/smoke_scrapeninja_scraper.py --limit 2
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

os.environ["SCRAPE_PROVIDER"] = "scrapeninja"

# Per-URL expectations: worldsummit's real page is a short thank-you (not a JS shell).
CASES = [
    {
        "url": "https://2026.allthingsai.org/calling-the-worlds-best-ai-speakers",
        "min_len": 500,
        "must_not_contain": ["couldn't load", "please wait while your request is being verified"],
        "must_contain_any": ["speaker", "call for", "cfp", "papers"],
    },
    {
        "url": "https://worldsummit.ai/form-speakers-enquiries/",
        "min_len": 80,
        "must_not_contain": ["please wait while your request is being verified", "checking your browser"],
        "must_contain_any": ["speak", "enquir", "interest"],
    },
    {
        "url": "https://www.jupiter-miami.com/apply-to-speak",
        "min_len": 500,
        "must_not_contain": ["please wait while your request is being verified"],
        "must_contain_any": ["speak", "apply"],
    },
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(CASES))
    args = parser.parse_args()

    from app.helpers.RapidAPIScraper import RapidAPIScraper

    scraper = RapidAPIScraper(delay_seconds=0)
    cases = CASES[: max(1, args.limit)]
    failed = 0

    print(f"SCRAPE_PROVIDER=scrapeninja cases={len(cases)}\n")
    for case in cases:
        url = case["url"]
        result = scraper.scrape(url)
        ok = bool(result.get("success"))
        data = result.get("data") or {}
        content = data.get("content") or ""
        low = content.lower()
        content_len = len(content)
        path = data.get("scrapePath")

        reasons: list[str] = []
        if not ok:
            reasons.append(f"scrape failed: {result.get('error')}")
        if content_len < case["min_len"]:
            reasons.append(f"content_len {content_len} < min_len {case['min_len']}")
        for bad in case.get("must_not_contain") or []:
            if bad.lower() in low:
                reasons.append(f"forbidden phrase: {bad!r}")
        must_any = case.get("must_contain_any") or []
        if must_any and not any(m.lower() in low for m in must_any):
            reasons.append(f"missing any of {must_any}")

        passed = not reasons
        status = "PASS" if passed else "FAIL"
        if not passed:
            failed += 1
        print(f"[{status}] len={content_len} path={path}")
        print(f"  {url}")
        print(f"  name={data.get('name')!r}")
        if reasons:
            for r in reasons:
                print(f"  reason: {r}")
        print(f"  preview={(content[:160] or '').replace(chr(10), ' ')!r}")
        print()

    if failed:
        print(f"SMOKE FAILED: {failed}/{len(cases)}")
        return 1
    print(f"SMOKE OK: {len(cases)}/{len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
