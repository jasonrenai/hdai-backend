"""Unit tests for geography preference matching (stdlib unittest; no pytest required)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.helpers.GeographyPreferenceMatch import (
    GEOGRAPHY_PREF_ANYWHERE_IN_USA,
    GEOGRAPHY_PREF_INTERNATIONAL_IN_PERSON_ONLY,
    GEOGRAPHY_PREF_INTERNATIONAL_VIRTUAL_ONLY,
    GEOGRAPHY_PREF_NORTHEAST,
    GEOGRAPHY_PREF_SOUTHEAST,
    filter_opportunities_by_geography,
    opportunity_allowed_for_speaker,
    opportunity_is_us_location,
    opportunity_location_scope,
)


JENNIFER_LIKE_PREFS = [
    GEOGRAPHY_PREF_NORTHEAST,
    GEOGRAPHY_PREF_SOUTHEAST,
    GEOGRAPHY_PREF_ANYWHERE_IN_USA,
    GEOGRAPHY_PREF_INTERNATIONAL_VIRTUAL_ONLY,
]
JENNIFER_LIKE_DELIVERY = ["Hybrid", "In-person", "Virtual"]


class TestGeographyPreferenceMatch(unittest.TestCase):
    def test_jennifer_nyc_in_person_allowed(self):
        opp = {
            "event_name": "The AI Summit New York",
            "location": "Javits Center, New York",
            "delivery_mode": "In-person",
        }
        self.assertTrue(
            opportunity_allowed_for_speaker(
                opp,
                geography_preferences=JENNIFER_LIKE_PREFS,
                delivery_modes=JENNIFER_LIKE_DELIVERY,
            )
        )

    def test_jennifer_london_in_person_denied_without_intl_in_person(self):
        opp = {
            "event_name": "The AI Summit London",
            "location": "London, UK",
            "delivery_mode": "In-person",
        }
        self.assertFalse(
            opportunity_allowed_for_speaker(
                opp,
                geography_preferences=JENNIFER_LIKE_PREFS,
                delivery_modes=JENNIFER_LIKE_DELIVERY,
            )
        )

    def test_intl_in_person_pref_allows_london(self):
        opp = {
            "event_name": "The AI Summit London",
            "location": "London, UK",
            "delivery_mode": "In-person",
        }
        self.assertTrue(
            opportunity_allowed_for_speaker(
                opp,
                geography_preferences=[GEOGRAPHY_PREF_INTERNATIONAL_IN_PERSON_ONLY],
                delivery_modes=JENNIFER_LIKE_DELIVERY,
            )
        )

    def test_virtual_and_hybrid_always_pass_geo_gate(self):
        prefs = [GEOGRAPHY_PREF_NORTHEAST]  # no international prefs
        for mode, loc in (("Virtual", "London, UK"), ("Hybrid", "Paris, France")):
            opp = {"location": loc, "delivery_mode": mode}
            self.assertTrue(
                opportunity_allowed_for_speaker(
                    opp,
                    geography_preferences=prefs,
                    delivery_modes=JENNIFER_LIKE_DELIVERY,
                ),
                msg=f"expected allow for {mode} @ {loc}",
            )

    def test_virtual_only_delivery_drops_in_person(self):
        opp = {
            "location": "New York, NY",
            "delivery_mode": "In-person",
        }
        self.assertFalse(
            opportunity_allowed_for_speaker(
                opp,
                geography_preferences=JENNIFER_LIKE_PREFS,
                delivery_modes=["Virtual"],
            )
        )

    def test_us_in_person_denied_without_us_prefs(self):
        opp = {
            "location": "NYC",
            "delivery_mode": "In-person",
        }
        self.assertFalse(
            opportunity_allowed_for_speaker(
                opp,
                geography_preferences=[GEOGRAPHY_PREF_INTERNATIONAL_VIRTUAL_ONLY],
                delivery_modes=JENNIFER_LIKE_DELIVERY,
            )
        )

    def test_unknown_location_in_person_allowed(self):
        opp = {"location": "", "delivery_mode": "In-person"}
        self.assertTrue(
            opportunity_allowed_for_speaker(
                opp,
                geography_preferences=[],
                delivery_modes=JENNIFER_LIKE_DELIVERY,
            )
        )

    def test_location_scope_helpers(self):
        self.assertEqual(opportunity_location_scope({"location": "NYC"}), "us")
        self.assertEqual(opportunity_location_scope({"location": "London, UK"}), "international")
        self.assertEqual(opportunity_location_scope({"location": ""}), "unknown")
        self.assertTrue(opportunity_is_us_location("Boston, MA"))
        self.assertFalse(opportunity_is_us_location("Amsterdam"))
        self.assertIsNone(opportunity_is_us_location(""))

    def test_filter_opportunities_by_geography(self):
        opps = [
            {"_id": "1", "location": "New York", "delivery_mode": "In-person"},
            {"_id": "2", "location": "London, UK", "delivery_mode": "In-person"},
            {"_id": "3", "location": "London, UK", "delivery_mode": "Virtual"},
        ]
        kept = filter_opportunities_by_geography(
            opps,
            geography_preferences=JENNIFER_LIKE_PREFS,
            delivery_modes=JENNIFER_LIKE_DELIVERY,
        )
        self.assertEqual([o["_id"] for o in kept], ["1", "3"])


if __name__ == "__main__":
    unittest.main()
