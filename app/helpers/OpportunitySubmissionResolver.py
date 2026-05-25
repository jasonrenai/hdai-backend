"""Resolve speaking opportunity submission details with LLM tool calls."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from openai import OpenAI
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.helpers.RapidAPIScraper import RapidAPIScraper

logger = logging.getLogger(__name__)

DEADLINE_NOT_FOUND = "deadline not found"
MAX_SOURCE_CONTENT_CHARS = 12000
MAX_TOOL_CONTENT_CHARS = 10000
MAX_DISCOVERED_LINKS = 100
MAX_TOOL_CALL_ROUNDS = 6


SUBMISSION_RESOLVER_SYSTEM_PROMPT = """You resolve speaker application/submission details for speaking opportunities.

Use only evidence from the provided scraped page content, discovered links, and pages you scrape through tools.

Return ONLY valid JSON with exactly these keys:
- status: one of "found", "contact_found", "not_found"
- applicationLink: direct speaker application / call-for-speakers / apply page URL, or ""
- formLink: direct form URL if a form is present or linked, or ""
- submissionEmail: valid email address only, specifically for speaker submissions, proposals, CFPs, or applications, or ""
- deadline: ISO date YYYY-MM-DD when explicitly found; otherwise exactly "deadline not found"
- contactEmail: valid email address only, general organizer/contact email only when submission data cannot be found, otherwise ""
- reason: short evidence-based reason
- sourceUrl: page URL where the strongest evidence was found, or ""

Workflow:
1. First inspect the provided page content and current submissionInfo.
2. If an application/form URL or submission email exists but deadline or details are missing, call scrape_url on the application/form URL to look for the missing fields.
3. If no application data exists in the page content, choose likely pages from discoveredLinks using semantic judgment from URL/text context. Do not use hardcoded endpoint rules or regex-style path filtering.
4. If application data is still not found, look for a contact page or page likely to contain an organizer contact email. Return status "contact_found" with contactEmail and reason "Submission details not found; contact email found." when available.
5. If a valid application/form URL or submission email is found but no deadline is found, keep status "found" and set deadline to "deadline not found".

Do not invent URLs, emails, deadlines, or forms."""


def _parse_date_to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == DEADLINE_NOT_FOUND:
        return None
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        try:
            datetime.strptime(s[:10], "%Y-%m-%d")
            return s[:10]
        except ValueError:
            pass
    formats = [
        "%Y-%m-%d",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%B %Y",
        "%b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s[:50].strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _validate_email_str(value: Any) -> str:
    """Return normalized EmailStr value, or empty string for non-email text."""
    raw = _clean_str(value)
    if not raw:
        return ""
    if raw.lower().startswith("mailto:"):
        raw = raw[7:].strip()
    try:
        return str(TypeAdapter(EmailStr).validate_python(raw))
    except ValidationError:
        return ""


def _absolute_url(value: Any, base_url: str = "") -> str:
    raw = _clean_str(value)
    if not raw:
        return ""
    return urljoin(base_url, raw) if base_url else raw


def _link_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("url", "href", "link"):
            if value.get(key):
                return value.get(key)
    return value


def _first_value(raw: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return ""


def normalize_submission_info(raw: Any, base_url: str = "") -> Dict[str, Any]:
    """Normalize LLM submissionInfo into the root opportunity schema."""
    data = raw if isinstance(raw, dict) else {}
    if isinstance(data.get("submissionInfo"), dict):
        data = {**data, **data["submissionInfo"]}
    deadline = _parse_date_to_iso(
        _first_value(data, "deadline", "applicationDeadline", "submissionDeadline", "application_submission_deadline")
    )

    info = {
        "status": _clean_str(data.get("status")).lower(),
        "applicationLink": _absolute_url(
            _first_value(
                data,
                "applicationLink",
                "applicationUrl",
                "applyUrl",
                "submissionLink",
                "submissionUrl",
                "submission_link",
            ),
            base_url,
        ),
        "formLink": _absolute_url(_first_value(data, "formLink", "formUrl", "submission_form_link"), base_url),
        "submissionEmail": _validate_email_str(
            _first_value(data, "submissionEmail", "submission_email", "email", "speakerEmail")
        ),
        "deadline": deadline or DEADLINE_NOT_FOUND,
        "contactEmail": _validate_email_str(_first_value(data, "contactEmail", "contact_email", "organizerEmail")),
        "reason": _clean_str(data.get("reason")),
        "sourceUrl": _absolute_url(_first_value(data, "sourceUrl", "sourceURL", "evidenceUrl"), base_url),
    }

    has_submission_path = bool(info["applicationLink"] or info["formLink"] or info["submissionEmail"])
    if has_submission_path:
        info["status"] = "found"
    elif info["contactEmail"]:
        info["status"] = "contact_found"
        if not info["reason"]:
            info["reason"] = "Submission details not found; contact email found."
    else:
        info["status"] = "not_found"
        if not info["reason"]:
            info["reason"] = "Submission details not found."

    if not info["sourceUrl"]:
        info["sourceUrl"] = base_url or ""

    return info


def submission_info_has_submission_path(info: Any) -> bool:
    if not isinstance(info, dict):
        return False
    return bool(
        _clean_str(info.get("applicationLink"))
        or _clean_str(info.get("formLink"))
        or _clean_str(info.get("submissionEmail"))
    )


def sync_submission_info_to_metadata(opportunity: Dict[str, Any]) -> None:
    """Keep legacy metadata fields populated for notifications and qualification."""
    info = normalize_submission_info(
        opportunity.get("submissionInfo"),
        base_url=(opportunity.get("link") or opportunity.get("url") or ""),
    )
    opportunity["submissionInfo"] = info

    meta = opportunity.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    for email_key in ("submission_email", "contact_email"):
        if email_key in meta:
            validated_email = _validate_email_str(meta.get(email_key))
            if validated_email:
                meta[email_key] = validated_email
            else:
                meta.pop(email_key, None)
    deadline = info.get("deadline")
    if deadline and deadline != DEADLINE_NOT_FOUND:
        meta["application_submission_deadline"] = deadline
    if info.get("applicationLink"):
        meta["submission_link"] = info["applicationLink"]
    if info.get("formLink"):
        meta["submission_form_link"] = info["formLink"]
    if info.get("submissionEmail"):
        meta["submission_email"] = info["submissionEmail"]
        meta.setdefault("contact_email", info["submissionEmail"])
    if info.get("contactEmail"):
        meta.setdefault("contact_email", info["contactEmail"])
    opportunity["metadata"] = meta


class OpportunitySubmissionResolver:
    """Uses OpenAI tool calls to complete submissionInfo for opportunities."""

    def __init__(self, rapidapi_scraper: RapidAPIScraper = None):
        self.rapidapi_scraper = rapidapi_scraper or RapidAPIScraper()

    def _tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "scrape_url",
                    "description": "Scrape a URL and return markdown content plus discovered links.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "Absolute or source-relative URL to scrape.",
                            }
                        },
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def _scrape_url_tool(self, url: str, base_url: str) -> dict:
        target = _absolute_url(url, base_url)
        if not target:
            return {"success": False, "error": "Missing URL"}
        try:
            result = self.rapidapi_scraper.scrape(target)
        except Exception as e:
            logger.warning("Submission resolver scrape failed for %s: %s", target[:80], e)
            return {"success": False, "url": target, "error": str(e)}
        if not result.get("success"):
            return {"success": False, "url": target, "error": result.get("error", "Scrape failed")}
        data = result.get("data") or {}
        return {
            "success": True,
            "url": target,
            "name": data.get("name") or "",
            "description": data.get("description") or "",
            "content": (data.get("content") or "")[:MAX_TOOL_CONTENT_CHARS],
            "discoveredLinks": self._normalize_links(data.get("urls") or [], target),
        }

    def _normalize_links(self, links: Any, base_url: str) -> list[str]:
        if not isinstance(links, list):
            return []
        normalized = []
        seen = set()
        for link in links:
            url = _absolute_url(_link_value(link), base_url)
            if not url or url in seen:
                continue
            seen.add(url)
            normalized.append(url)
            if len(normalized) >= MAX_DISCOVERED_LINKS:
                break
        return normalized

    def _build_user_prompt(
        self,
        opportunity: Dict[str, Any],
        source_url: str,
        source_page_content: str,
        source_page_links: list[str],
    ) -> str:
        opp_payload = {
            "event_name": opportunity.get("event_name") or opportunity.get("title") or "",
            "link": opportunity.get("link") or opportunity.get("url") or "",
            "metadata": opportunity.get("metadata") if isinstance(opportunity.get("metadata"), dict) else {},
            "submissionInfo": opportunity.get("submissionInfo") if isinstance(opportunity.get("submissionInfo"), dict) else {},
        }
        payload = {
            "sourceUrl": source_url,
            "opportunity": opp_payload,
            "discoveredLinks": source_page_links[:MAX_DISCOVERED_LINKS],
            "sourcePageContent": (source_page_content or "")[:MAX_SOURCE_CONTENT_CHARS],
        }
        return json.dumps(payload, ensure_ascii=True)

    def resolve_submission_info(
        self,
        opportunity: Dict[str, Any],
        source_url: str,
        source_page_content: str,
        source_page_links: list[str],
    ) -> Dict[str, Any]:
        base_url = (opportunity.get("link") or opportunity.get("url") or source_url or "").strip()
        opportunity["submissionInfo"] = normalize_submission_info(
            opportunity.get("submissionInfo"),
            base_url=base_url,
        )
        current_info = opportunity["submissionInfo"]
        if (
            submission_info_has_submission_path(current_info)
            and current_info.get("deadline")
            and current_info.get("deadline") != DEADLINE_NOT_FOUND
        ):
            sync_submission_info_to_metadata(opportunity)
            return opportunity

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            sync_submission_info_to_metadata(opportunity)
            return opportunity

        try:
            client = OpenAI(api_key=api_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            messages: list[dict] = [
                {"role": "system", "content": SUBMISSION_RESOLVER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self._build_user_prompt(
                        opportunity,
                        source_url,
                        source_page_content,
                        self._normalize_links(source_page_links, source_url),
                    ),
                },
            ]

            for _ in range(MAX_TOOL_CALL_ROUNDS):
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=self._tools(),
                    tool_choice="auto",
                    temperature=0.1,
                    timeout=60,
                )
                msg = response.choices[0].message
                if not msg:
                    break

                assistant_msg = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                messages.append(assistant_msg)

                if not msg.tool_calls:
                    parsed = self._parse_json_object(msg.content or "")
                    if parsed:
                        opportunity["submissionInfo"] = normalize_submission_info(parsed, base_url=base_url)
                    break

                for tool_call in msg.tool_calls:
                    try:
                        args = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if tool_call.function.name != "scrape_url":
                        tool_result = {"success": False, "error": "Unknown tool"}
                    else:
                        tool_result = self._scrape_url_tool(args.get("url") or "", base_url)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(tool_result, ensure_ascii=True),
                        }
                    )
        except Exception as e:
            logger.warning("Submission resolver failed for %s: %s", base_url[:80], e)

        sync_submission_info_to_metadata(opportunity)
        return opportunity

    def resolve_opportunities(
        self,
        opportunities: List[Dict[str, Any]],
        source_url: str,
        source_page_content: str,
        source_page_links: list[str],
    ) -> List[Dict[str, Any]]:
        resolved = []
        for opportunity in opportunities:
            resolved.append(
                self.resolve_submission_info(
                    opportunity,
                    source_url=source_url,
                    source_page_content=source_page_content,
                    source_page_links=source_page_links,
                )
            )
        return resolved

    def _parse_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                    return data if isinstance(data, dict) else None
                except json.JSONDecodeError:
                    pass
        return None
