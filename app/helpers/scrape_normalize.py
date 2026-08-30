"""
Normalize ScrapeNinja HTML into the legacy scraper shape:
  { content, name, description, urls }

- urls: deterministic href extraction (no LLM)
- content/name/description: OpenAI cleanup of visible page text; plain-text fallback on failure
"""
from __future__ import annotations

import json
import logging
import os
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

_DEFAULT_HTML_TRUNCATE = 100_000
_MAX_URLS = 200


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, val in attrs:
            if key.lower() == "href" and val:
                self.hrefs.append(val.strip())


class _TextExtractor(HTMLParser):
    """Extract visible text; skip script/style/noscript."""

    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self.title: str = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip_depth += 1
            return
        if t == "title":
            self._in_title = True
        if t in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if t == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = (data or "").strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        self._parts.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self._parts)
        return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", raw))).strip()


def strip_scripts_styles(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    return text


def html_to_plain_text(html: str) -> tuple[str, str]:
    """Return (visible_text, title)."""
    cleaned = strip_scripts_styles(html or "")
    parser = _TextExtractor()
    try:
        parser.feed(cleaned)
        parser.close()
    except Exception:
        # Extremely broken HTML — crude fallback
        crude = re.sub(r"(?s)<[^>]+>", " ", cleaned)
        crude = re.sub(r"\s+", " ", crude).strip()
        return crude, ""
    return parser.get_text(), parser.title


def extract_urls_from_html(html: str, base_url: str) -> list[str]:
    """Deterministic absolute http(s) links from href attributes."""
    collector = _HrefCollector()
    try:
        collector.feed(html or "")
        collector.close()
    except Exception:
        collector.hrefs = re.findall(r"""href=["']([^"']+)["']""", html or "", flags=re.I)

    seen: set[str] = set()
    out: list[str] = []
    for href in collector.hrefs:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        # Drop fragments for dedupe stability
        norm = abs_url.split("#", 1)[0]
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
        if len(out) >= _MAX_URLS:
            break
    return out


def _html_truncate_chars() -> int:
    raw = (os.getenv("SCRAPENINJA_HTML_TRUNCATE_CHARS") or "").strip()
    if not raw:
        return _DEFAULT_HTML_TRUNCATE
    try:
        return max(10_000, int(raw))
    except ValueError:
        return _DEFAULT_HTML_TRUNCATE


def normalize_html_with_ai(url: str, html: str) -> dict[str, Any]:
    """
    Returns {content, name, description}. Never invents CFS facts — cleans visible text.
    Falls back to plain-text extraction if OpenAI is unavailable or fails.
    """
    plain, title = html_to_plain_text(html)
    cleaned_html = strip_scripts_styles(html or "")
    truncate = _html_truncate_chars()
    html_for_model = cleaned_html[:truncate]

    fallback = {
        "content": plain,
        "name": title or None,
        "description": (plain[:280] + "...") if len(plain) > 280 else (plain or None),
    }

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY missing; using plain-text scrape normalize for url=%s", url[:80])
        return fallback
    if not html_for_model.strip() and not plain.strip():
        return fallback

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        system = (
            "You clean scraped HTML into fields for a speaking-opportunity discovery pipeline. "
            "Return ONLY valid JSON with keys: name (string), description (string), content (string). "
            "CRITICAL for content: return the FULL visible page text cleaned into plain text or light "
            "markdown — do NOT summarize, shorten, or omit sections. Preserve headings, call-for-speakers / "
            "apply-to-speak wording, deadlines, dates, locations, form labels, and links as text. "
            "Do NOT invent events, dates, or apply paths that are not on the page. "
            "name is the page/document title when present. description is a short 1–2 sentence summary only "
            "(description may be short; content must stay long and complete)."
        )
        user = (
            f"Page URL: {url}\n\n"
            f"Pre-extracted visible text (prefer expanding from this; do not drop it):\n"
            f"---\n{plain[: min(len(plain), truncate)]}\n---\n\n"
            f"HTML (may be truncated):\n---\n{html_for_model}\n---"
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed = json.loads(raw) if raw else {}
        content = str(parsed.get("content") or "").strip() or plain
        # If the model over-summarized, keep the fuller plain extraction
        if plain and len(content) < max(500, int(0.5 * len(plain))):
            logger.info(
                "AI normalize over-summarized (ai_len=%d plain_len=%d); using plain text content url=%s",
                len(content),
                len(plain),
                url[:80],
            )
            content = plain
        name = str(parsed.get("name") or "").strip() or title or None
        description = str(parsed.get("description") or "").strip() or fallback["description"]
        return {"content": content, "name": name, "description": description}
    except Exception:
        logger.exception("AI scrape normalize failed url=%s; using plain text", url[:80])
        return fallback
