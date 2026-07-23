"""Resolve speaking opportunity submission details with LLM tool calls."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from openai import OpenAI
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.helpers.RapidAPIScraper import RapidAPIScraper

logger = logging.getLogger(__name__)

DEADLINE_NOT_FOUND = "deadline not found"
MAX_SOURCE_CONTENT_CHARS = 12000
MAX_TOOL_CONTENT_CHARS = 10000
MAX_DISCOVERED_LINKS = 100
MAX_TOOL_CALL_ROUNDS = 6
APPLY_SIGNAL_EXCERPT_CHARS = 2500

_SPEAKER_LINK_HINTS = (
    "for-speakers",
    "for_speakers",
    "forspeakers",
    "speakers",
    "speaker",
    "speak-at",
    "speak_at",
    "call-for-speakers",
    "call_for_speakers",
    "cfp",
    "cfs",
    "apply-to-speak",
    "apply_to_speak",
    "apply-to-be-a-speaker",
    "become-a-speaker",
    "speaker-application",
    "speaker_application",
    "speaker-survey",
    "speaker_survey",
    "submit-a-talk",
    "submit_a_talk",
    "submit-talk",
    "proposal",
    "survey",
)

_EXTERNAL_FORM_HOSTS = (
    "typeform.com",
    "forms.gle",
    "docs.google.com/forms",
    "airtable.com",
    "jotform.com",
    "forms.office.com",
)

_APPLY_SIGNAL_PHRASES = (
    "apply to speak",
    "apply to be a speaker",
    "become a speaker",
    "call for speakers",
    "speaker application",
    "submit a talk",
    "submit a proposal",
    "speaker survey",
)


SUBMISSION_RESOLVER_SYSTEM_PROMPT = """You resolve speaker application/submission details for speaking opportunities.

Use only evidence from the provided scraped page content, discovered links, and pages you scrape through tools.

Return ONLY valid JSON with exactly these keys:
- status: one of "found", "contact_found", "not_found"
- applicationLink: direct speaker application / call-for-speakers / apply page URL, or ""
- formLink: direct form URL if a form is present or linked (including third-party forms such as Typeform, Google Forms, Jotform, Airtable, Microsoft Forms), or ""
- submissionEmail: valid email address only, specifically for speaker submissions, proposals, CFPs, or applications, or ""
- deadline: ISO date YYYY-MM-DD when explicitly found; otherwise exactly "deadline not found"
- contactEmail: valid email address only, general organizer/contact email only when submission data cannot be found, otherwise ""
- reason: short evidence-based reason
- sourceUrl: when an application/form URL is found, set this to that same submission URL; otherwise the page URL where the strongest evidence was found, or ""
- submissionVerified: true only when you scraped an application/form URL and confirmed it shows a real submit path (form, apply CTA, or submission email); otherwise false

Workflow:
1. First inspect the provided page content and current submissionInfo for apply/CFP/form links or submission emails. Treat Typeform, Google Forms, Jotform, Airtable, Microsoft Forms, and similar survey/form hosts linked from "apply to speak" / speaker CTA context as valid formLink values.
2. If an application/form URL exists, call scrape_url on it and confirm it is a real submission surface. Extract the deadline from THAT page when explicitly stated; otherwise keep deadline as "deadline not found". Set sourceUrl to that application/form URL.
3. If no application data exists, prioritize discoveredLinks whose URL path, host, or surrounding context suggests speaker submission: "for speakers", "speakers", "speaker", "speak at", "call for speakers", "cfp", "apply to speak", "speaker application", "submit a talk", "survey", "speaker-survey", plus third-party form hosts (typeform.com, forms.gle, docs.google.com/forms, jotform, airtable, forms.office.com). Call scrape_url on the best candidates.
4. Contact pages/emails are ONLY a fallback when no real application URL, form URL, or submission email exists. Return status "contact_found" with contactEmail and reason "Submission details not found; contact email found." only in that case. Never use a contact page as sourceUrl when a form/application URL is available.
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
        "submissionVerified": bool(
            data.get("submissionVerified") is True
            or (
                isinstance(data.get("submissionVerified"), str)
                and data.get("submissionVerified").strip().lower() in ("true", "yes", "1")
            )
        ),
    }

    has_submission_path = bool(info["applicationLink"] or info["formLink"] or info["submissionEmail"])
    if has_submission_path:
        info["status"] = "found"
        submission_url = info["formLink"] or info["applicationLink"]
        if submission_url:
            info["sourceUrl"] = submission_url
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
    if info.get("submissionVerified"):
        meta["submission_verified"] = True
    opportunity["metadata"] = meta


def _is_external_form_host(url: str) -> bool:
    low = (url or "").lower()
    if not low:
        return False
    try:
        parsed = urlparse(low)
        host_path = f"{parsed.netloc}{parsed.path}"
    except Exception:
        host_path = low
    return any(host in host_path for host in _EXTERNAL_FORM_HOSTS)


def _speaker_hint_score(url: str) -> int:
    low = (url or "").lower()
    return sum(1 for hint in _SPEAKER_LINK_HINTS if hint in low)


def _score_submission_link(url: str) -> int:
    """Higher score = stronger speaker-submission / form candidate."""
    low = (url or "").lower()
    if not low:
        return 0
    score = _speaker_hint_score(low)
    if _is_external_form_host(low):
        score += 8
        if score > 8:
            score += 6  # form host + speaker/survey/apply signal
    return score


def _best_external_submission_form(links: list[str]) -> str:
    """
    Pick the strongest third-party form URL that also looks speaker-related.
    Prefer form hosts with speaker/survey/apply hints; allow high-scoring form hosts alone.
    """
    best_url = ""
    best_score = 0
    for url in links or []:
        if not _is_external_form_host(url):
            continue
        score = _score_submission_link(url)
        # Require either speaker-ish hints beyond bare host, or a survey/speaker path fragment
        low = (url or "").lower()
        speakerish = _speaker_hint_score(low) > 0 or any(
            token in low for token in ("speaker", "survey", "cfp", "apply", "talk", "proposal")
        )
        if not speakerish and score < 10:
            continue
        if score > best_score:
            best_score = score
            best_url = url
    return best_url


def _promote_submission_form_if_missing(
    info: Dict[str, Any],
    links: list[str],
) -> Dict[str, Any]:
    """If LLM missed a high-confidence form link, promote it into submissionInfo."""
    if submission_info_has_submission_path(info):
        return info
    form_url = _best_external_submission_form(links)
    if not form_url:
        return info
    info = dict(info)
    info["formLink"] = form_url
    info["status"] = "found"
    info["sourceUrl"] = form_url
    info["reason"] = "Promoted third-party speaker form URL from discovered links."
    info["contactEmail"] = ""
    return normalize_submission_info(info, base_url=form_url)


def _apply_signal_excerpt(content: str, max_chars: int = APPLY_SIGNAL_EXCERPT_CHARS) -> str:
    """Return a window around the first apply-to-speak style phrase, if any."""
    text = content or ""
    if not text.strip():
        return ""
    low = text.lower()
    best_idx = -1
    for phrase in _APPLY_SIGNAL_PHRASES:
        idx = low.find(phrase)
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx = idx
    if best_idx < 0:
        return ""
    half = max_chars // 2
    start = max(0, best_idx - half)
    end = min(len(text), best_idx + half)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."
    return excerpt


def _prepare_source_page_content(content: str, max_chars: int = MAX_SOURCE_CONTENT_CHARS) -> str:
    """Truncate long pages but keep an apply-signal excerpt when the CTA would be cut off."""
    text = content or ""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    excerpt = _apply_signal_excerpt(text)
    if not excerpt:
        return head
    # If the phrase already appears in the head, no need to append
    low_head = head.lower()
    if any(p in low_head for p in _APPLY_SIGNAL_PHRASES):
        return head
    marker = "\n\n--- apply signal excerpt ---\n"
    budget = max_chars - len(marker)
    if budget < 500:
        return head
    # Keep most of the head, append excerpt within budget
    keep_head = budget - min(len(excerpt), APPLY_SIGNAL_EXCERPT_CHARS)
    if keep_head < 500:
        keep_head = budget // 2
    combined = text[:keep_head].rstrip() + marker + excerpt[: APPLY_SIGNAL_EXCERPT_CHARS]
    return combined[:max_chars]


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
        return self._prioritize_speaker_links(normalized)

    @staticmethod
    def _prioritize_speaker_links(links: list[str]) -> list[str]:
        """Surface Speakers / CFP / third-party form URLs first for the resolver LLM."""
        return sorted(links, key=_score_submission_link, reverse=True)

    def _build_user_prompt(
        self,
        opportunity: Dict[str, Any],
        source_url: str,
        source_page_content: str,
        source_page_links: list[str],
    ) -> str:
        normalized_links = source_page_links[:MAX_DISCOVERED_LINKS]
        high_confidence = [u for u in normalized_links if _score_submission_link(u) >= 8][:15]
        opp_payload = {
            "event_name": opportunity.get("event_name") or opportunity.get("title") or "",
            "link": opportunity.get("link") or opportunity.get("url") or "",
            "metadata": opportunity.get("metadata") if isinstance(opportunity.get("metadata"), dict) else {},
            "submissionInfo": opportunity.get("submissionInfo") if isinstance(opportunity.get("submissionInfo"), dict) else {},
        }
        payload = {
            "sourceUrl": source_url,
            "opportunity": opp_payload,
            "discoveredLinks": normalized_links,
            "highConfidenceSubmissionCandidates": high_confidence,
            "sourcePageContent": _prepare_source_page_content(source_page_content),
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
        normalized_links = self._normalize_links(source_page_links, source_url)
        # Deterministic promote before LLM when path is missing
        opportunity["submissionInfo"] = _promote_submission_form_if_missing(
            opportunity["submissionInfo"],
            normalized_links,
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
            opportunity["submissionInfo"] = _promote_submission_form_if_missing(
                opportunity["submissionInfo"],
                normalized_links,
            )
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
                        normalized_links,
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

        opportunity["submissionInfo"] = _promote_submission_form_if_missing(
            opportunity.get("submissionInfo") or {},
            normalized_links,
        )
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
