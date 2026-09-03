"""Unit tests for Postmark server selection used by the template sync script."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SYNC_PATH = ROOT / "scripts" / "sync_postmark_templates.py"
_SPEC = importlib.util.spec_from_file_location("sync_postmark_templates", _SYNC_PATH)
assert _SPEC and _SPEC.loader
_SYNC = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SYNC)
SyncError = _SYNC.SyncError
pick_server = _SYNC.pick_server
server_api_token = _SYNC.server_api_token


def _server(server_id: int, name: str, tokens: list[str] | None = None) -> dict:
    return {"ID": server_id, "Name": name, "ApiTokens": tokens if tokens is not None else [f"tok-{server_id}"]}


class TestPickPostmarkServer(unittest.TestCase):
    def test_single_server(self):
        row = pick_server([_server(7, "Nexus")])
        self.assertEqual(row["ID"], 7)

    def test_pick_by_id(self):
        row = pick_server(
            [_server(1, "Prod"), _server(2, "Sandbox")],
            server_id="2",
        )
        self.assertEqual(row["Name"], "Sandbox")

    def test_pick_by_name_substring(self):
        row = pick_server(
            [_server(1, "SpeakerPitcher Prod"), _server(2, "Sandbox")],
            server_name="speakerpitcher",
        )
        self.assertEqual(row["ID"], 1)

    def test_multiple_without_filter_errors(self):
        with self.assertRaises(SyncError) as ctx:
            pick_server([_server(1, "Prod"), _server(2, "Sandbox")])
        self.assertIn("POSTMARK_SERVER_NAME", str(ctx.exception))

    def test_missing_id_errors(self):
        with self.assertRaises(SyncError):
            pick_server([_server(1, "Prod")], server_id="99")

    def test_empty_servers_error(self):
        with self.assertRaises(SyncError):
            pick_server([])

    def test_server_api_token(self):
        self.assertEqual(server_api_token(_server(1, "Nexus", ["abc"])), "abc")

    def test_server_api_token_missing(self):
        with self.assertRaises(SyncError):
            server_api_token(_server(1, "Nexus", []))


if __name__ == "__main__":
    unittest.main()
