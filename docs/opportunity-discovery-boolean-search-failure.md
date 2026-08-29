# Opportunity Discovery Failure: Boolean Search vs Customer Google Search

**Date:** 2026-08-28  
**Environment audited:** `dev` MongoDB (`GoogleQueries`, `UrlCollections`, `Opportunities`)  
**Customer search:** `calls for speakers AI marketing`  
**Symptom:** Customer found multiple call-for-speakers (CFS) domains via a simple Google search; our heavy boolean GoogleQuery set did not surface most of them as Opportunities.

---

## 1. Summary

Two separate failure modes explain the gap:

| Layer | What failed | Effect |
|-------|-------------|--------|
| **A. SERP discovery** | Heavy boolean queries never returned most customer domains in the top-20 organic results | Domains never entered the scrape pipeline via GoogleQuery |
| **B. Post-scrape qualification** | Several domains *were* scraped (manual / other UrlCollection paths) but never inserted into `Opportunities` | Even when we already had the URL, the extract → qualify → verify pipeline dropped them |

A plain intent query like the customer’s ranks commercial CFS pages highly. Our boolean queries optimize for precision (exact phrases, year, geo, topic OR-groups) and lose that recall.

---

## 2. Domains in scope

Customer-reported domains (normalized):

- `2026.allthingsai.org`
- `ai4.io`
- `ana.foleon.com`
- `events.cmoalliance.com`
- `events.reutersevents.com`
- `reg.theaisummit.com`
- `sessionize.com`
- `www.aidataanalytics.network`
- `www.corporatecompliance.org`
- `www.enterpriseaiworld.com`
- `www.jupiter-miami.com`
- `www.marketingaiinstitute.com`
- `worldsummit.ai`

Related note: `theaisummit.com` (e.g. `newyork.theaisummit.com`) exists in Opportunities; `reg.theaisummit.com` does not.

---

## 3. Domain outcome matrix

Checked against `GoogleQueries.urls` (SERP), `UrlCollections` (scraped), and `Opportunities` (final inventory).

| Domain | In GoogleQuery SERP? | In UrlCollections? | In Opportunities? | Failure mode |
|--------|----------------------|--------------------|-------------------|--------------|
| `2026.allthingsai.org` | No | Yes (1; `/register` only) | No | SERP miss + wrong page scraped |
| `ai4.io` | Yes (1) | Yes | Yes (1) | Found |
| `ana.foleon.com` | No | No | No | SERP miss (never discovered) |
| `events.cmoalliance.com` | No | No | No | SERP miss (never discovered) |
| `events.reutersevents.com` | No | Yes (18) | No | Qualification drop |
| `reg.theaisummit.com` | No | No | No | SERP miss (never discovered) |
| `theaisummit.com` (any host) | Yes (1; NY) | Yes | Yes (1; NY) | Partial — London CFS scraped, not stored as Opp |
| `sessionize.com` | Yes (many) | Yes (many) | Yes (51) | Found (platform-wide) |
| `aidataanalytics.network` | No | No | No | SERP miss (never discovered) |
| `corporatecompliance.org` | Yes (1; EdTech query) | Yes (6) | No | Qualification drop |
| `enterpriseaiworld.com` | No | Yes (6; CallForSpeakers) | No | Qualification drop |
| `jupiter-miami.com` | No | Yes (1; apply-to-speak) | No | Qualification drop |
| `marketingaiinstitute.com` | No | No | No | SERP miss (never discovered) |
| `worldsummit.ai` | No | Yes (4; speaker form) | No | Qualification drop |

**Net:** Of the customer list, only `ai4.io` and `sessionize.com` (plus NY AI Summit under `theaisummit.com`) landed as Opportunities. Most domains failed at SERP; several that we *did* scrape still failed qualification.

---

## 4. What we searched (marketing / AI marketing booleans)

We have **6 marketing-related** GoogleQueries (of 74 total). Closest to the customer intent:

```text
("AI marketing" OR "SEO" OR "influencer marketing" OR "conversion rate optimization")
AND "submit a proposal" (2026 OR 2027)
```

Other marketing queries:

```text
("digital marketing" OR "advertising" OR "martech")
AND ("call for speakers" OR "submit a talk")
AND (USA OR "United States" OR "New York" OR "Boston" OR "San Diego") (2026 OR 2027)

("brand strategy" OR "growth marketing" OR "content marketing")
AND "call for papers"
AND (Canada OR Toronto OR Vancouver OR Mexico) (2026 OR 2027)

("digital marketing" OR "growth hacking" OR "marketing strategy")
AND ("CFP" OR "CFS" OR "apply to speak") (2026 OR 2027)

site:sessionize.com ("marketing" OR "digital marketing" OR "martech" OR "growth") (2026 OR 2027)

site:meetup.com ("marketing" OR "digital marketing" OR "social media marketing")
AND ("speaker" OR "looking for speakers") (2026 OR 2027)
```

**SERP hits from the customer domain list across all 6 marketing queries:** only `sessionize.com` (from the sessionize / CFP-style queries). **Zero** hits for the other customer domains.

### Actual top-20 for the “AI marketing” boolean

Stored organic URLs were dominated by **agency RFPs, social posts, Digimarcon speaker-request pages, and vendor content** — not the commercial CFS sites the customer found. Examples:

- `conferences.upcea.edu/.../proposals.html`
- Instagram / Facebook posts
- `contentmarketingworld.com/speaker-submissions/`
- SEO agency RFP templates / DesignRush / Upwork tips
- Government BID documents
- Digimarcon `*/speaker-requests/` pages

None of: Marketing AI Institute, Enterprise AI World, World Summit AI, CMO Alliance, All Things AI, AI Data Analytics Network, etc.

---

## 5. Root causes — Layer A (SERP / discovery)

### 5.1 Customer query vs boolean shape

| | Customer | Our heavy boolean |
|--|----------|-------------------|
| Form | Natural language intent | Nested `AND` / `OR` / `site:` |
| Phrasing | “calls for speakers” | Often forced: `"submit a proposal"`, `"call for papers"`, `CFP`/`CFS` |
| Topic | Tight: AI + marketing | Diluted: SEO, influencer, CRO, growth hacking, etc. |
| Year | Implicit / ranking-based | Almost always `(2026 OR 2027)` (73/74 queries) |
| Geo | None | Many queries lock USA+cities or Canada |

Google ranks CFS landing pages well for soft intent. Boolean precision filters those pages out of the top results.

### 5.2 Forced phrase mismatch

Many real CFS pages use:

- “call for speakers”
- “apply to speak”
- “speaker application”
- “submit a speaker proposal”

Requiring `"submit a proposal"` or `"call for papers"` excludes pages that would match the customer’s wording. `"call for papers"` also biases toward academic tracks.

### 5.3 OR-group dilution

Expanding `"AI marketing"` with `"SEO" OR "influencer marketing" OR "conversion rate optimization"` shifts the result set toward **agency procurement / RFP** content. That matches the stored SERP list for the AI marketing query.

### 5.4 Hard top-20 ceiling

Pipeline behavior (`GoogleQueryScraperService` + `SerpHelper.search_multi_page`):

- RapidAPI Real-Time Web Search
- **Max 20 organic URLs** per query (`GOOGLE_QUERY_TOP_N = 20`, 2 × 10 pages)
- Then scrape → LLM extract → qualify → verify

Even a slightly better boolean can miss mid-ranked CFS pages a human finds by scrolling further or using a simpler query.

### 5.5 Site-scoped queries

`site:sessionize.com` and `site:meetup.com` marketing queries only cover those platforms. They cannot discover first-party event domains (Enterprise AI World, World Summit AI, Marketing AI Institute, etc.).

---

## 6. Root causes — Layer B (scraped but not Opportunities)

Several customer domains appear in `UrlCollections` with completed scrapes of **clear CFS URLs**, yet `Opportunities` count is 0:

| URL pattern | Evidence of CFS intent | Still no Opportunity |
|-------------|------------------------|----------------------|
| `enterpriseaiworld.com/.../CallForSpeakers.aspx` | “Submit a Proposal to Speak…” | Yes |
| `worldsummit.ai/form-speakers-enquiries/` | “Apply to speak at World Summit AI” | Yes |
| `jupiter-miami.com/apply-to-speak` | “Apply to Speak…” | Yes |
| `corporatecompliance.org/...call-speakers` | Explicit call for speakers | Yes |
| `events.reutersevents.com/marketing/csx` | Event hub (may lack open CFS on scraped URL) | Yes |

Likely drop points after scrape (pipeline order):

1. LLM extraction finds no usable opportunity / wrong page type  
2. Qualification clauses (deadline closed, no submission path, Meetup-style rules, etc.)  
3. Official-site verify (`OpportunityQualifier` / `EventDetailEnricherAgent`) rejects `hosts_speaking_opportunity`  
4. Dedupe or insert path never persists a root Opportunity document  

**Special case — All Things AI:** only `https://2026.allthingsai.org/register` was scraped (registration), not a CFS page — discovery targeted the wrong URL.

**Special case — AI Summit:** `newyork.theaisummit.com` is in Opportunities; `london.theaisummit.com/.../submit-speaker/` was scraped repeatedly but not stored as an Opportunity; `reg.theaisummit.com` never discovered.

---

## 7. Pipeline context (for engineers)

```text
GoogleQuery (boolean string)
  → SerpHelper (RapidAPI, top 20 URLs)
  → skip URLs already known in Opportunities
  → UrlCollections scrape (RapidAPI)
  → SpeakingOpportunityExtractor / discovery pipeline
  → OpportunityQualifier (clauses + official-site LLM verify)
  → Opportunities insert (+ vector store when applicable)
```

Relevant code:

- `app/services/GoogleQueryScraper.py` — SERP + scrape orchestration  
- `app/helpers/SerpHelper.py` — RapidAPI search, multi-page top N  
- `app/helpers/OpportunityQualifier.py` — post-extract keep/drop  
- `app/agents/EventDetailEnricherAgent.py` — speaking-opportunity verification  

GoogleQueries audited were largely created **2026-07-22** as a batch of topic/geo boolean strings; marketing subset as listed in §4.

---

## 8. Conclusions

1. **Primary gap is query design + SERP depth**, not “Google doesn’t index these sites.” The customer’s simple query surfaces CFS domains; our booleans do not return them in the top 20.  
2. **The AI marketing boolean is especially misaligned** — forced `"submit a proposal"` + SEO/influencer/CRO ORs produce RFP/agency noise.  
3. **Secondary gap is qualification** — Enterprise AI World, World Summit AI, Jupiter Miami, Corporate Compliance (and others) were already scraped as CFS-like URLs and still never became Opportunities.  
4. **Inventory today** for this customer list is effectively only `ai4.io`, `sessionize.com` (many events), and one `theaisummit.com` (New York) opportunity.

---

## 9. Recommended follow-ups (out of scope for this report)

Not implemented here; listed for planning:

1. Add **soft intent** companion queries (e.g. `call for speakers AI marketing 2026`) alongside heavy booleans.  
2. Relax / diversify CFS phrase OR-groups (`call for speakers`, `apply to speak`, `speaker application`, …) instead of a single forced phrase.  
3. Avoid diluting niche topics with unrelated commercial OR terms (SEO agency RFPs).  
4. Re-run qualification / verify on known UrlCollection CFS URLs that never produced Opportunities.  
5. Optionally raise SERP depth or add a second-pass query for high-value topics.  
6. Track per-URL drop reasons in UrlCollections for faster audits next time.

---

## 10. Audit method

- DB: `DB_NAME` from `.env` (`dev` at audit time)  
- Collections: `GoogleQueries`, `UrlCollections`, `Opportunities`  
- Match: case-insensitive regex on `urls` / `url` / `link` / `source_url` for each domain  
- Marketing query set: `GoogleQueries` where `query` matches `/marketing/i`
