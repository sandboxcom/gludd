"""Branch contracts for beta4 hosted issue-source adapters."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.issue_sources import bitbucket_issues, clickup, linear, monday
from general_ludd.security.url_fetch import FetchResult


@pytest.mark.parametrize("module", [clickup, monday, bitbucket_issues])
@pytest.mark.parametrize(
    ("host", "blocked"),
    [
        ("", True),
        ("[::1]", True),
        ("localhost", True),
        ("barehost", True),
        ("service.internal", True),
        ("api.example.test", False),
    ],
)
def test_adapter_host_guards_cover_every_literal_class(module: Any, host: str, blocked: bool) -> None:
    assert module._is_blocked_host(host) is blocked


@pytest.mark.parametrize("module", [clickup, bitbucket_issues])
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"", {}),
        (b"not-json", {}),
        (b"[1, 2]", {"data": [1, 2]}),
    ],
)
def test_secure_fetch_transports_narrow_response_shapes(
    module: Any,
    content: bytes,
    expected: dict[str, object],
) -> None:
    result = FetchResult(
        url="https://api.example.test",
        status_code=202,
        headers={},
        content=content,
    )
    with patch.object(module, "secure_fetch", return_value=result):
        status, payload = module._default_transport(
            "GET", "https://api.example.test/path", {}, None, 1.0
        )
    assert status == 202
    assert payload == expected


def test_clickup_normalization_and_fetch_failure_branches() -> None:
    source = clickup.ClickUpIssueSource({"list_id": "L"}, transport=MagicMock())
    issue = source._normalize(
        cast(
            clickup.ClickUpTask,
            {
                "id": "1",
                "status": "open",
                "priority": "high",
                "assignees": [{"email": "owner@example.test"}],
                "tags": [{"name": ""}, {"name": "bug"}],
                "text_content": "fallback",
            },
        )
    )
    assert issue["status"] == "open"
    assert issue["priority"] == "high"
    assert issue["assignee"] == "owner@example.test"
    assert issue["labels"] == ["bug"]
    assert source._extract_tasks(None) == []
    assert source._extract_tasks({"tasks": [None, {"id": "1"}]}) == [{"id": "1"}]

    source._transport = MagicMock(return_value=(500, {}))
    with pytest.raises(RuntimeError, match="fetch failed"):
        source.fetch_issues({"include_closed": True, "page": object()})
    with pytest.raises(ValueError, match="list_id required"):
        clickup.ClickUpIssueSource({}, transport=MagicMock()).fetch_issues()


def test_bitbucket_normalization_query_and_auth_fallbacks() -> None:
    transport = MagicMock(return_value=(200, {"values": []}))
    source = bitbucket_issues.BitbucketIssueSource(
        {"workspace": "workspace", "repo": "repo"},
        transport=transport,
        env={},
    )
    assert source._auth_header() == ""
    assert "Authorization" not in source._headers()
    issue = source._normalize(
        cast(
            bitbucket_issues.BitbucketIssue,
            {"id": 1, "assignee": "invalid", "content": "invalid", "links": "invalid"},
        )
    )
    assert issue["assignee"] == ""
    assert issue["description"] == ""
    assert issue["url"] == ""
    assert issue["labels"] == []
    assert source._extract_values({"values": [None, {"id": 1}]}) == [{"id": 1}]
    source.fetch_issues({"q": "status = open", "pagelen": object()})
    assert "q=status%20%3D%20open" in transport.call_args.args[1]
    with pytest.raises(ValueError, match="workspace and repo"):
        bitbucket_issues.BitbucketIssueSource({}, transport=transport)._repo_path(None, None)
    transport.return_value = (500, {})
    with pytest.raises(RuntimeError, match="fetch failed"):
        source.fetch_issues()


def test_monday_coercion_normalization_and_malformed_payloads() -> None:
    assert monday._as_float(object(), 1.5) == 1.5
    assert monday._as_int(object(), 3) == 3
    source = monday.MondayIssueSource({"board_id": "B"}, transport=MagicMock())
    people_item = cast(
        monday.MondayItem,
        {
            "id": "1",
            "creators": [{"name": "creator"}],
            "column_values": [
                {"id": "person", "text": "assigned"},
                {"id": "tags", "text": "bug, , urgent"},
            ],
        },
    )
    assert source._normalize(people_item)["assignee"] == "assigned"
    creator_item = cast(monday.MondayItem, {"creators": [{"name": "creator"}]})
    assert source._normalize(creator_item)["assignee"] == "creator"
    assert source._normalize(cast(monday.MondayItem, {}))["assignee"] == ""
    malformed = {"data": {"boards": [None, {"items_page": None}, {"items_page": {"items": [None, {"id": "1"}]}}]}}
    assert source._extract_items(malformed) == [{"id": "1"}]
    source._transport = MagicMock(return_value=(200, {"errors": ["denied"]}))
    with pytest.raises(RuntimeError, match="fetch errors"):
        source.fetch_issues()
    with pytest.raises(ValueError, match="board_id required"):
        monday.MondayIssueSource({}, transport=MagicMock()).update_status("1", "done")


class _Response:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return 200

    def read(self) -> bytes:
        return self.body


@pytest.mark.parametrize(
    ("body", "expected"),
    [(b"", {}), (b"invalid", {}), (b"[1]", {"data": [1]})],
)
def test_monday_transport_narrows_response_shapes(body: bytes, expected: dict[str, object]) -> None:
    opener = MagicMock()
    opener.open.return_value = _Response(body)
    with patch("general_ludd.issue_sources.monday.urllib.request.build_opener", return_value=opener):
        status, payload = monday._default_transport(
            "POST", "https://api.example.test/v2", {"X-Test": "1"}, None, 1.0
        )
    assert status == 200
    assert payload == expected


@pytest.mark.parametrize(
    "base_url",
    ["ftp://example.test", "https:///missing", "http://127.0.0.1", "http://224.0.0.1"],
)
def test_linear_rejects_unsupported_and_non_global_urls(base_url: str) -> None:
    with pytest.raises(ValueError):
        linear._reject_internal_base_url(base_url)


def test_linear_graphql_and_normalization_failure_branches() -> None:
    node: dict[str, Any] = {
        "id": "1",
        "state": None,
        "assignee": None,
        "labels": {"nodes": [{"name": ""}, {"name": "bug"}]},
    }
    issue = linear.LinearIssueSource._normalize_issue(node)
    assert issue["assignee"] is None
    assert issue["labels"] == ["bug"]

    def errors(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "denied"}]})

    source = linear.LinearIssueSource({}, transport=httpx.MockTransport(errors))
    with pytest.raises(RuntimeError, match="graphql errors"):
        source.fetch_issues()


def test_linear_add_comment_reuses_or_owns_client() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": {"commentCreate": {"success": True}}})

    source = linear.LinearIssueSource({}, transport=httpx.MockTransport(handler))
    with source._client() as client:
        assert source.add_comment("1", "body", _client=client)["success"] is True
    assert source.add_comment("1", "body")["success"] is True
    assert calls == 2
