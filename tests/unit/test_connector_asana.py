"""Unit tests for the self-contained Asana project-tasks connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.asana import AsanaSource

PROJECT_GID = "12345"
TOKEN_ENV = "ASANA_TEST_TOKEN"


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
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            return FakeResponse(200, {"data": []})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


CANNED_TASK = {
    "gid": "task-1",
    "name": "Implement feature X",
    "completed": False,
    "modified_at": "2026-07-10T12:00:00.000Z",
    "created_at": "2026-07-01T00:00:00.000Z",
    "assignee": {"gid": "user-1", "name": "Alice"},
    "due_on": "2026-07-20",
    "permalink_url": "https://app.asana.com/0/12345/task-1",
    "notes": "detailed description here",
}


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(TOKEN_ENV, "asana-very-secret-token")
    return "asana-very-secret-token"


def _src(transport: Any, **extra: Any) -> AsanaSource:
    cfg: dict[str, Any] = {"project_gid": PROJECT_GID, "token_env": TOKEN_ENV}
    cfg.update(extra)
    return AsanaSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert AsanaSource.KIND == "tasks"
    src = AsanaSource(
        {"project_gid": PROJECT_GID, "token_env": TOKEN_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.name == "asana"


def test_requires_project_gid() -> None:
    with pytest.raises(ValueError, match="project_gid"):
        AsanaSource({"token_env": TOKEN_ENV}, transport=lambda *a, **k: None)


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        AsanaSource(
            {"project_gid": PROJECT_GID}, transport=lambda *a, **k: None
        )


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
        AsanaSource(
            {
                "project_gid": PROJECT_GID,
                "token_env": TOKEN_ENV,
                "base_url": bad,
            },
            transport=lambda *a, **k: None,
        )


def test_ssrf_allow_private_opt_in(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, {"data": []})])
    src = AsanaSource(
        {
            "project_gid": PROJECT_GID,
            "token_env": TOKEN_ENV,
            "base_url": "http://127.0.0.1",
            "allow_private": True,
        },
        transport=transport,
    )
    assert src.query({}) == []


# -- auth -----------------------------------------------------------------


def test_auth_header_present(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, {"data": []})])
    _src(transport).query({})
    assert (
        transport.calls[0]["headers"]["Authorization"]
        == f"Bearer {token}"
    )


def test_missing_env_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, {"data": []})])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


# -- query / normalisation -------------------------------------------------


def test_query_normalizes(token: str) -> None:
    body = {"data": [CANNED_TASK]}
    transport = RecordingTransport([FakeResponse(200, body)])
    rows = _src(transport).query({"modified_since": "2026-07-01T00:00:00Z"})

    assert len(rows) == 1
    first = rows[0]
    assert first["ts"] == "2026-07-10T12:00:00.000Z"
    assert first["source"] == "asana"
    assert first["kind"] == "tasks"
    assert first["level_or_status"] == "open"
    assert first["message"] == "Implement feature X"
    assert first["value"] == 1
    assert first["labels"]["assignee"] == "Alice"
    assert first["labels"]["due_on"] == "2026-07-20"
    assert first["labels"]["completed"] is False
    assert first["labels"]["permalink_url"] == "https://app.asana.com/0/12345/task-1"
    assert first["raw"]["gid"] == "task-1"


def test_query_passes_spec_params(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, {"data": []})])
    _src(transport).query(
        {
            "modified_since": "2026-07-01",
            "completed_since": "2026-07-10",
            "assignee": "user-1",
        }
    )
    sent = transport.calls[0]["params"]
    assert sent["modified_since"] == "2026-07-01"
    assert sent["completed_since"] == "2026-07-10"
    assert sent["assignee"] == "user-1"


def test_query_raises_on_http_error(token: str) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query({})


def test_query_raises_on_4xx(token: str) -> None:
    transport = RecordingTransport([FakeResponse(404, {})])
    with pytest.raises(RuntimeError, match="HTTP 404"):
        _src(transport).query({})


# -- pagination ------------------------------------------------------------


def test_pagination_follows_next_page_uri(token: str) -> None:
    page1 = FakeResponse(
        200,
        {
            "data": [CANNED_TASK],
            "next_page": {
                "offset": "eyJ...",
                "path": "/api/1.0/projects/12345/tasks",
                "uri": "https://app.asana.com/api/1.0/projects/12345/tasks?offset=eyJ...&limit=10",
            },
        },
    )
    task2 = dict(CANNED_TASK, gid="task-2", name="Second task")
    page2 = FakeResponse(200, {"data": [task2]})
    transport = RecordingTransport([page1, page2])
    rows = _src(transport).query({})
    assert len(rows) == 2
    assert len(transport.calls) == 2
    assert rows[0]["raw"]["gid"] == "task-1"
    assert rows[1]["raw"]["gid"] == "task-2"


def test_pagination_bounded_by_max_pages(token: str) -> None:
    looping = FakeResponse(
        200,
        {
            "data": [CANNED_TASK],
            "next_page": {
                "uri": "https://app.asana.com/api/1.0/projects/12345/tasks?offset=next&limit=1",
            },
        },
    )
    transport = RecordingTransport([looping] * 50)
    rows = _src(transport, max_pages=3).query({})
    assert len(transport.calls) == 3
    assert len(rows) == 3


# -- health ---------------------------------------------------------------


def test_health_ok(token: str) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, {"data": [CANNED_TASK]})]
    )
    h = _src(transport).health()
    assert h["ok"] is True
    assert "asana reachable" in h["detail"]


def test_health_not_ok_on_4xx(token: str) -> None:
    transport = RecordingTransport(
        [FakeResponse(403, {"errors": [{"message": "forbidden"}]})]
    )
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "asana HTTP 403"


def test_health_never_raises(token: str) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"
