"""UpdateSpeakerProfile — merge updates with conflict detection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Scalar fields where overwrite of a different non-empty value needs confirmation.
_CONFLICT_FIELDS: Set[str] = {
    "full_name",
    "professional_title",
    "company",
    "email",
    "phone_number",
    "address_city",
    "address_state",
    "address_country",
    "bio",
    "linkedin_url",
}


@dataclass
class ProfileUpdatePlan:
    updates: Dict[str, Any] = field(default_factory=dict)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    needs_confirmation: bool = False
    pending_confirmation: Optional[Dict[str, Any]] = None


def _values_differ(old: Any, new: Any) -> bool:
    if old is None or old == "" or old == []:
        return False
    if isinstance(old, str) and isinstance(new, str):
        return old.strip().lower() != new.strip().lower()
    return old != new


def plan_profile_update(
    *,
    profile: Optional[dict],
    updates: Dict[str, Any],
    intent: str = "ANSWER",
    pending_confirmation: Optional[Dict[str, Any]] = None,
    user_confirmed: bool = False,
) -> ProfileUpdatePlan:
    """
    Merge strategy:
    - Empty → fill freely
    - CHANGE_PREVIOUS or user_confirmed → overwrite
    - Conflict on scalar → set pending_confirmation, withhold that field
    """
    updates = dict(updates or {})
    profile = profile or {}

    # Resolve pending confirmation
    if pending_confirmation and user_confirmed:
        field_name = pending_confirmation.get("field")
        new_val = pending_confirmation.get("newValue")
        if field_name and new_val is not None:
            updates[field_name] = new_val

    if not updates:
        return ProfileUpdatePlan()

    apply: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []

    for k, v in updates.items():
        if v is None or v == "" or v == []:
            continue
        # Email/phone locked after set
        if k == "email" and (profile.get("email") or "").strip():
            continue
        if k == "phone_number" and (profile.get("phone_number") or "").strip():
            continue

        old = profile.get(k)
        if (
            intent != "CHANGE_PREVIOUS"
            and not user_confirmed
            and k in _CONFLICT_FIELDS
            and _values_differ(old, v)
        ):
            conflicts.append({"field": k, "oldValue": old, "newValue": v})
            continue
        apply[k] = v

    pending = None
    needs = False
    if conflicts:
        # Confirm one conflict at a time
        pending = conflicts[0]
        needs = True

    return ProfileUpdatePlan(
        updates=apply,
        conflicts=conflicts,
        needs_confirmation=needs,
        pending_confirmation=pending,
    )


def merge_pending_identity(
    pending: Optional[Dict[str, Any]],
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge identity/contact fields into pending.
    Never overwrite an existing full_name with a value that does not look like a person name
    (e.g. title/company follow-ups like "CEO, DCL").
    """
    from app.services.speaker_profile_chatbot_steps import looks_like_person_name

    merged = dict(pending or {})
    existing_name = str(merged.get("full_name") or "").strip()
    for k in ("full_name", "professional_title", "company", "email", "phone_number"):
        v = updates.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        val = v.strip()
        if k == "full_name" and existing_name:
            # Protect known name from title/company dumps or same-as-title overwrites
            if not looks_like_person_name(val):
                continue
            title = str(updates.get("professional_title") or merged.get("professional_title") or "").strip()
            company = str(updates.get("company") or merged.get("company") or "").strip()
            if title and company and val.lower() in {
                f"{title}, {company}".lower(),
                f"{title} {company}".lower(),
                title.lower(),
            }:
                continue
            if val.lower() == existing_name.lower():
                continue
        merged[k] = val
        if k == "full_name":
            existing_name = val
    return merged
