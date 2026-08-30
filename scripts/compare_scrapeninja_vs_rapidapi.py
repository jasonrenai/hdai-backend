"""
Compare current RapidAPI AI Content Scraper vs ScrapeNinja (static + JS).

Targets URLs that previously failed or returned JS shells (thin/empty content).

Usage (from repo root):
  .venv/bin/python scripts/compare_scrapeninja_vs_rapidapi.py
  .venv/bin/python scripts/compare_scrapeninja_vs_rapidapi.py --limit 5
  .venv/bin/python scripts/compare_scrapeninja_vs_rapidapi.py --include-failed-mongo --limit 10

Requires RAPIDAPI_KEY in .env (same RapidAPI key used for ScrapeNinja host).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("compare_scrapers")

# Known thin-scrape / JS-shell / customer-miss URLs from opportunity discovery audit
KNOWN_JS_OR_THIN_URLS = [
    "https://2026.allthingsai.org/calling-the-worlds-best-ai-speakers",
    "https://worldsummit.ai/form-speakers-enquiries/",
    "https://www.jupiter-miami.com/apply-to-speak",
    "https://www.marketingaiinstitute.com/blog/maicon-call-for-speakers",
    "https://www.marketingaiinstitute.com/speaking-inquiries",
    "https://www.corporatecompliance.org/ai-compliance-conference-call-speakers",
    "https://www.aidataanalytics.network/events-generativeaisummit",
    "https://events.reutersevents.com/marketing/csx",
    "https://ana.foleon.com/",
]

SCRAPENINJA_HOST = "scrapeninja.p.rapidapi.com"
THIN_CONTENT_CHARS = 500  # below this → likely shell / fail for CFS extraction


def _api_key() -> str:
    key = (os.getenv("RAPIDAPI_KEY") or "").strip()
    if not key:
        raise SystemExit("RAPIDAPI_KEY missing in environment / .env")
    return key


def _headers() -> dict[str, str]:
    return {
        "x-rapidapi-key": _api_key(),
        "x-rapidapi-host": SCRAPENINJA_HOST,
        "Content-Type": "application/json",
    }


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_text_from_scrapeninja(payload: Any) -> tuple[str, dict[str, Any]]:
    """Best-effort extract readable text + meta from ScrapeNinja JSON."""
    meta: dict[str, Any] = {"raw_type": type(payload).__name__}
    if payload is None:
        return "", meta
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:
            raw = payload.decode("utf-8", errors="replace")
            return _strip_html(raw), {"raw_type": "bytes"}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return _strip_html(payload), {"raw_type": "str"}

    if not isinstance(payload, dict):
        return str(payload)[:5000], meta

    meta["top_keys"] = sorted(payload.keys())
    # Common ScrapeNinja shapes
    candidates: list[str] = []
    for key in ("body", "html", "content", "text", "markdown", "data"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            candidates.append(val)
        elif isinstance(val, dict):
            for k2 in ("body", "html", "content", "text", "markdown"):
                v2 = val.get(k2)
                if isinstance(v2, str) and v2.strip():
                    candidates.append(v2)
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    if info:
        meta["info_status"] = info.get("statusCode") or info.get("status")
        meta["info_keys"] = sorted(info.keys())[:20]

    if not candidates:
        # last resort: stringify small payload
        blob = json.dumps(payload)[:2000]
        return blob, meta

    # Prefer longest candidate (usually full HTML body)
    best = max(candidates, key=len)
    text = _strip_html(best) if "<" in best[:200] else best
    meta["html_len"] = len(best)
    meta["text_len"] = len(text)
    return text, meta


def scrape_current_rapidapi(url: str) -> dict[str, Any]:
    from app.helpers.RapidAPIScraper import RapidAPIScraper

    started = datetime.utcnow()
    result = RapidAPIScraper(delay_seconds=0).scrape(url)
    elapsed_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
    if not result.get("success"):
        return {
            "provider": "ai-content-scraper",
            "ok": False,
            "error": result.get("error"),
            "content_len": 0,
            "elapsed_ms": elapsed_ms,
            "preview": "",
        }
    data = result.get("data") or {}
    content = data.get("content") or ""
    return {
        "provider": "ai-content-scraper",
        "ok": True,
        "error": None,
        "content_len": len(content),
        "elapsed_ms": elapsed_ms,
        "name": data.get("name"),
        "description": (data.get("description") or "")[:200],
        "preview": content[:300].replace("\n", " "),
        "thin": len(content) < THIN_CONTENT_CHARS,
    }


def scrape_scrapeninja(url: str, *, js: bool) -> dict[str, Any]:
    path = "/v2/scrape-js" if js else "/scrape"
    provider = "scrapeninja-js" if js else "scrapeninja-static"
    body: dict[str, Any] = {"url": url}
    if js:
        body.update({"geo": "us", "timeout": 20})
    started = datetime.utcnow()
    try:
        resp = requests.post(
            f"https://{SCRAPENINJA_HOST}{path}",
            headers=_headers(),
            json=body,
            timeout=90 if js else 60,
        )
        elapsed_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
        raw_text = resp.text
        try:
            payload = resp.json()
        except Exception:
            payload = raw_text
        text, meta = _extract_text_from_scrapeninja(payload)
        ok = resp.status_code < 400 and len(text) > 0
        return {
            "provider": provider,
            "ok": ok,
            "http_status": resp.status_code,
            "error": None if ok else f"http={resp.status_code} empty_or_error",
            "content_len": len(text),
            "elapsed_ms": elapsed_ms,
            "meta": meta,
            "preview": text[:300].replace("\n", " "),
            "thin": len(text) < THIN_CONTENT_CHARS,
            "has_form_signal": bool(
                re.search(r"\b(apply|speaker|submit|form|enquiry|inquiry|call for)\b", text, re.I)
            ),
        }
    except Exception as e:
        elapsed_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
        return {
            "provider": provider,
            "ok": False,
            "error": str(e),
            "content_len": 0,
            "elapsed_ms": elapsed_ms,
            "preview": "",
            "thin": True,
            "has_form_signal": False,
        }


def load_failed_mongo_urls(limit: int) -> list[str]:
    import certifi
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_CONNECTION_STRING")
    db_name = os.getenv("DB_NAME")
    if not uri or not db_name:
        return []
    client = MongoClient(uri, tlsCAFile=certifi.where())
    col = client[db_name]["UrlCollections"]
    urls: list[str] = []
    seen: set[str] = set()
    for doc in col.find({"status": "failed"}, {"url": 1}).sort("createdAt", -1).limit(limit * 3):
        u = (doc.get("url") or "").strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
        if len(urls) >= limit:
            break
    return urls


def winner(row: dict[str, Any]) -> str:
    scores = []
    for key in ("current", "ninja_static", "ninja_js"):
        r = row.get(key) or {}
        if not r.get("ok"):
            scores.append((key, -1))
            continue
        # Prefer non-thin + form signal + length
        score = r.get("content_len") or 0
        if r.get("has_form_signal"):
            score += 5000
        if not r.get("thin"):
            score += 2000
        scores.append((key, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scores[0]
    if best_score < 0:
        return "none"
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare RapidAPI scrapers on hard URLs")
    parser.add_argument("--limit", type=int, default=8, help="Max URLs to test")
    parser.add_argument(
        "--include-failed-mongo",
        action="store_true",
        help="Also sample status=failed UrlCollections from Mongo",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "scrapeninja-comparison.json"),
        help="Write JSON results path",
    )
    parser.add_argument(
        "--skip-js",
        action="store_true",
        help="Skip ScrapeNinja /v2/scrape-js (cheaper/faster smoke)",
    )
    args = parser.parse_args()

    urls = list(KNOWN_JS_OR_THIN_URLS)
    if args.include_failed_mongo:
        urls.extend(load_failed_mongo_urls(args.limit))

    # de-dupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    ordered = ordered[: max(1, args.limit)]

    print(f"Testing {len(ordered)} URLs (thin threshold={THIN_CONTENT_CHARS} chars)\n")
    rows: list[dict[str, Any]] = []

    for i, url in enumerate(ordered, 1):
        print("=" * 88)
        print(f"[{i}/{len(ordered)}] {url}")
        current = scrape_current_rapidapi(url)
        print(
            f"  current ai-content-scraper: ok={current['ok']} len={current['content_len']} "
            f"thin={current.get('thin')} {current['elapsed_ms']}ms err={current.get('error')}"
        )
        ninja_static = scrape_scrapeninja(url, js=False)
        print(
            f"  scrapeninja /scrape:       ok={ninja_static['ok']} len={ninja_static['content_len']} "
            f"thin={ninja_static.get('thin')} form={ninja_static.get('has_form_signal')} "
            f"{ninja_static['elapsed_ms']}ms err={ninja_static.get('error')}"
        )
        if args.skip_js:
            ninja_js = {
                "provider": "scrapeninja-js",
                "ok": False,
                "error": "skipped",
                "content_len": 0,
                "thin": True,
                "has_form_signal": False,
                "elapsed_ms": 0,
            }
        else:
            ninja_js = scrape_scrapeninja(url, js=True)
            print(
                f"  scrapeninja /v2/scrape-js: ok={ninja_js['ok']} len={ninja_js['content_len']} "
                f"thin={ninja_js.get('thin')} form={ninja_js.get('has_form_signal')} "
                f"{ninja_js['elapsed_ms']}ms err={ninja_js.get('error')}"
            )

        row = {"url": url, "current": current, "ninja_static": ninja_static, "ninja_js": ninja_js}
        row["winner"] = winner(row)
        print(f"  → winner: {row['winner']}")
        rows.append(row)

    # Summary
    tallies = {"current": 0, "ninja_static": 0, "ninja_js": 0, "none": 0}
    for r in rows:
        tallies[r["winner"]] = tallies.get(r["winner"], 0) + 1

    current_thin = sum(1 for r in rows if (r["current"] or {}).get("thin") or not r["current"].get("ok"))
    js_good = sum(
        1
        for r in rows
        if r["ninja_js"].get("ok") and not r["ninja_js"].get("thin") and r["ninja_js"].get("has_form_signal")
    )

    summary = {
        "tested": len(rows),
        "winner_counts": tallies,
        "current_thin_or_fail": current_thin,
        "ninja_js_usable_with_form_signal": js_good,
        "thin_threshold_chars": THIN_CONTENT_CHARS,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    out = {"summary": summary, "results": rows}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 88)
    print("SUMMARY")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
