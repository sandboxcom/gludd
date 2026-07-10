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

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from email.message import Message
from typing import IO, TypedDict, cast
from urllib.parse import urlsplit

from general_ludd.security.ssrf import host_is_blocked


class MondayColumnValue(TypedDict, total=False):
    """A single column-value entry on a Monday.com item."""

    id: str
    title: str
    text: str


class MondayCreator(TypedDict, total=False):
    """A creator entry on a Monday.com item."""

    id: str
    name: str


class MondayItem(TypedDict, total=False):
    """A Monday.com board item as returned by the GraphQL v2 ``items_page``.

    All fields are optional (``total=False``) because the GraphQL projection
    may omit any of them and Monday.com payloads routinely vary.
    """

    id: str
    name: str
    updated_at: str
    creators: list[MondayCreator]
    column_values: list[MondayColumnValue]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Opener handler that refuses to follow any HTTP redirect.

    Raising ``urllib.error.HTTPError`` on redirect prevents SSRF via
    attacker-controlled 3xx responses that point at internal endpoints.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes] | None,
        code: int,
        msg: str,
        headers: Message,
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
    [str, str, Mapping[str, str], "dict[str, object] | None", float],
    "tuple[int, dict[str, object]]",
]

_DEFAULT_BASE_URL = "https://api.monday.com"
_DEFAULT_TIMEOUT = 15.0


def _is_blocked_host(host: str) -> bool:
    """Return True if ``host`` is a literal that must be SSRF-blocked.

    Only literal inspection is performed; no DNS resolution happens here so
    that a hostile DNS answer cannot move a name from "allowed" to "internal"
    after the check. Delegates the loopback/metadata-name/IP-literal decision
    to the canonical :func:`general_ludd.security.ssrf.host_is_blocked` (so
    this adapter's classification — previously missing ``metadata.goog``/
    ``instance-data``/the ``ip6-*`` aliases and the ``not is_global`` catch
    for CGNAT/TEST-NET ranges — can never drift from the single source of
    truth), then ADDITIVELY also rejects any bare hostname with no dot and
    any hostname ending in ``.local``/``.internal``/``.localhost``/
    ``.intranet`` — a stricter rule this adapter had before consolidation
    that the canonical guard does not apply (it would be too strict for
    other callers).
    """
    host = (host or "").strip().lower()
    if not host:
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host_is_blocked(host):
        return True
    if "." not in host:
        return True
    return host.endswith((".local", ".internal", ".localhost", ".intranet"))


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    json_body: dict[str, object] | None,
    timeout: float,
) -> tuple[int, dict[str, object]]:
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
    payload: dict[str, object] = {}
    if body:
        try:
            parsed: object = json.loads(body.decode("utf-8"))
            payload = parsed if isinstance(parsed, dict) else {"data": parsed}
        except (ValueError, UnicodeDecodeError):
            payload = {}
    return status, payload


def _as_float(value: object, default: float) -> float:
    """Coerce ``value`` to float; fall back to ``default`` on unsupported types."""
    if isinstance(value, (int, float, str)):
        return float(value)
    return default


def _as_int(value: object, default: int) -> int:
    """Coerce ``value`` to int; fall back to ``default`` on unsupported types."""
    if isinstance(value, (int, float, str)):
        return int(value)
    return default


class MondayIssueSource:
    """Issue source backed by the Monday.com GraphQL v2 API."""

    SYSTEM = "monday"

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._config: dict[str, object] = dict(config or {})
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

        self._timeout = _as_float(self._config.get("timeout", _DEFAULT_TIMEOUT), _DEFAULT_TIMEOUT)
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

    def _gql(
        self, query: str, variables: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        return self._transport(
            "POST",
            self._endpoint(),
            self._headers(),
            {"query": query, "variables": variables},
            self._timeout,
        )

    # -- health ----------------------------------------------------------
    def health(self) -> dict[str, object]:
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
        data = payload.get("data")
        me = data.get("me") if isinstance(data, Mapping) else None
        if not (isinstance(me, Mapping) and me.get("id")):
            return {"ok": False, "detail": "no authenticated user"}
        return {"ok": True, "detail": "ok"}

    # -- normalization ---------------------------------------------------
    def _column_text(self, item: MondayItem, title_or_id: str) -> str:
        for col in item.get("column_values") or []:
            if col.get("id") == title_or_id or col.get("title") == title_or_id:
                return str(col.get("text") or "")
        return ""

    def _normalize(self, item: MondayItem) -> dict[str, object]:
        item_id = str(item.get("id") or "")
        creators = item.get("creators") or []
        people = self._column_text(item, "person") or self._column_text(item, "people")
        if people:
            assignee = people
        elif creators:
            first = creators[0] or {}
            assignee = str(first.get("name") or "")
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

    @staticmethod
    def _extract_items(payload: object) -> list[MondayItem]:
        """Narrow a GraphQL response payload to a list of Monday items.

        Mirrors the ``_extract_builds`` pattern from the connectors layer:
        each level is narrowed with ``isinstance`` because the transport
        contract only promises ``dict[str, object]``.
        """
        items: list[MondayItem] = []
        data = payload.get("data") if isinstance(payload, Mapping) else None
        boards = data.get("boards") if isinstance(data, Mapping) else None
        if isinstance(boards, list):
            for board in boards:
                page = board.get("items_page") if isinstance(board, Mapping) else None
                board_items = page.get("items") if isinstance(page, Mapping) else None
                if isinstance(board_items, list):
                    for entry in board_items:
                        if isinstance(entry, dict):
                            items.append(cast(MondayItem, entry))
        return items

    def fetch_issues(
        self, spec: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        spec_dict: dict[str, object] = dict(spec or {})
        board_id = spec_dict.get("board_id", self._board_id)
        if board_id is None:
            raise ValueError("board_id required (spec or config)")
        limit = _as_int(spec_dict.get("limit", 100), 100)
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
        return [self._normalize(item) for item in self._extract_items(payload)]

    # -- write-back ------------------------------------------------------
    def update_status(
        self, external_id: str, status: str, comment: str | None = None
    ) -> dict[str, object]:
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
        variables: dict[str, object] = {
            "board": str(board_id),
            "item": str(external_id),
            "column": self._status_column,
            "value": value,
        }
        code, payload = self._gql(mutation, variables)
        result: dict[str, object] = {
            "ok": code == 200 and not payload.get("errors"),
            "external_id": str(external_id),
            "status": status,
            "http": code,
            "raw": payload,
        }
        if comment:
            result["comment"] = self.add_comment(external_id, comment)
        return result

    def add_comment(self, external_id: str, comment: str) -> dict[str, object]:
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
