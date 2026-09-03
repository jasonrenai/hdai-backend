"""Create or update SpeakerPitcher Postmark templates by alias.

Sending and template APIs require a **Server API token**
(``X-Postmark-Server-Token``). An Account API token cannot send mail or
create templates; it can only list servers and return each server's token.

Usage:
  POSTMARK_SERVER_API_TOKEN=... python scripts/sync_postmark_templates.py
  POSTMARK_ACCOUNT_API_TOKEN=... python scripts/sync_postmark_templates.py
  POSTMARK_ACCOUNT_API_TOKEN=... POSTMARK_SERVER_NAME=Nexus python scripts/sync_postmark_templates.py
  python scripts/sync_postmark_templates.py --dry-run
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.email.postmark_templates import ALIASES, TEMPLATES, template_bodies

POSTMARK_API = "https://api.postmarkapp.com"
ACCOUNT_TOKEN_ENV_KEYS = (
    "POSTMARK_ACCOUNT_API_TOKEN",
    "POSTMARK-ACCOUNT-API-TOKEN",
)
SERVER_TOKEN_ENV_KEYS = (
    "POSTMARK_SERVER_API_TOKEN",
    "POSTMARK-SERVER-API-TOKEN",
)


class SyncError(Exception):
    """User-facing sync failure."""


def _env_first(keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return None


def _request(
    method: str,
    path: str,
    *,
    server_token: str | None = None,
    account_token: str | None = None,
    body: dict | None = None,
) -> tuple[int, dict]:
    if bool(server_token) == bool(account_token):
        raise ValueError("Provide exactly one of server_token or account_token.")
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if server_token:
        headers["X-Postmark-Server-Token"] = server_token
    else:
        headers["X-Postmark-Account-Token"] = account_token or ""
    req = urllib.request.Request(
        f"{POSTMARK_API}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"Message": raw}
        return exc.code, payload


def _is_unauthorized(status: int, payload: dict) -> bool:
    if status == 401:
        return True
    message = str(payload.get("Message") or "").lower()
    return "unauthorized" in message or ("invalid" in message and "token" in message)


def list_servers(account_token: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    offset = 0
    while True:
        status, payload = _request(
            "GET",
            f"/servers?count=100&offset={offset}",
            account_token=account_token,
        )
        if status != 200:
            raise SyncError(f"List Postmark servers failed ({status}): {payload}")
        rows = payload.get("Servers") or []
        found.extend(row for row in rows if isinstance(row, dict))
        total = int(payload.get("TotalCount") or 0)
        offset += len(rows)
        if offset >= total or not rows:
            break
    return found


def pick_server(
    servers: list[dict[str, Any]],
    *,
    server_id: str | None = None,
    server_name: str | None = None,
) -> dict[str, Any]:
    if not servers:
        raise SyncError("This Postmark account has no servers. Create a server in Postmark first.")

    if server_id:
        wanted = str(server_id).strip()
        for row in servers:
            if str(row.get("ID") or "") == wanted:
                return row
        raise SyncError(f"No Postmark server with ID {wanted}.")

    if server_name:
        needle = server_name.strip().lower()
        matches = [row for row in servers if needle in str(row.get("Name") or "").lower()]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(f"{row.get('Name')} (id={row.get('ID')})" for row in servers)
        if not matches:
            raise SyncError(f"No Postmark server named {server_name!r}. Available: {names}")
        raise SyncError(
            f"Multiple Postmark servers match {server_name!r}. Set POSTMARK_SERVER_ID. Available: {names}"
        )

    if len(servers) == 1:
        return servers[0]

    names = ", ".join(f"{row.get('Name')} (id={row.get('ID')})" for row in servers)
    raise SyncError(
        "Multiple Postmark servers found. Set POSTMARK_SERVER_NAME or POSTMARK_SERVER_ID. "
        f"Available: {names}"
    )


def server_api_token(server: dict[str, Any]) -> str:
    tokens = [str(t).strip() for t in (server.get("ApiTokens") or []) if str(t).strip()]
    if not tokens:
        name = server.get("Name") or server.get("ID")
        raise SyncError(f"Postmark server {name!r} has no Server API tokens.")
    return tokens[0]


def _account_token_error() -> str:
    return (
        "Postmark account API tokens cannot send mail or manage templates. "
        "Use a Server API token from Postmark → Servers → (your server) → API Tokens "
        "as POSTMARK_SERVER_API_TOKEN. Or set POSTMARK_ACCOUNT_API_TOKEN so this script "
        "can look up that server token (still put the server token in the app env for sending)."
    )


def resolve_server_token() -> tuple[str, str]:
    """Return (server_token, source_description)."""
    configured_server = _env_first(SERVER_TOKEN_ENV_KEYS)
    configured_account = _env_first(ACCOUNT_TOKEN_ENV_KEYS)
    server_id = (os.getenv("POSTMARK_SERVER_ID") or "").strip() or None
    server_name = (os.getenv("POSTMARK_SERVER_NAME") or "").strip() or None

    if configured_server:
        status, payload = _request(
            "GET",
            "/templates?count=1&offset=0&templateType=Standard",
            server_token=configured_server,
        )
        if status == 200:
            return configured_server, "POSTMARK_SERVER_API_TOKEN"

        account_candidate = configured_account or configured_server
        servers_status, servers_payload = _request(
            "GET",
            "/servers?count=1&offset=0",
            account_token=account_candidate,
        )
        if servers_status == 200:
            if configured_server and not configured_account:
                print(
                    "warning: POSTMARK_SERVER_API_TOKEN looks like an Account API token. "
                    "Resolving a Server API token from the account. "
                    "The running app still needs the Server API token to send email."
                )
            servers = list_servers(account_candidate)
            server = pick_server(servers, server_id=server_id, server_name=server_name)
            token = server_api_token(server)
            desc = f"account token → server {server.get('Name')!r} (id={server.get('ID')})"
            return token, desc

        if _is_unauthorized(status, payload):
            raise SyncError(
                f"Postmark rejected the token ({status}): {payload}. {_account_token_error()}"
            )
        raise SyncError(f"List templates failed ({status}): {payload}")

    if configured_account:
        servers = list_servers(configured_account)
        server = pick_server(servers, server_id=server_id, server_name=server_name)
        token = server_api_token(server)
        desc = f"POSTMARK_ACCOUNT_API_TOKEN → server {server.get('Name')!r} (id={server.get('ID')})"
        return token, desc

    raise SyncError(
        "Set POSTMARK_SERVER_API_TOKEN (Server API token) or POSTMARK_ACCOUNT_API_TOKEN "
        "(Account API token, used only to look up the server token)."
    )


def _existing_by_alias(token: str) -> dict[str, int]:
    found: dict[str, int] = {}
    offset = 0
    while True:
        status, payload = _request(
            "GET",
            f"/templates?count=100&offset={offset}&templateType=Standard",
            server_token=token,
        )
        if status != 200:
            raise SyncError(f"List templates failed ({status}): {payload}")
        templates = payload.get("Templates") or []
        for row in templates:
            alias = row.get("Alias") or ""
            tid = row.get("TemplateId")
            if alias and tid:
                found[alias] = int(tid)
        total = int(payload.get("TotalCount") or 0)
        offset += len(templates)
        if offset >= total or not templates:
            break
    return found


def _print_dry_run() -> None:
    for alias in ALIASES:
        spec = TEMPLATES[alias]
        html_len = len(spec["HtmlBody"])
        text_len = len(spec["TextBody"])
        print(f"{alias:28} name={spec['Name']!r} subject={spec['Subject']!r} html={html_len} text={text_len}")
    print(f"dry-run templates={len(ALIASES)}")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--dry-run" in args:
        _print_dry_run()
        return

    token, source = resolve_server_token()
    print(f"using server token from {source}")
    existing = _existing_by_alias(token)
    created = 0
    updated = 0
    for alias in ALIASES:
        body = template_bodies(alias)
        template_id = existing.get(alias)
        if template_id:
            status, payload = _request(
                "PUT", f"/templates/{template_id}", server_token=token, body=body
            )
            if status != 200:
                raise SyncError(f"Update {alias} failed ({status}): {payload}")
            updated += 1
            print(f"updated {alias} id={payload.get('TemplateId', template_id)}")
        else:
            status, payload = _request("POST", "/templates", server_token=token, body=body)
            if status != 200:
                raise SyncError(f"Create {alias} failed ({status}): {payload}")
            created += 1
            print(f"created {alias} id={payload.get('TemplateId')}")
    print(f"done created={created} updated={updated}")
    print(
        "Set POSTMARK_SERVER_API_TOKEN on the app to this server's API token "
        "(Postmark → Servers → API Tokens). An account token will not send mail."
    )


if __name__ == "__main__":
    try:
        main()
    except SyncError as exc:
        raise SystemExit(str(exc)) from exc
    except KeyboardInterrupt:
        sys.exit(130)
