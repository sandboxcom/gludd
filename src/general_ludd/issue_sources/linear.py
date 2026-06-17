"""Linear issue-source adapter.

Queries issues from Linear's GraphQL API and normalizes them into the common
issue shape. Supports write-back of workflow state (``issueUpdate``) and adding
comments (``commentCreate``).

Self-contained: no shared base class / sibling imports (``issue_sources`` is a
PEP-420 namespace package).

Security / robustness invariants mirror the other adapters: env-only secret,
literal-host SSRF block (no DNS) on ``base_url``, injectable + time-bound HTTP
transport, and a ``health()`` that never raises.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

SYSTEM = "linear"

_DEFAULT_BASE_URL = "https://api.linear.app"
_DEFAULT_TIMEOUT = 30.0

_BLOCKED_HOST_LITERALS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }
)

_ISSUES_QUERY = """
query Issues($first: Int!) {
  issues(first: $first) {
    nodes {
      id
      identifier
      title
      description
      url
      updatedAt
      priority
      state { name }
      assignee { name }
      labels { nodes { name } }
    }
  }
}
""".strip()

_ISSUE_UPDATE_MUTATION = """
mutation IssueUpdate($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue { id state { name } }
  }
}
""".strip()

_COMMENT_CREATE_MUTATION = """
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}
""".strip()


def _reject_internal_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme for base_url: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ValueError("base_url has no host")
    if host.lower() in _BLOCKED_HOST_LITERALS:
        raise ValueError(f"internal/blocked host in base_url: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    ):
        raise ValueError(f"internal/blocked IP in base_url: {host!r}")


class LinearIssueSource:
    """Linear GraphQL → normalized issues, with state + comment write-back."""

    SYSTEM = SYSTEM

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = str(config.get("name") or SYSTEM)
        self._base_url = str(config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        _reject_internal_base_url(self._base_url)

        key_env = str(config.get("api_key_env") or "LINEAR_API_KEY")
        self._api_key = os.environ.get(key_env, "")

        # Maps a desired logical status -> Linear workflow-state id for write-back.
        raw_map = config.get("status_state_map") or {}
        self._status_state_map: dict[str, str] = {str(k): str(v) for k, v in raw_map.items()}

        self._page_size = int(config.get("page_size") or 50)
        self._timeout = float(config.get("timeout") or _DEFAULT_TIMEOUT)
        self._transport = transport

    # -- internal helpers -------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": self._api_key, "Content-Type": "application/json"},
        )

    def _graphql(
        self,
        client: httpx.Client,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        resp = client.post("/graphql", json={"query": query, "variables": variables})
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"linear graphql errors: {payload['errors']}")
        data: dict[str, Any] = payload.get("data", {})
        return data

    @staticmethod
    def _normalize_issue(node: dict[str, Any]) -> dict[str, Any]:
        state = node.get("state") or {}
        assignee = node.get("assignee") or {}
        labels_container = node.get("labels") or {}
        labels = [
            str(lbl.get("name", ""))
            for lbl in labels_container.get("nodes", [])
            if lbl.get("name")
        ]
        return {
            "external_id": str(node.get("id", "")),
            "source": SYSTEM,
            "title": str(node.get("title", "")),
            "description": str(node.get("description") or ""),
            "status": str(state.get("name", "")),
            "assignee": (str(assignee.get("name")) if assignee.get("name") else None),
            "labels": labels,
            "priority": node.get("priority"),
            "url": str(node.get("url", "")),
            "updated_ts": node.get("updatedAt"),
            "raw": node,
        }

    # -- public contract --------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            with self._client() as client:
                data = self._graphql(client, "query { viewer { id } }", {})
            if data.get("viewer"):
                return {"ok": True, "detail": "linear reachable"}
            return {"ok": False, "detail": "no viewer in response"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        first = int(spec.get("first") or self._page_size)
        with self._client() as client:
            data = self._graphql(client, _ISSUES_QUERY, {"first": first})
        nodes = (data.get("issues") or {}).get("nodes", [])
        return [self._normalize_issue(node) for node in nodes]

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        state_id = self._status_state_map.get(status)
        if not state_id:
            raise ValueError(f"no Linear state mapped for status {status!r}")
        result: dict[str, Any] = {}
        with self._client() as client:
            data = self._graphql(
                client,
                _ISSUE_UPDATE_MUTATION,
                {"id": external_id, "stateId": state_id},
            )
            result["updated"] = data.get("issueUpdate", {})
            if comment:
                result["comment"] = self.add_comment(external_id, comment, _client=client)
        return result

    def add_comment(
        self,
        external_id: str,
        comment: str,
        *,
        _client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        def _create(client: httpx.Client) -> dict[str, Any]:
            data = self._graphql(
                client,
                _COMMENT_CREATE_MUTATION,
                {"issueId": external_id, "body": comment},
            )
            created: dict[str, Any] = data.get("commentCreate", {})
            return created

        if _client is not None:
            return _create(_client)
        with self._client() as client:
            return _create(client)
