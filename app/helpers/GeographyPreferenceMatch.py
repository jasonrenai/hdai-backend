"""
Match speaker geography_preferences against opportunity location for in-person events.

Geography prefs apply only to strictly in-person opportunities:
- Virtual / Hybrid always pass this gate (delivery_mode Mongo filter is separate).
- In-person + US location: speaker needs any US region pref
  (Northeast, Southeast, Midwest, West Coast, Anywhere in USA).
- In-person + international: speaker needs International – In-Person only.
- International – Virtual only is stored for UI/compat but is not a geo gate
  (virtual already passes).

Also: if speaker delivery_mode is Virtual-only, drop in-person opportunities.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Literal, Optional, Sequence

GEOGRAPHY_PREF_INTERNATIONAL_VIRTUAL_ONLY = "International – Virtual only"
GEOGRAPHY_PREF_INTERNATIONAL_IN_PERSON_ONLY = "International – In-Person only"

GEOGRAPHY_PREF_NORTHEAST = "Northeast"
GEOGRAPHY_PREF_SOUTHEAST = "Southeast"
GEOGRAPHY_PREF_MIDWEST = "Midwest"
GEOGRAPHY_PREF_WEST_COAST = "West Coast"
GEOGRAPHY_PREF_ANYWHERE_IN_USA = "Anywhere in USA"

_KIND_VIRTUAL = "virtual"
_KIND_IN_PERSON = "in_person"
_KIND_HYBRID = "hybrid"

LocationScope = Literal["us", "international", "unknown"]

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

_US_REGION_PREF_KEYS = {
    _norm_pref(GEOGRAPHY_PREF_NORTHEAST),
    "north east",
    _norm_pref(GEOGRAPHY_PREF_SOUTHEAST),
    "south east",
    _norm_pref(GEOGRAPHY_PREF_MIDWEST),
    _norm_pref(GEOGRAPHY_PREF_WEST_COAST),
    "westcoast",
    "west-coast",
    _norm_pref(GEOGRAPHY_PREF_ANYWHERE_IN_USA),
    "anywhere in the usa",
    "anywhere usa",
    "usa",
}

# Common US location signals (token / phrase match on lowercased location text).
_US_COUNTRY_PHRASES = (
    "united states",
    "united states of america",
    "u.s.a",
    "u.s.",
    "usa",
)
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}
_US_STATE_ABBREVS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
_US_CITY_PHRASES = {
    "nyc", "new york city", "los angeles", "san francisco", "san diego",
    "chicago", "boston", "seattle", "miami", "atlanta", "dallas", "houston",
    "austin", "denver", "phoenix", "las vegas", "washington dc",
    "washington, dc", "javits", "javits center",
}

# Strong non-US signals (country / major city). Checked before US when both could appear.
_INTERNATIONAL_PHRASES = {
    "london", "uk", "u.k.", "united kingdom", "england", "scotland", "wales",
    "canada", "toronto", "vancouver", "montreal",
    "australia", "sydney", "melbourne",
    "germany", "berlin", "munich", "frankfurt",
    "france", "paris", "lyon",
    "netherlands", "amsterdam", "rotterdam",
    "spain", "madrid", "barcelona",
    "italy", "rome", "milan",
    "india", "mumbai", "bangalore", "bengaluru", "delhi", "hyderabad",
    "singapore", "hong kong", "tokyo", "japan", "seoul", "korea",
    "dubai", "uae", "abu dhabi", "israel", "tel aviv",
    "brazil", "sao paulo", "são paulo", "mexico city", "mexico",
    "ireland", "dublin", "switzerland", "zurich", "sweden", "stockholm",
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


def speaker_has_us_region_pref(geography_preferences: Sequence[str]) -> bool:
    keys = {_norm_pref(p) for p in geography_preferences if p}
    return bool(keys & _US_REGION_PREF_KEYS)


def speaker_has_international_in_person_pref(geography_preferences: Sequence[str]) -> bool:
    keys = {_norm_pref(p) for p in geography_preferences if p}
    return bool(keys & _IN_PERSON_ONLY_KEYS)


def speaker_has_international_virtual_pref(geography_preferences: Sequence[str]) -> bool:
    """Recognized for profile storage/compat; not used as a geo gate."""
    keys = {_norm_pref(p) for p in geography_preferences if p}
    return bool(keys & _VIRTUAL_ONLY_KEYS)


def international_delivery_filter(
    geography_preferences: Sequence[str],
) -> str | None:
    """
    Deprecated compatibility shim.

    Old behavior treated International Virtual/In-Person as a global delivery filter.
    New matching ignores this; always returns None.
    """
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


def _location_blob(opportunity: dict[str, Any] | None) -> str:
    opp = opportunity or {}
    parts = [
        str(opp.get("location") or ""),
        str(opp.get("event_name") or ""),
        str(opp.get("title") or ""),
    ]
    return " ".join(p for p in parts if p).strip().lower()


def opportunity_location_scope(opportunity: dict[str, Any] | None) -> LocationScope:
    """
    Classify opportunity location as us, international, or unknown.
    Empty / ambiguous → unknown (caller should allow to avoid false denials).
    """
    blob = _location_blob(opportunity)
    if not blob:
        return "unknown"

    # Prefer explicit international phrases when present (e.g. "London, UK").
    for phrase in _INTERNATIONAL_PHRASES:
        if phrase in blob:
            # Avoid treating "Georgia" (US state) as country when "atlanta"/US context;
            # international set uses country names that are unambiguous enough here.
            return "international"

    for phrase in _US_COUNTRY_PHRASES:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", blob):
            return "us"

    for phrase in _US_CITY_PHRASES:
        if phrase in blob:
            return "us"

    for name in _US_STATE_NAMES:
        if name in blob:
            return "us"

    # State abbrevs as standalone word tokens (avoid matching inside longer words)
    word_tokens = set(re.findall(r"\b[a-z]{2}\b", blob))
    if word_tokens & _US_STATE_ABBREVS:
        return "us"

    return "unknown"


def opportunity_is_us_location(location: str) -> Optional[bool]:
    """
    Heuristic on a location string alone.
    True = US, False = international, None = unknown/empty.
    """
    scope = opportunity_location_scope({"location": location or ""})
    if scope == "us":
        return True
    if scope == "international":
        return False
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

    # Virtual / Hybrid / unknown delivery: geography prefs do not apply.
    if kind != _KIND_IN_PERSON:
        return True

    if speaker_is_virtual_delivery_only(delivery_modes):
        return False

    scope = opportunity_location_scope(opportunity)
    if scope == "unknown":
        return True
    if scope == "us":
        return speaker_has_us_region_pref(geography_preferences)
    # international
    return speaker_has_international_in_person_pref(geography_preferences)


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
