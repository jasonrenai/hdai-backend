"""SERP Helper - Google search via RapidAPI Real-Time Web Search returning URLs only."""
import logging
import os
from typing import List

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SEARCH_URL = "https://real-time-web-search.p.rapidapi.com/search"
SEARCH_HOST = "real-time-web-search.p.rapidapi.com"


class SerpHelper:
    """Helper for Google search queries - returns organic result URLs."""

    def __init__(self):
        self.api_key = os.getenv("RAPIDAPI_KEY", "")

    def search(self, query: str, num: int = 10) -> List[str]:
        """
        Search Google via RapidAPI Real-Time Web Search and return organic result URLs.

        Args:
            query: Search query string
            num: Number of results to request (default 10)

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
            "start": "0",
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

        logger.info("RapidAPI web search query=%r returned %d urls", query[:120], len(urls))
        return urls
