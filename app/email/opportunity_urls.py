"""Shared opportunity → public URL helpers for email templates."""

from __future__ import annotations

import os
from typing import Any, Mapping


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
