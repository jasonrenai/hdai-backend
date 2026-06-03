"""Shared opportunity → public URL helpers for email templates."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import urlencode

from app.email.constants import PITCH_REVIEW_FRONTEND_BASE


def opportunity_app_url(speaker_profile_id: str, opportunity_id: str) -> str:
    """Frontend deep link to opportunities page for a speaker + opportunity."""
    speaker_id = str(speaker_profile_id or "").strip()
    opp_id = str(opportunity_id or "").strip()
    if not speaker_id or not opp_id:
        return ""
    query = urlencode(
        {
            "speakerProfileId": speaker_id,
            "opportunity_idFromEmail": opp_id,
        }
    )
    return f"{PITCH_REVIEW_FRONTEND_BASE}/opportunities?{query}"


def opportunity_action_url(opportunity: Mapping[str, Any]) -> str:
    """Prefer submission link/form; else external link; else API detail URL when available."""
    oid = str(opportunity.get("_id") or "").strip()
    submission_info = opportunity.get("submissionInfo")
    if isinstance(submission_info, Mapping):
        for key in ("formLink", "applicationLink"):
            value = (submission_info.get(key) or "").strip()
            if value:
                return value
        email = (submission_info.get("submissionEmail") or "").strip()
        if email:
            return f"mailto:{email}"
    external = (opportunity.get("link") or opportunity.get("url") or "").strip()
    if external:
        return external
    base = (os.getenv("API_BASE_URL") or "").rstrip("/")
    return f"{base}/api/v1/opportunities/{oid}" if base and oid else ""
