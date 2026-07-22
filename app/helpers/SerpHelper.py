"""SERP Helper - Google search via RapidAPI Real-Time Web Search returning URLs only."""
import logging
import os
from typing import List
from urllib.parse import urlparse, urlunparse

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SEARCH_URL = "https://real-time-web-search.p.rapidapi.com/search"
SEARCH_HOST = "real-time-web-search.p.rapidapi.com"
DEFAULT_PAGE_SIZE = 10


def normalize_url_for_dedupe(url: str) -> str:
    """Normalize URL for duplicate checks (strip, drop fragment, trailing slash on path)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        path = (p.path or "").rstrip("/") or ""
        return urlunparse((p.scheme.lower(), (p.netloc or "").lower(), path, "", p.query, ""))
    except Exception:
        return raw.rstrip("/")


class SerpHelper:
    """Helper for Google search queries - returns organic result URLs."""

    def __init__(self):
        self.api_key = os.getenv("RAPIDAPI_KEY", "")

    def search(self, query: str, num: int = 10, start: int = 0) -> List[str]:
        """
        Search Google via RapidAPI Real-Time Web Search and return organic result URLs.

        Args:
            query: Search query string
            num: Number of results to request for this page (default 10)
            start: Result offset for pagination (0 = first page, 10 = second page, ...)

        Returns:
            List of URL strings from organic search results

        Raises:
            ValueError: If RAPIDAPI_KEY is not set or query is empty
            RuntimeError: If the API request fails or returns a non-OK status
        """
        if not self.api_key:
            raise ValueError("Missing RAPIDAPI_KEY in environment variables")
        query = (query or "").strip()
        if not query:
            raise ValueError("Search query is required")

        params = {
            "q": query,
            "fetch_ai_overviews": "false",
            "num": str(num),
            "start": str(max(0, int(start))),
            "gl": "us",
            "hl": "en",
            "nfpr": "0",
            "return_organic_result_video_thumbnail": "false",
            "deduplicate": "false",
        }
        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": SEARCH_HOST,
            "Content-Type": "application/json",
        }

        try:
            response = requests.get(SEARCH_URL, headers=headers, params=params, timeout=60)
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"RapidAPI web search request failed: {e}") from e

        status = (payload.get("status") or "").upper()
        if status != "OK":
            raise RuntimeError(
                f"RapidAPI web search returned status={payload.get('status')!r} for query={query!r}"
            )

        organic = (payload.get("data") or {}).get("organic_results") or []
        urls: List[str] = []
        for item in organic:
            if not isinstance(item, dict):
                continue
            link = (item.get("url") or "").strip()
            if link:
                urls.append(link)

        logger.info(
            "RapidAPI web search query=%r start=%s num=%s returned %d urls",
            query[:120],
            start,
            num,
            len(urls),
        )
        return urls

    def search_multi_page(
        self,
        query: str,
        total: int = 20,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> List[str]:
        """
        Fetch up to `total` organic URLs across consecutive SERP pages (e.g. 2x10 = 20).
        Deduplicates by normalized URL while preserving order.
        """
        total = max(1, int(total))
        page_size = max(1, int(page_size))
        seen: set[str] = set()
        out: List[str] = []
        start = 0
        while len(out) < total:
            page = self.search(query, num=page_size, start=start)
            if not page:
                break
            for url in page:
                key = normalize_url_for_dedupe(url)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(url)
                if len(out) >= total:
                    break
            if len(page) < page_size:
                break
            start += page_size
        logger.info(
            "RapidAPI web search multi-page query=%r total_requested=%d returned %d urls",
            query[:120],
            total,
            len(out),
        )
        return out
