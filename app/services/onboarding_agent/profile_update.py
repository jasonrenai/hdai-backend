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
    merged = dict(pending or {})
    for k in ("full_name", "professional_title", "company", "email", "phone_number"):
        v = updates.get(k)
        if isinstance(v, str) and v.strip():
            merged[k] = v.strip()
    return merged
