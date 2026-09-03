"""Postmark template bodies stay aligned with event aliases and Mustache variables."""

from __future__ import annotations

import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.email.constants import DEFAULT_POSTMARK_TEMPLATES, TEMPLATE_VARIABLE_KEYS
from app.email.enums import EmailEventType
from app.email.postmark_templates import ALIASES, ALIAS_BY_EVENT, TEMPLATES, template_bodies

MUSTACHE_RE = re.compile(r"\{\{\s*([#^/!]?)([^}]+?)\s*\}\}")


class _HtmlWellFormed(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.errors: list[str] = []

    def error(self, message: str) -> None:  # pragma: no cover - HTMLParser hook
        self.errors.append(message)


def _mustache_names(source: str) -> set[str]:
    names: set[str] = set()
    for kind, raw in MUSTACHE_RE.findall(source):
        if kind == "!":
            continue
        name = raw.strip().split()[0]
        if not name or name.startswith("/"):
            continue
        if kind == "/":
            continue
        names.add(name)
    return names


def _top_level_keys(names: set[str]) -> set[str]:
    return {name.split(".", 1)[0] for name in names}


class TestPostmarkTemplates(unittest.TestCase):
    def test_alias_coverage_matches_event_registry(self):
        self.assertEqual(tuple(ALIAS_BY_EVENT[event] for event in EmailEventType), ALIASES)
        self.assertEqual(set(TEMPLATES), set(ALIASES))
        for event, (_template_id, alias) in DEFAULT_POSTMARK_TEMPLATES.items():
            self.assertEqual(ALIAS_BY_EVENT[event], alias)
            self.assertIn(alias, TEMPLATES)

    def test_template_bodies_include_postmark_fields(self):
        for alias in ALIASES:
            body = template_bodies(alias)
            self.assertEqual(body["Alias"], alias)
            self.assertEqual(body["TemplateType"], "Standard")
            self.assertTrue(body["Name"])
            self.assertTrue(body["Subject"])
            self.assertIn("<!DOCTYPE html>", body["HtmlBody"])
            self.assertIn("SpeakerPitcher", body["HtmlBody"])
            self.assertIn("{{user_name}}", body["HtmlBody"])
            self.assertTrue(body["TextBody"].strip())

    def test_required_variables_are_present(self):
        for event, keys in TEMPLATE_VARIABLE_KEYS.items():
            alias = ALIAS_BY_EVENT[event]
            spec = TEMPLATES[alias]
            blob = "\n".join((spec["Subject"], spec["HtmlBody"], spec["TextBody"]))
            names = _mustache_names(blob)
            top = _top_level_keys(names)
            missing = [key for key in keys if key not in top]
            self.assertFalse(
                missing,
                f"{alias} missing Mustache keys {missing}; found {sorted(top)}",
            )

    def test_html_is_well_formed(self):
        for alias in ALIASES:
            parser = _HtmlWellFormed()
            parser.feed(TEMPLATES[alias]["HtmlBody"])
            parser.close()
            self.assertFalse(parser.errors, f"{alias} HTML parse errors: {parser.errors}")

    def test_opportunity_templates_iterate_list(self):
        new_opp = TEMPLATES["New_opportunity"]["HtmlBody"]
        self.assertIn("{{#opportunities}}", new_opp)
        self.assertIn("{{opportunity_url}}", new_opp)
        deadline = TEMPLATES["Deadline_approaching"]["HtmlBody"]
        self.assertIn("{{#opportunities}}", deadline)
        self.assertIn("{{^opportunities}}", deadline)
        self.assertIn("{{event_name}}", deadline)

    def test_billing_uses_nested_invoice_pdf_url(self):
        html = TEMPLATES["Billing_questions"]["HtmlBody"]
        self.assertIn("{{invoice_pdf_url.invoice_pdf_url}}", html)
        self.assertIn("{{#invoice_pdf_url.invoice_pdf_url}}", html)

    def test_welcome_hides_preheader_from_body_copy(self):
        html = TEMPLATES["welcome_mail"]["HtmlBody"]
        self.assertIn("{{preheader}}", html)
        self.assertIn("display:none", html)
        visible = html.split("<td", 2)[-1]
        # Preheader is inbox preview only; it must not be the visible greeting copy.
        self.assertNotRegex(
            visible,
            r">\s*\{\{preheader\}\}\s*<",
        )


if __name__ == "__main__":
    unittest.main()
