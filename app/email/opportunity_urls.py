"""Shared opportunity → public URL helpers for email templates."""

from __future__ import annotations

import os
from typing import Any, Mapping


def opportunity_action_url(opportunity: Mapping[str, Any]) -> str:
    """Prefer external link; else API detail URL when API_BASE_URL and id are set."""
    oid = str(opportunity.get("_id") or "").strip()
    external = (opportunity.get("link") or opportunity.get("url") or "").strip()
    if external:
        return external
    base = (os.getenv("API_BASE_URL") or "").rstrip("/")
    return f"{base}/api/v1/opportunities/{oid}" if base and oid else ""
