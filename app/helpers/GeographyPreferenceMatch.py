"""
Match speaker geography_preferences against opportunity delivery (virtual vs in-person).

Canonical values stored on speaker_profiles.geography_preferences:
- International – Virtual only
- International – In-Person only
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

GEOGRAPHY_PREF_INTERNATIONAL_VIRTUAL_ONLY = "International – Virtual only"
GEOGRAPHY_PREF_INTERNATIONAL_IN_PERSON_ONLY = "International – In-Person only"

_KIND_VIRTUAL = "virtual"
_KIND_IN_PERSON = "in_person"
_KIND_HYBRID = "hybrid"

_VIRTUAL_LOCATION_TOKENS = {"virtual", "online", "remote", "webinar"}
_IN_PERSON_MODE_KEYS = {"in-person", "in person", "inperson"}
_VIRTUAL_MODE_KEYS = {"virtual"}
_HYBRID_MODE_KEYS = {"hybrid"}


def _norm_pref(value: str) -> str:
    s = (value or "").strip().lower()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    return s


_VIRTUAL_ONLY_KEYS = {
    _norm_pref(GEOGRAPHY_PREF_INTERNATIONAL_VIRTUAL_ONLY),
    "international - virtual only",
    "international virtual only",
}
_IN_PERSON_ONLY_KEYS = {
    _norm_pref(GEOGRAPHY_PREF_INTERNATIONAL_IN_PERSON_ONLY),
    "international - in-person only",
    "international - in person only",
    "international in-person only",
    "international in person only",
}


def geography_prefs_from_profile(profile: dict | None) -> list[str]:
    raw = (profile or {}).get("geography_preferences")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip() if item is not None else ""
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def international_delivery_filter(
    geography_preferences: Sequence[str],
) -> str | None:
    """
    Return 'virtual', 'in_person', or None (no extra geography delivery filter).

    Virtual only → drop in-person. In-person only → drop virtual. Both → no filter.
    """
    keys = {_norm_pref(p) for p in geography_preferences if p}
    want_virtual = bool(keys & _VIRTUAL_ONLY_KEYS)
    want_in_person = bool(keys & _IN_PERSON_ONLY_KEYS)
    if want_virtual and not want_in_person:
        return _KIND_VIRTUAL
    if want_in_person and not want_virtual:
        return _KIND_IN_PERSON
    return None


def _is_virtual_location(location: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", (location or "").lower()))
    return bool(tokens & _VIRTUAL_LOCATION_TOKENS)


def _mode_key(delivery_mode: str) -> str:
    return re.sub(r"\s+", " ", (delivery_mode or "").strip().lower())


def opportunity_delivery_kind(opportunity: dict[str, Any] | None) -> str | None:
    """Classify an opportunity as virtual, in_person, hybrid, or unknown (None)."""
    opp = opportunity or {}
    mode = _mode_key(str(opp.get("delivery_mode") or ""))
    location = str(opp.get("location") or "")

    if mode in _HYBRID_MODE_KEYS:
        return _KIND_HYBRID
    if mode in _VIRTUAL_MODE_KEYS:
        return _KIND_VIRTUAL
    if mode in _IN_PERSON_MODE_KEYS:
        return _KIND_IN_PERSON
    if _is_virtual_location(location):
        return _KIND_VIRTUAL
    if location.strip():
        return _KIND_IN_PERSON
    return None


def speaker_is_virtual_delivery_only(delivery_modes: Sequence[str]) -> bool:
    kinds: set[str] = set()
    for raw in delivery_modes:
        key = _mode_key(str(raw))
        if key in _VIRTUAL_MODE_KEYS:
            kinds.add(_KIND_VIRTUAL)
        elif key in _IN_PERSON_MODE_KEYS:
            kinds.add(_KIND_IN_PERSON)
        elif key in _HYBRID_MODE_KEYS:
            kinds.add(_KIND_HYBRID)
    return kinds == {_KIND_VIRTUAL}


def delivery_mode_query_values(delivery_modes: Sequence[str]) -> list[str]:
    """Case / hyphen variants so Mongo $in matches In-person and In-Person."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    for raw in delivery_modes:
        s = str(raw).strip()
        if not s:
            continue
        _add(s)
        key = _mode_key(s)
        if key in _VIRTUAL_MODE_KEYS:
            _add("Virtual")
            _add("virtual")
        elif key in _HYBRID_MODE_KEYS:
            _add("Hybrid")
            _add("hybrid")
        elif key in _IN_PERSON_MODE_KEYS:
            _add("In-person")
            _add("In-Person")
            _add("in-person")
            _add("in person")
            _add("In Person")
    return out


def opportunity_allowed_for_speaker(
    opportunity: dict[str, Any],
    *,
    geography_preferences: Sequence[str],
    delivery_modes: Sequence[str],
) -> bool:
    kind = opportunity_delivery_kind(opportunity)
    if kind == _KIND_IN_PERSON and speaker_is_virtual_delivery_only(delivery_modes):
        return False

    wanted = international_delivery_filter(geography_preferences)
    if wanted is None or kind is None or kind == _KIND_HYBRID:
        return True
    if wanted == _KIND_VIRTUAL:
        return kind != _KIND_IN_PERSON
    return kind != _KIND_VIRTUAL


def filter_opportunities_by_geography(
    opportunities: Iterable[dict[str, Any]],
    *,
    geography_preferences: Sequence[str],
    delivery_modes: Sequence[str],
) -> list[dict[str, Any]]:
    return [
        opp
        for opp in opportunities
        if opportunity_allowed_for_speaker(
            opp,
            geography_preferences=geography_preferences,
            delivery_modes=delivery_modes,
        )
    ]
