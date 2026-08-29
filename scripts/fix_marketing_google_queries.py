"""
Three-category GoogleQuery rewrite (AI / Marketing / AI+Marketing).

Subcommands:
  baseline  — snapshot customer-domain SERP / UrlCollections / Opportunities matrix
  apply     — rewrite/insert 6 booleans + insert 12 soft free-text as pending
  verify    — diff vs baseline; write markdown + JSON results under docs/

Usage (from project root):
  .venv/bin/python scripts/fix_marketing_google_queries.py baseline
  .venv/bin/python scripts/fix_marketing_google_queries.py apply
  .venv/bin/python scripts/process_pending_google_queries.py --limit 20
  .venv/bin/python scripts/fix_marketing_google_queries.py verify
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from bson import ObjectId

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("fix_marketing_google_queries")

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
BASELINE_PATH = DOCS / "marketing-query-fix-baseline.json"
RESULTS_JSON_PATH = DOCS / "marketing-query-fix-results.json"
RESULTS_MD_PATH = DOCS / "marketing-query-fix-results.md"

CUSTOMER_DOMAINS = [
    "2026.allthingsai.org",
    "ai4.io",
    "ana.foleon.com",
    "events.cmoalliance.com",
    "events.reutersevents.com",
    "reg.theaisummit.com",
    "theaisummit.com",
    "sessionize.com",
    "aidataanalytics.network",
    "corporatecompliance.org",
    "enterpriseaiworld.com",
    "jupiter-miami.com",
    "marketingaiinstitute.com",
    "worldsummit.ai",
]

CFS_GROUP = (
    '("call for speakers" OR "apply to speak" OR "speaker application" OR "submit a talk")'
)

# category -> list of (kind, query_text)
# kind: boolean | soft
QUERY_SETS: dict[str, list[tuple[str, str]]] = {
    "ai": [
        (
            "boolean",
            f'("artificial intelligence" OR "generative AI" OR "machine learning" OR "AI conference") '
            f"AND {CFS_GROUP} (2026 OR 2027)",
        ),
        (
            "boolean",
            f'("AI summit" OR "enterprise AI" OR "AI world" OR "applied AI") '
            f"AND {CFS_GROUP} (2026 OR 2027)",
        ),
        ("soft", "call for speakers AI conference"),
        ("soft", "upcoming call for speakers artificial intelligence"),
        ("soft", "apply to speak AI summit"),
        ("soft", "AI conference looking for speakers"),
    ],
    "marketing": [
        (
            "boolean",
            f'("digital marketing" OR "marketing strategy" OR "martech" OR "brand strategy") '
            f'AND ("call for speakers" OR "apply to speak" OR "speaker application" OR '
            f'"submit a talk" OR "CFP" OR "CFS") (2026 OR 2027)',
        ),
        (
            "boolean",
            f'("content marketing" OR "growth marketing" OR "advertising" OR "CMO") '
            f"AND {CFS_GROUP} (2026 OR 2027)",
        ),
        ("soft", "call for speakers digital marketing"),
        ("soft", "upcoming call for speakers marketing conference"),
        ("soft", "apply to speak martech conference"),
        ("soft", "digital marketing conference looking for speakers"),
    ],
    "ai_marketing": [
        (
            "boolean",
            f'("AI marketing" OR "marketing AI" OR "AI for marketing" OR "AI in marketing") '
            f"AND {CFS_GROUP} (2026 OR 2027)",
        ),
        (
            "boolean",
            f'("marketing AI" OR "AI marketing summit" OR "AI for marketers") '
            f"AND {CFS_GROUP} (2026 OR 2027)",
        ),
        ("soft", "call for speakers AI marketing"),
        ("soft", "upcoming call for speakers AI marketing"),
        ("soft", "apply to speak AI marketing conference"),
        ("soft", "open call for speakers marketing AI"),
    ],
}

# Prefer in-place rewrite for these existing first-party docs (marketing batch + pure AI).
REWRITE_CANDIDATES = {
    "ai": [
        "6a60fc1412de5cb877cf8b60",
        "6a60fc1412de5cb877cf8b62",
    ],
    "marketing": [
        "6a60fc1412de5cb877cf8b92",
        "6a60fc1412de5cb877cf8b93",
    ],
    "ai_marketing": [
        "6a60fc1412de5cb877cf8b95",
        "6a60fc1412de5cb877cf8b94",  # second AI+marketing boolean (was diluted marketing)
    ],
}


def _normalize_query(q: str) -> str:
    return " ".join((q or "").split())


def _domain_in_url(url: str, domain: str) -> bool:
    return domain.lower() in (url or "").lower()


def _opp_urls(doc: dict) -> list[str]:
    urls: list[str] = []
    for key in ("link", "url"):
        v = doc.get(key)
        if isinstance(v, str) and v.strip():
            urls.append(v.strip())
    source = doc.get("source") or {}
    if isinstance(source, dict):
        for key in ("source_url", "url"):
            v = source.get(key)
            if isinstance(v, str) and v.strip():
                urls.append(v.strip())
    meta = doc.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("sourceUrl", "website", "submission_link", "url"):
            v = meta.get(key)
            if isinstance(v, str) and v.strip():
                urls.append(v.strip())
    return urls


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat() + ("Z" if dt.tzinfo is None else "")
    return str(dt)


async def _connect():
    from app.helpers.Database import MongoDB

    uri = os.getenv("MONGODB_CONNECTION_STRING")
    db_name = os.getenv("DB_NAME")
    if not uri or not db_name:
        raise SystemExit("Missing MONGODB_CONNECTION_STRING or DB_NAME")
    MongoDB.connect(uri)
    return MongoDB.get_database(db_name)


async def build_domain_matrix(db) -> dict[str, Any]:
    gq_col = db["GoogleQueries"]
    uc_col = db["UrlCollections"]
    opp_col = db["Opportunities"]

    google_queries = await gq_col.find({}, {"query": 1, "urls": 1, "status": 1, "category": 1}).to_list(1000)
    url_collections = await uc_col.find({}, {"url": 1, "status": 1}).to_list(20000)

    # Opportunities: project link-related fields
    opportunities = await opp_col.find(
        {},
        {
            "event_name": 1,
            "link": 1,
            "url": 1,
            "source": 1,
            "metadata": 1,
            "createdAt": 1,
            "isQualified": 1,
        },
    ).to_list(50000)

    matrix: dict[str, Any] = {}
    for domain in CUSTOMER_DOMAINS:
        serp_hits: list[dict] = []
        for gq in google_queries:
            urls = gq.get("urls") or []
            matched = [u for u in urls if isinstance(u, str) and _domain_in_url(u, domain)]
            if matched:
                serp_hits.append(
                    {
                        "google_query_id": str(gq["_id"]),
                        "query": gq.get("query"),
                        "category": gq.get("category"),
                        "matched_urls": matched[:10],
                    }
                )

        uc_hits = [
            {"url_collection_id": str(uc["_id"]), "url": uc.get("url"), "status": uc.get("status")}
            for uc in url_collections
            if _domain_in_url(uc.get("url") or "", domain)
        ]

        opp_hits = []
        for opp in opportunities:
            urls = _opp_urls(opp)
            if any(_domain_in_url(u, domain) for u in urls):
                opp_hits.append(
                    {
                        "opportunity_id": str(opp["_id"]),
                        "event_name": opp.get("event_name"),
                        "link": opp.get("link") or (urls[0] if urls else None),
                        "createdAt": _iso(opp.get("createdAt")),
                        "isQualified": opp.get("isQualified"),
                    }
                )

        matrix[domain] = {
            "in_serp": bool(serp_hits),
            "in_url_collections": bool(uc_hits),
            "in_opportunities": bool(opp_hits),
            "serp_hit_count": len(serp_hits),
            "url_collection_count": len(uc_hits),
            "opportunity_count": len(opp_hits),
            "serp_hits": serp_hits,
            "url_collections": uc_hits[:50],
            "opportunities": opp_hits[:50],
        }

    return {
        "captured_at": datetime.utcnow().isoformat() + "Z",
        "db_name": os.getenv("DB_NAME"),
        "domains": matrix,
        "totals": {
            "google_queries": len(google_queries),
            "url_collections_scanned": len(url_collections),
            "opportunities_scanned": len(opportunities),
        },
    }


async def cmd_baseline() -> None:
    from app.helpers.Database import MongoDB

    db = await _connect()
    try:
        snapshot = await build_domain_matrix(db)
        DOCS.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        logger.info("Wrote baseline %s", BASELINE_PATH)
        # Quick console summary
        for domain, row in snapshot["domains"].items():
            logger.info(
                "baseline %s serp=%s uc=%s opp=%s",
                domain,
                row["in_serp"],
                row["in_url_collections"],
                row["in_opportunities"],
            )
    finally:
        if MongoDB.client:
            MongoDB.client.close()


async def _reset_google_query(col, oid: ObjectId, query: str, category: str, kind: str) -> None:
    await col.update_one(
        {"_id": oid},
        {
            "$set": {
                "query": query,
                "status": "pending",
                "urls": [],
                "urlCollectionIds": [],
                "error": None,
                "updatedAt": datetime.utcnow(),
                "category": category,
                "queryKind": kind,
                "fixBatch": "three_category_ai_marketing",
            }
        },
    )


async def _insert_google_query(col, query: str, category: str, kind: str) -> str:
    # Skip if identical normalized query already exists
    existing = await col.find({}, {"query": 1}).to_list(2000)
    norm = _normalize_query(query)
    for doc in existing:
        if _normalize_query(doc.get("query") or "") == norm:
            logger.info("Skip insert (duplicate query exists) id=%s", doc["_id"])
            # Still reset to pending so it gets re-run
            await _reset_google_query(col, doc["_id"], query, category, kind)
            return str(doc["_id"])

    doc = {
        "query": query,
        "status": "pending",
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
        "urls": [],
        "urlCollectionIds": [],
        "error": None,
        "category": category,
        "queryKind": kind,
        "fixBatch": "three_category_ai_marketing",
    }
    result = await col.insert_one(doc)
    return str(result.inserted_id)


async def cmd_apply() -> None:
    from app.helpers.Database import MongoDB

    db = await _connect()
    try:
        col = db["GoogleQueries"]
        actions: list[dict] = []

        for category, items in QUERY_SETS.items():
            booleans = [q for kind, q in items if kind == "boolean"]
            softs = [q for kind, q in items if kind == "soft"]
            rewrite_ids = REWRITE_CANDIDATES.get(category, [])

            for i, query in enumerate(booleans):
                if i < len(rewrite_ids):
                    oid = ObjectId(rewrite_ids[i])
                    existing = await col.find_one({"_id": oid})
                    if existing:
                        await _reset_google_query(col, oid, query, category, "boolean")
                        actions.append(
                            {
                                "action": "rewrite",
                                "category": category,
                                "kind": "boolean",
                                "id": str(oid),
                                "query": query,
                            }
                        )
                        logger.info("Rewrote %s boolean[%s] id=%s", category, i, oid)
                        continue
                    logger.warning("Rewrite candidate missing id=%s; inserting instead", rewrite_ids[i])
                new_id = await _insert_google_query(col, query, category, "boolean")
                actions.append(
                    {
                        "action": "insert",
                        "category": category,
                        "kind": "boolean",
                        "id": new_id,
                        "query": query,
                    }
                )
                logger.info("Inserted %s boolean[%s] id=%s", category, i, new_id)

            for query in softs:
                new_id = await _insert_google_query(col, query, category, "soft")
                actions.append(
                    {
                        "action": "insert",
                        "category": category,
                        "kind": "soft",
                        "id": new_id,
                        "query": query,
                    }
                )
                logger.info("Inserted %s soft id=%s query=%s", category, new_id, query)

        apply_log = {
            "applied_at": datetime.utcnow().isoformat() + "Z",
            "actions": actions,
            "pending_expected": len(actions),
        }
        apply_path = DOCS / "marketing-query-fix-apply.json"
        DOCS.mkdir(parents=True, exist_ok=True)
        apply_path.write_text(json.dumps(apply_log, indent=2), encoding="utf-8")
        logger.info("Apply complete: %d actions → %s", len(actions), apply_path)
        print(json.dumps({"actions": len(actions), "path": str(apply_path)}, indent=2))
    finally:
        if MongoDB.client:
            MongoDB.client.close()


def _classify_query(query: str) -> str:
    q = _normalize_query(query)
    for category, items in QUERY_SETS.items():
        for kind, text in items:
            if _normalize_query(text) == q:
                return f"{category}:{kind}"
    if "AND" in q and ("OR" in q):
        return "other_boolean"
    return "other"


async def cmd_verify() -> None:
    from app.helpers.Database import MongoDB

    if not BASELINE_PATH.exists():
        raise SystemExit(f"Baseline missing: {BASELINE_PATH}. Run baseline first.")

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_at = baseline.get("captured_at")
    try:
        baseline_dt = datetime.fromisoformat(baseline_at.replace("Z", ""))
    except Exception:
        baseline_dt = None

    db = await _connect()
    try:
        after = await build_domain_matrix(db)

        # New opportunities after baseline for customer domains
        opp_col = db["Opportunities"]
        new_opps_by_domain: dict[str, list[dict]] = {d: [] for d in CUSTOMER_DOMAINS}
        all_new_customer_opps: list[dict] = []

        query_filter: dict = {}
        if baseline_dt:
            query_filter["createdAt"] = {"$gt": baseline_dt}

        cursor = opp_col.find(
            query_filter,
            {
                "event_name": 1,
                "link": 1,
                "url": 1,
                "source": 1,
                "metadata": 1,
                "createdAt": 1,
                "isQualified": 1,
            },
        )
        async for opp in cursor:
            urls = _opp_urls(opp)
            matched_domains = [d for d in CUSTOMER_DOMAINS if any(_domain_in_url(u, d) for u in urls)]
            if not matched_domains:
                continue
            row = {
                "opportunity_id": str(opp["_id"]),
                "event_name": opp.get("event_name"),
                "link": opp.get("link") or (urls[0] if urls else None),
                "createdAt": _iso(opp.get("createdAt")),
                "isQualified": opp.get("isQualified"),
                "domains": matched_domains,
            }
            all_new_customer_opps.append(row)
            for d in matched_domains:
                new_opps_by_domain[d].append(row)

        # SERP attribution by category for target domains after run
        gq_col = db["GoogleQueries"]
        fix_queries = await gq_col.find(
            {"fixBatch": "three_category_ai_marketing"},
            {"query": 1, "urls": 1, "category": 1, "queryKind": 1, "status": 1},
        ).to_list(100)

        serp_by_category: dict[str, dict[str, list[str]]] = {}
        for gq in fix_queries:
            cat = gq.get("category") or "unknown"
            kind = gq.get("queryKind") or "unknown"
            key = f"{cat}:{kind}"
            urls = gq.get("urls") or []
            for domain in CUSTOMER_DOMAINS:
                matched = [u for u in urls if isinstance(u, str) and _domain_in_url(u, domain)]
                if matched:
                    serp_by_category.setdefault(domain, {}).setdefault(key, []).extend(matched)

        # Diff matrix
        domain_diff = {}
        for domain in CUSTOMER_DOMAINS:
            before = baseline["domains"].get(domain, {})
            after_row = after["domains"].get(domain, {})
            domain_diff[domain] = {
                "before": {
                    "in_serp": before.get("in_serp"),
                    "in_url_collections": before.get("in_url_collections"),
                    "in_opportunities": before.get("in_opportunities"),
                    "opportunity_count": before.get("opportunity_count"),
                },
                "after": {
                    "in_serp": after_row.get("in_serp"),
                    "in_url_collections": after_row.get("in_url_collections"),
                    "in_opportunities": after_row.get("in_opportunities"),
                    "opportunity_count": after_row.get("opportunity_count"),
                },
                "gained_serp": bool(after_row.get("in_serp")) and not bool(before.get("in_serp")),
                "gained_url_collections": bool(after_row.get("in_url_collections"))
                and not bool(before.get("in_url_collections")),
                "gained_opportunities": bool(after_row.get("in_opportunities"))
                and not bool(before.get("in_opportunities")),
                "new_opportunities_since_baseline": new_opps_by_domain.get(domain, []),
                "serp_attribution_from_fix_batch": serp_by_category.get(domain, {}),
            }

        results = {
            "verified_at": datetime.utcnow().isoformat() + "Z",
            "baseline_at": baseline_at,
            "fix_batch_google_queries": [
                {
                    "id": str(g["_id"]),
                    "category": g.get("category"),
                    "queryKind": g.get("queryKind"),
                    "status": g.get("status"),
                    "query": g.get("query"),
                    "url_count": len(g.get("urls") or []),
                }
                for g in fix_queries
            ],
            "domain_diff": domain_diff,
            "new_customer_opportunities": all_new_customer_opps,
        }

        DOCS.mkdir(parents=True, exist_ok=True)
        RESULTS_JSON_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

        # Markdown report
        lines = [
            "# Marketing / AI query fix — verification results",
            "",
            f"**Baseline:** `{baseline_at}`  ",
            f"**Verified:** `{results['verified_at']}`  ",
            f"**DB:** `{os.getenv('DB_NAME')}`",
            "",
            "## Domain matrix (before → after)",
            "",
            "| Domain | SERP | UrlCollections | Opportunities | Gained |",
            "|--------|------|----------------|---------------|--------|",
        ]
        for domain in CUSTOMER_DOMAINS:
            d = domain_diff[domain]
            b, a = d["before"], d["after"]

            def flag(x: bool | None) -> str:
                return "Yes" if x else "No"

            gained = []
            if d["gained_serp"]:
                gained.append("SERP")
            if d["gained_url_collections"]:
                gained.append("UC")
            if d["gained_opportunities"]:
                gained.append("Opp")
            gained_s = ", ".join(gained) if gained else "—"

            lines.append(
                f"| `{domain}` | {flag(b['in_serp'])}→{flag(a['in_serp'])} | "
                f"{flag(b['in_url_collections'])}→{flag(a['in_url_collections'])} | "
                f"{flag(b['in_opportunities'])}→{flag(a['in_opportunities'])} "
                f"({b.get('opportunity_count')}→{a.get('opportunity_count')}) | {gained_s} |"
            )

        lines.extend(["", "## New Opportunities on customer domains (since baseline)", ""])
        if not all_new_customer_opps:
            lines.append("_None yet._")
        else:
            for opp in all_new_customer_opps:
                lines.append(
                    f"- **{opp.get('event_name') or '(no name)'}** — `{opp.get('link')}` "
                    f"(domains: {', '.join(opp.get('domains') or [])}; created `{opp.get('createdAt')}`)"
                )

        lines.extend(["", "## SERP attribution from fix-batch queries", ""])
        any_attr = False
        for domain in CUSTOMER_DOMAINS:
            attr = serp_by_category.get(domain) or {}
            if not attr:
                continue
            any_attr = True
            lines.append(f"### `{domain}`")
            for key, urls in attr.items():
                lines.append(f"- **{key}** ({len(urls)} urls)")
                for u in urls[:8]:
                    lines.append(f"  - {u}")
            lines.append("")
        if not any_attr:
            lines.append("_No customer domains appeared in fix-batch SERP urls._")

        lines.extend(
            [
                "",
                "## Fix-batch GoogleQuery statuses",
                "",
                "| Category | Kind | Status | URLs | Query |",
                "|----------|------|--------|------|-------|",
            ]
        )
        for g in results["fix_batch_google_queries"]:
            q_short = _normalize_query(g.get("query") or "")[:80]
            lines.append(
                f"| {g.get('category')} | {g.get('queryKind')} | {g.get('status')} | "
                f"{g.get('url_count')} | `{q_short}` |"
            )

        RESULTS_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Wrote %s and %s", RESULTS_JSON_PATH, RESULTS_MD_PATH)
        print(f"Wrote {RESULTS_MD_PATH}")
    finally:
        if MongoDB.client:
            MongoDB.client.close()


def main():
    parser = argparse.ArgumentParser(description="Three-category GoogleQuery fix ops")
    parser.add_argument("command", choices=["baseline", "apply", "verify"])
    args = parser.parse_args()
    if args.command == "baseline":
        asyncio.run(cmd_baseline())
    elif args.command == "apply":
        asyncio.run(cmd_apply())
    else:
        asyncio.run(cmd_verify())


if __name__ == "__main__":
    main()
