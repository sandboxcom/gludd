"""Unit tests for the self-contained Notion database connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.notion import NotionSource

DATABASE_ID = "abc123-def456-ghi789"
TOKEN_ENV = "NOTION_TEST_TOKEN"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self) -> Any:
        return self._body


class RecordingTransport:
    """Replays a queue of responses and records every call."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "json": json or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            return FakeResponse(200, {})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


PAGE_1 = {
    "object": "page",
    "id": "page-1-id",
    "created_time": "2026-06-12T10:00:00.000Z",
    "last_edited_time": "2026-06-12T11:00:00.000Z",
    "url": "https://notion.so/page-1",
    "properties": {
        "Name": {
            "id": "title",
            "type": "title",
            "title": [
                {
                    "type": "text",
                    "text": {"content": "Meeting Notes", "link": None},
                    "plain_text": "Meeting Notes",
                }
            ],
        },
        "Status": {
            "id": "status",
            "type": "select",
            "select": {"name": "Done"},
        },
    },
}

PAGE_2 = {
    "object": "page",
    "id": "page-2-id",
    "created_time": "2026-06-12T12:00:00.000Z",
    "last_edited_time": "2026-06-12T13:00:00.000Z",
    "url": "https://notion.so/page-2",
    "properties": {
        "Name": {
            "id": "title",
            "type": "title",
            "title": [
                {
                    "type": "text",
                    "text": {"content": "Sprint Planning", "link": None},
                    "plain_text": "Sprint Planning",
                }
            ],
        },
    },
}


def _notion_list(
    results: list[dict[str, Any]],
    has_more: bool = False,
    next_cursor: str | None = None,
) -> dict[str, Any]:
    """Build a Notion API list-shaped response body."""
    body: dict[str, Any] = {
        "object": "list",
        "results": results,
        "has_more": has_more,
    }
    if next_cursor is not None:
        body["next_cursor"] = next_cursor
    return body


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(TOKEN_ENV, "s3cr3t-notion-token")
    return "s3cr3t-notion-token"


def _src(transport: Any, **extra: Any) -> NotionSource:
    cfg: dict[str, Any] = {"database_id": DATABASE_ID, "token_env": TOKEN_ENV}
    cfg.update(extra)
    return NotionSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert NotionSource.KIND == "pages"
    src = NotionSource(
        {"database_id": DATABASE_ID, "token_env": TOKEN_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.name == "notion"


def test_requires_database_id() -> None:
    with pytest.raises(ValueError, match="database_id"):
        NotionSource({"token_env": TOKEN_ENV}, transport=lambda *a, **k: None)


# -- normalization --------------------------------------------------------


def test_query_normalizes(token: str) -> None:
    body = _notion_list([PAGE_1, PAGE_2])
    transport = RecordingTransport([FakeResponse(200, body)])
    rows = _src(transport).query({"start_cursor": "cur-start"})

    assert len(rows) == 2
    first = rows[0]
    assert first["ts"] == "2026-06-12T11:00:00.000Z"
    assert first["source"] == "notion"
    assert first["kind"] == "pages"
    assert first["level_or_status"] == "info"
    assert first["message"] == "Meeting Notes"
    assert first["value"] == 1
    assert first["labels"] == {
        "page_id": "page-1-id",
        "url": "https://notion.so/page-1",
        "created_time": "2026-06-12T10:00:00.000Z",
        "last_edited_time": "2026-06-12T11:00:00.000Z",
    }
    assert first["raw"]["id"] == "page-1-id"
    assert rows[1]["message"] == "Sprint Planning"


def test_query_raises_on_http_error(token: str) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query({})


# -- Notion-Version header ------------------------------------------------


def test_notion_version_header(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _notion_list([]))])
    _src(transport, notion_version="2024-01-01").query({})
    sent_headers = transport.calls[0]["headers"]
    assert sent_headers["Notion-Version"] == "2024-01-01"


def test_notion_version_default_header(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _notion_list([]))])
    _src(transport).query({})
    sent_headers = transport.calls[0]["headers"]
    assert sent_headers["Notion-Version"] == "2022-06-28"


# -- auth from env --------------------------------------------------------


def test_auth_header_from_env(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _notion_list([]))])
    _src(transport).query({})
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {token}"


def test_missing_env_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, _notion_list([]))])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_token_not_in_any_url_or_body(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _notion_list([]))])
    _src(transport).query({})
    call = transport.calls[0]
    assert token not in call["url"]
    assert token not in str(call["json"])


# -- pagination (cursor) --------------------------------------------------


def test_pagination_follows_cursor(token: str) -> None:
    page1 = FakeResponse(
        200,
        _notion_list([PAGE_1], has_more=True, next_cursor="cur-page2"),
    )
    page2 = FakeResponse(200, _notion_list([PAGE_2], has_more=False))
    transport = RecordingTransport([page1, page2])
    rows = _src(transport).query({})
    assert len(rows) == 2
    assert len(transport.calls) == 2
    assert transport.calls[0]["json"]["start_cursor"] is None
    assert transport.calls[1]["json"]["start_cursor"] == "cur-page2"


def test_pagination_respects_start_cursor_from_spec(token: str) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, _notion_list([PAGE_1], has_more=False))]
    )
    _src(transport).query({"start_cursor": "cur-initial"})
    assert transport.calls[0]["json"]["start_cursor"] == "cur-initial"


def test_pagination_has_more_without_cursor_stops(token: str) -> None:
    page = FakeResponse(
        200,
        _notion_list([PAGE_1], has_more=True, next_cursor=None),
    )
    transport = RecordingTransport([page, FakeResponse(200, _notion_list([]))])
    rows = _src(transport).query({})
    assert len(rows) == 1
    assert len(transport.calls) == 1


# -- SSRF -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "http://127.0.0.1",
        "https://localhost",
        "http://10.0.0.5",
        "http://169.254.169.254",
        "http://192.168.1.1",
        "http://[::1]",
    ],
)
def test_ssrf_rejects_private(bad: str) -> None:
    with pytest.raises(ValueError, match=r"private|loopback"):
        NotionSource(
            {"database_id": DATABASE_ID, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


# -- health ---------------------------------------------------------------


def test_health_ok(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _notion_list([]))])
    h = _src(transport).health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_4xx(token: str) -> None:
    transport = RecordingTransport([FakeResponse(403, {})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "notion HTTP 403"


def test_health_never_raises_on_transport_error(token: str) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"
