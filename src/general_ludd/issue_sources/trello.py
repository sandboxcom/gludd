"""Trello issue-source adapter.

Reads cards from a Trello board and normalizes them into the common issue
shape used across General Ludd issue sources. Supports write-back of status
(moving a card to a mapped list) and adding comments.

Self-contained on purpose: this module does NOT import a shared base class or
sibling adapters (``issue_sources`` is a PEP-420 namespace package). The common
contract is duplicated deliberately so each adapter ships independently.

Security / robustness invariants:
- Secrets (API key + token) are read from the environment via ``*_env`` config
  keys. They are NEVER accepted inline and NEVER logged.
- ``base_url`` is validated against a literal-host SSRF blocklist with NO DNS
  resolution — only the parsed hostname literal is inspected.
- HTTP transport is injectable for tests; all requests are time-bound.
- ``health()`` never raises; it returns ``{"ok": bool, "detail": str}``.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

SYSTEM = "trello"

_DEFAULT_BASE_URL = "https://api.trello.com"
_DEFAULT_TIMEOUT = 30.0

_BLOCKED_HOST_LITERALS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }
)


def _reject_internal_base_url(base_url: str) -> None:
    """Raise ``ValueError`` if ``base_url`` targets an internal/literal host.

    No DNS resolution is performed: only the literal hostname in the URL is
    inspected. A hostname literal that is loopback/private/link-local/reserved,
    or a well-known internal name, is rejected. Non-http(s) schemes and missing
    hosts are rejected too.
    """

    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme for base_url: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ValueError("base_url has no host")
    lowered = host.lower()
    if lowered in _BLOCKED_HOST_LITERALS:
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


class TrelloIssueSource:
    """Trello board → normalized issues, with status + comment write-back."""

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

        self._board_id = str(config.get("board_id") or config.get("board") or "")

        key_env = str(config.get("api_key_env") or "TRELLO_API_KEY")
        token_env = str(config.get("token_env") or "TRELLO_TOKEN")
        self._api_key = os.environ.get(key_env, "")
        self._token = os.environ.get(token_env, "")

        # Maps a desired logical status -> destination list id for write-back.
        raw_map = config.get("status_list_map") or {}
        self._status_list_map: dict[str, str] = {str(k): str(v) for k, v in raw_map.items()}

        self._timeout = float(config.get("timeout") or _DEFAULT_TIMEOUT)
        self._transport = transport

    # -- internal helpers -------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        )

    def _auth_params(self) -> dict[str, str]:
        return {"key": self._api_key, "token": self._token}

    def _list_names(self, client: httpx.Client) -> dict[str, str]:
        resp = client.get(
            f"/1/boards/{self._board_id}/lists",
            params=self._auth_params(),
        )
        resp.raise_for_status()
        return {str(lst["id"]): str(lst.get("name", "")) for lst in resp.json()}

    @staticmethod
    def _normalize_card(card: dict[str, Any], list_names: dict[str, str]) -> dict[str, Any]:
        labels = [str(lbl.get("name", "")) for lbl in card.get("labels", []) if lbl.get("name")]
        members = card.get("idMembers") or []
        assignee = str(members[0]) if members else None
        return {
            "external_id": str(card.get("id", "")),
            "source": SYSTEM,
            "title": str(card.get("name", "")),
            "description": str(card.get("desc", "")),
            "status": list_names.get(str(card.get("idList", "")), ""),
            "assignee": assignee,
            "labels": labels,
            "priority": None,
            "url": str(card.get("shortUrl") or card.get("url") or ""),
            "updated_ts": card.get("dateLastActivity"),
            "raw": card,
        }

    # -- public contract --------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            with self._client() as client:
                resp = client.get(
                    f"/1/boards/{self._board_id}",
                    params=self._auth_params(),
                )
            if resp.status_code == 200:
                return {"ok": True, "detail": "trello reachable"}
            return {"ok": False, "detail": f"http {resp.status_code}"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        board = str(spec.get("board_id") or self._board_id)
        with self._client() as client:
            list_names = self._list_names(client)
            resp = client.get(
                f"/1/boards/{board}/cards",
                params=self._auth_params(),
            )
            resp.raise_for_status()
            cards = resp.json()
        return [self._normalize_card(card, list_names) for card in cards]

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        dest_list = self._status_list_map.get(status)
        if not dest_list:
            raise ValueError(f"no Trello list mapped for status {status!r}")
        result: dict[str, Any] = {}
        with self._client() as client:
            move = client.put(
                f"/1/cards/{external_id}",
                params={**self._auth_params(), "idList": dest_list},
            )
            move.raise_for_status()
            result["moved"] = move.json()
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
        def _post(client: httpx.Client) -> dict[str, Any]:
            resp = client.post(
                f"/1/cards/{external_id}/actions/comments",
                params={**self._auth_params(), "text": comment},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        if _client is not None:
            return _post(_client)
        with self._client() as client:
            return _post(client)
