"""
Scrapes URL content for the opportunity pipeline.

Default provider: ScrapeNinja (static /scrape, optional /v2/scrape-js fallback),
then AI-normalize HTML into {content, name, description} + deterministic urls.

Legacy provider (SCRAPE_PROVIDER=legacy): RapidAPI AI Content Scraper — kept for
A/B regression scripts only.

Returns the same contract as before:
  { success, data: { content, name, description, urls, ogUrl? } } or { success: False, error }
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from app.helpers.scrape_normalize import (
    extract_urls_from_html,
    html_to_plain_text,
    normalize_html_with_ai,
)

logger = logging.getLogger(__name__)

SCRAPENINJA_HOST = "scrapeninja.p.rapidapi.com"
LEGACY_SCRAPE_URL = "https://ai-content-scraper.p.rapidapi.com/scrape"
_DEFAULT_THIN_CHARS = 500
_DEFAULT_JS_TIMEOUT = 20


def _env_bool(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def scrape_provider() -> str:
    """scrapeninja (default) | legacy"""
    raw = (os.getenv("SCRAPE_PROVIDER") or "scrapeninja").strip().lower()
    if raw in ("legacy", "ai-content-scraper", "old"):
        return "legacy"
    return "scrapeninja"


_BOT_CHALLENGE_HINTS = (
    "please wait while your request is being verified",
    "checking your browser",
    "just a moment",
    "enable javascript",
    "cf-browser-verification",
    "attention required",
    "access denied",
)


def _looks_like_bot_challenge(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _BOT_CHALLENGE_HINTS)


class RapidAPIScraper:
    """
    Page scraper used by discovery / verify pipelines.
    Class name kept for call-site compatibility.
    """

    def __init__(self, delay_seconds: float = 0):
        """
        Args:
            delay_seconds: Optional delay before each scrape request (e.g. rate limits).
        """
        self.api_key = os.getenv("RAPIDAPI_KEY", "")
        self.delay_seconds = float(delay_seconds) if delay_seconds else 0

    def scrape(self, url: str) -> dict:
        """
        Scrape a URL and return LLM-ready fields.

        Returns:
            success: bool
            data: { content: str, name?: str, description?: str, urls?: list } on success
            error: str on failure
        """
        url = (url or "").strip()
        if not url:
            return {"success": False, "error": "URL is required"}

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if not self.api_key:
            logger.error("RAPIDAPI_KEY not configured")
            return {"success": False, "error": "RAPIDAPI_KEY not configured"}

        provider = scrape_provider()
        if provider == "legacy":
            return self._scrape_legacy(url)
        return self._scrape_scrapeninja(url)

    # ------------------------------------------------------------------ legacy
    def _scrape_legacy(self, url: str) -> dict:
        logger.info("Starting legacy AI Content Scraper for url=%s", url[:80])
        try:
            response = requests.post(
                LEGACY_SCRAPE_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-rapidapi-host": "ai-content-scraper.p.rapidapi.com",
                    "x-rapidapi-key": self.api_key,
                },
                json={"url": url},
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("content", "")
            if not content or not isinstance(content, str):
                logger.warning("No content returned from legacy scraper for url=%s", url[:80])
                return {"success": False, "error": "No content returned from scraper"}
            logger.info(
                "Legacy scrape success url=%s content_length=%d",
                url[:80],
                len(content),
            )
            return {
                "success": True,
                "data": {
                    "content": content,
                    "name": data.get("name"),
                    "description": data.get("description"),
                    "urls": data.get("urls", []),
                    "ogUrl": data.get("ogUrl"),
                },
            }
        except requests.exceptions.RequestException as e:
            logger.exception("Legacy scrape request failed for url=%s: %s", url[:80], e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("Legacy scrape error for url=%s: %s", url[:80], e)
            return {"success": False, "error": str(e)}

    # -------------------------------------------------------------- scrapeninja
    def _scrape_scrapeninja(self, url: str) -> dict:
        logger.info("Starting ScrapeNinja scrape for url=%s", url[:80])
        thin_chars = max(1, _env_int("SCRAPENINJA_THIN_CHARS", _DEFAULT_THIN_CHARS))
        enable_js = _env_bool("SCRAPENINJA_ENABLE_JS_FALLBACK", True)

        html, path, err = self._fetch_scrapeninja_html(url, js=False)
        plain, _ = html_to_plain_text(html) if html else ("", "")
        plain_len = len(plain)
        need_js = (not html or plain_len < thin_chars or _looks_like_bot_challenge(plain)) and enable_js
        if need_js:
            logger.info(
                "ScrapeNinja static needs JS fallback (plain_len=%s thin_threshold=%s bot=%s) url=%s",
                plain_len,
                thin_chars,
                _looks_like_bot_challenge(plain),
                url[:80],
            )
            html_js, path_js, err_js = self._fetch_scrapeninja_html(url, js=True)
            plain_js, _ = html_to_plain_text(html_js) if html_js else ("", "")
            if html_js and (
                len(plain_js) > plain_len
                or (_looks_like_bot_challenge(plain) and not _looks_like_bot_challenge(plain_js))
            ):
                html, path, err = html_js, path_js, err_js
                plain, plain_len = plain_js, len(plain_js)

        if not html or not html.strip():
            logger.warning(
                "ScrapeNinja returned no HTML url=%s path=%s err=%s",
                url[:80],
                path,
                err,
            )
            return {"success": False, "error": err or "No HTML returned from ScrapeNinja"}

        try:
            urls = extract_urls_from_html(html, url)
            normalized = normalize_html_with_ai(url, html)
            content = (normalized.get("content") or "").strip()
            if not content:
                content = plain or html_to_plain_text(html)[0]

            # If normalized content still thin/bot and we only used static, try JS once more
            if (
                enable_js
                and path == "static"
                and (len(content) < thin_chars or _looks_like_bot_challenge(content))
            ):
                logger.info(
                    "ScrapeNinja normalized content still thin/bot (len=%s); retrying JS url=%s",
                    len(content),
                    url[:80],
                )
                html_js, path_js, err_js = self._fetch_scrapeninja_html(url, js=True)
                if html_js and html_js.strip():
                    urls_js = extract_urls_from_html(html_js, url)
                    norm_js = normalize_html_with_ai(url, html_js)
                    content_js = (norm_js.get("content") or "").strip() or html_to_plain_text(html_js)[0]
                    if len(content_js) > len(content) or (
                        _looks_like_bot_challenge(content) and not _looks_like_bot_challenge(content_js)
                    ):
                        html, path, err = html_js, path_js, err_js
                        urls, normalized, content = urls_js, norm_js, content_js

            if not content:
                return {"success": False, "error": "No content after ScrapeNinja normalize"}

            logger.info(
                "ScrapeNinja scrape success url=%s path=%s html_len=%d content_len=%d urls=%d",
                url[:80],
                path,
                len(html),
                len(content),
                len(urls),
            )
            return {
                "success": True,
                "data": {
                    "content": content,
                    "name": normalized.get("name"),
                    "description": normalized.get("description"),
                    "urls": urls,
                    "ogUrl": None,
                    "scrapePath": path,
                },
            }
        except Exception as e:
            logger.exception("ScrapeNinja normalize error for url=%s: %s", url[:80], e)
            return {"success": False, "error": str(e)}

    def _fetch_scrapeninja_html(self, url: str, *, js: bool) -> tuple[str, str, str | None]:
        """
        Returns (html_body, path_label, error_message_or_None).
        """
        path = "/v2/scrape-js" if js else "/scrape"
        label = "js" if js else "static"
        body: dict[str, Any] = {"url": url}
        if js:
            body["geo"] = "us"
            body["timeout"] = max(5, _env_int("SCRAPENINJA_JS_TIMEOUT", _DEFAULT_JS_TIMEOUT))
            # No screenshot in production — cost/payload

        timeout = 90 if js else 60
        try:
            response = requests.post(
                f"https://{SCRAPENINJA_HOST}{path}",
                headers={
                    "Content-Type": "application/json",
                    "x-rapidapi-host": SCRAPENINJA_HOST,
                    "x-rapidapi-key": self.api_key,
                },
                json=body,
                timeout=timeout,
            )
            if response.status_code >= 400:
                return "", label, f"ScrapeNinja {label} HTTP {response.status_code}"

            try:
                payload = response.json()
            except Exception:
                # Rare: raw HTML body
                text = response.text or ""
                return text, label, None if text.strip() else f"ScrapeNinja {label} empty body"

            html = self._extract_body_html(payload)
            info = payload.get("info") if isinstance(payload, dict) else None
            status = None
            if isinstance(info, dict):
                status = info.get("statusCode") or info.get("status")
            if status is not None:
                try:
                    code = int(status)
                    if code >= 400 and not (html or "").strip():
                        return "", label, f"ScrapeNinja {label} upstream status={code}"
                except (TypeError, ValueError):
                    pass
            if not (html or "").strip():
                return "", label, f"ScrapeNinja {label} empty HTML"
            return html, label, None
        except requests.exceptions.RequestException as e:
            logger.warning("ScrapeNinja %s request failed url=%s err=%s", label, url[:80], e)
            return "", label, str(e)
        except Exception as e:
            logger.exception("ScrapeNinja %s error url=%s", label, url[:80])
            return "", label, str(e)

    @staticmethod
    def _extract_body_html(payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return ""
        for key in ("body", "html", "content", "text"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, dict):
                for k2 in ("body", "html", "content", "text"):
                    v2 = val.get(k2)
                    if isinstance(v2, str) and v2.strip():
                        return v2
        return ""
