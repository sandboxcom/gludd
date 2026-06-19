"""Monday.com issue-source adapter.

Self-contained, config-driven adapter that reads "items" from a Monday.com
board via the GraphQL v2 API and normalizes them into the gludd issue dict
shape. Supports status write-back (``change_column_value``) and comment
write-back (``create_update``).

The module deliberately has no dependency on sibling adapters, a base class,
or package ``__init__`` side effects. The default transport uses only the
stdlib ``urllib``; tests inject a mock transport.

Transport contract (injectable callable)::

    transport(method: str, url: str, headers: dict[str, str],
              json_body: dict | None, timeout: float) -> tuple[int, dict]

Returns ``(status_code, parsed_json_payload)``. The default transport never
uses ``shell=True`` and is strictly time-bound.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Opener handler that refuses to follow any HTTP redirect.

    Raising ``urllib.error.HTTPError`` on redirect prevents SSRF via
    attacker-controlled 3xx responses that point at internal endpoints.
    """

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise urllib.error.HTTPError(
            url=newurl,
            code=code,
            msg=f"redirect blocked: {msg}",
            hdrs=headers,
            fp=None,
        )


Transport = Callable[
    [str, str, Mapping[str, str], "dict[str, Any] | None", float],
    "tuple[int, dict[str, Any]]",
]

_DEFAULT_BASE_URL = "https://api.monday.com"
_DEFAULT_TIMEOUT = 15.0


def _is_blocked_host(host: str) -> bool:
    """Return True if ``host`` is a literal that must be SSRF-blocked.

    Only literal inspection is performed; no DNS resolution happens here so
    that a hostile DNS answer cannot move a name from "allowed" to "internal"
    after the check. Bare/internal-looking hostnames are also rejected.
    """
    host = (host or "").strip().lower()
    if not host:
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        )
    if host in {"localhost", "localhost.localdomain"}:
        return True
    if "." not in host:
        return True
    return host.endswith((".local", ".internal", ".localhost", ".intranet"))


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    json_body: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        _opener = urllib.request.build_opener(_NoRedirectHandler())
        with _opener.open(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            body = resp.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        status = int(exc.code)
        body = exc.read() if hasattr(exc, "read") else b""
    payload: dict[str, Any] = {}
    if body:
        try:
            parsed = json.loads(body.decode("utf-8"))
            payload = parsed if isinstance(parsed, dict) else {"data": parsed}
        except (ValueError, UnicodeDecodeError):
            payload = {}
    return status, payload


class MondayIssueSource:
    """Issue source backed by the Monday.com GraphQL v2 API."""

    SYSTEM = "monday"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._config = dict(config or {})
        self.name = str(self._config.get("name") or self.SYSTEM)
        self._env: Mapping[str, str] = env if env is not None else os.environ
        self._transport: Transport = transport or _default_transport

        base_url = str(self._config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        parts = urlsplit(base_url)
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            raise ValueError(f"invalid base_url: {base_url!r}")
        if _is_blocked_host(parts.hostname):
            raise ValueError(f"base_url host is internal/blocked: {parts.hostname!r}")
        self._base_url = base_url

        self._timeout = float(self._config.get("timeout", _DEFAULT_TIMEOUT))
        self._status_column = str(self._config.get("status_column", "status"))
        self._board_id = self._config.get("board_id")

    # -- secrets ---------------------------------------------------------
    def _token(self) -> str:
        env_key = str(self._config.get("token_env", "MONDAY_API_TOKEN"))
        return str(self._env.get(env_key, ""))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._token(),
            "Content-Type": "application/json",
            "API-Version": str(self._config.get("api_version", "2023-10")),
        }

    def _endpoint(self) -> str:
        return f"{self._base_url}/v2"

    def _gql(self, query: str, variables: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._transport(
            "POST",
            self._endpoint(),
            self._headers(),
            {"query": query, "variables": variables},
            self._timeout,
        )

    # -- health ----------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Lightweight reachability/auth probe. Never raises."""
        query = "query { me { id } }"
        try:
            status, payload = self._gql(query, {})
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}
        if status != 200:
            return {"ok": False, "detail": f"http {status}"}
        if payload.get("errors"):
            return {"ok": False, "detail": str(payload["errors"])}
        me = (payload.get("data") or {}).get("me") or {}
        if not me.get("id"):
            return {"ok": False, "detail": "no authenticated user"}
        return {"ok": True, "detail": "ok"}

    # -- normalization ---------------------------------------------------
    def _column_text(self, item: Mapping[str, Any], title_or_id: str) -> str:
        for col in item.get("column_values") or []:
            if col.get("id") == title_or_id or col.get("title") == title_or_id:
                return str(col.get("text") or "")
        return ""

    def _normalize(self, item: Mapping[str, Any]) -> dict[str, Any]:
        item_id = str(item.get("id") or "")
        creators = item.get("creators") or []
        people = self._column_text(item, "person") or self._column_text(item, "people")
        if people:
            assignee = people
        elif creators:
            assignee = str((creators[0] or {}).get("name") or "")
        else:
            assignee = ""
        labels: list[str] = []
        tags = self._column_text(item, "tags")
        if tags:
            labels = [t.strip() for t in tags.split(",") if t.strip()]
        return {
            "external_id": item_id,
            "source": self.SYSTEM,
            "title": str(item.get("name") or ""),
            "description": str(self._column_text(item, "description") or ""),
            "status": str(self._column_text(item, self._status_column) or ""),
            "assignee": assignee,
            "labels": labels,
            "priority": str(self._column_text(item, "priority") or ""),
            "url": f"https://view.monday.com/{item_id}" if item_id else "",
            "updated_ts": str(item.get("updated_at") or ""),
            "raw": dict(item),
        }

    def fetch_issues(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = dict(spec or {})
        board_id = spec.get("board_id", self._board_id)
        if board_id is None:
            raise ValueError("board_id required (spec or config)")
        limit = int(spec.get("limit", 100))
        query = (
            "query ($board: [ID!], $limit: Int) {"
            "  boards (ids: $board) {"
            "    items_page (limit: $limit) {"
            "      items {"
            "        id name updated_at"
            "        creators { id name }"
            "        column_values { id title text }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        status, payload = self._gql(query, {"board": [str(board_id)], "limit": limit})
        if status != 200:
            raise RuntimeError(f"monday fetch failed: http {status}")
        if payload.get("errors"):
            raise RuntimeError(f"monday fetch errors: {payload['errors']}")
        boards = (payload.get("data") or {}).get("boards") or []
        out: list[dict[str, Any]] = []
        for board in boards:
            page = board.get("items_page") or {}
            for item in page.get("items") or []:
                out.append(self._normalize(item))
        return out

    # -- write-back ------------------------------------------------------
    def update_status(
        self, external_id: str, status: str, comment: str | None = None
    ) -> dict[str, Any]:
        board_id = self._board_id
        if board_id is None:
            raise ValueError("board_id required in config for update_status")
        mutation = (
            "mutation ($board: ID!, $item: ID!, $column: String!, $value: JSON!) {"
            "  change_column_value (board_id: $board, item_id: $item,"
            "    column_id: $column, value: $value) { id }"
            "}"
        )
        value = json.dumps({"label": status})
        variables = {
            "board": str(board_id),
            "item": str(external_id),
            "column": self._status_column,
            "value": value,
        }
        code, payload = self._gql(mutation, variables)
        result: dict[str, Any] = {
            "ok": code == 200 and not payload.get("errors"),
            "external_id": str(external_id),
            "status": status,
            "http": code,
            "raw": payload,
        }
        if comment:
            result["comment"] = self.add_comment(external_id, comment)
        return result

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        mutation = (
            "mutation ($item: ID!, $body: String!) {"
            "  create_update (item_id: $item, body: $body) { id }"
            "}"
        )
        code, payload = self._gql(mutation, {"item": str(external_id), "body": comment})
        return {
            "ok": code == 200 and not payload.get("errors"),
            "external_id": str(external_id),
            "http": code,
            "raw": payload,
        }
