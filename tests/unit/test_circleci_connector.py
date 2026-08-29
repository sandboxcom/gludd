"""Unit tests for the self-contained CircleCiSource connector.

A fake transport returns canned payloads so no real network is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

import general_ludd.connectors.circleci as circleci
from general_ludd.connectors.circleci import CircleCiSource, _parse_ts

_SLUG = "gh/acme/widgets"

_PIPELINE_BODY: dict[str, Any] = {
    "items": [
        {
            "id": "pipe-1",
            "number": 42,
            "state": "created",
            "created_at": "2026-06-10T08:00:00.500Z",
            "updated_at": "2026-06-10T08:01:00Z",
            "vcs": {"revision": "abc123def", "branch": "main"},
        },
        {
            "id": "pipe-2",
            "number": 43,
            "state": "errored",
            "created_at": "2026-06-11T09:00:00Z",
            "vcs": {"revision": "999", "branch": "release"},
        },
    ],
    "next_page_token": None,
}

_WORKFLOW_BODY: dict[str, Any] = {
    "items": [
        {
            "id": "wf-1",
            "name": "build-test-deploy",
            "status": "success",
            "pipeline_number": 42,
            "created_at": "2026-06-10T08:02:00Z",
            "stopped_at": "2026-06-10T08:09:00Z",
        }
    ],
}


class _FakeTransport:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.url: str | None = None
        self.headers: dict[str, str] | None = None

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, Any]:
        self.url = url
        self.headers = headers
        return self.status, self.body


def _make(transport: _FakeTransport, **cfg: Any) -> CircleCiSource:
    config = {"project_slug": _SLUG}
    config.update(cfg)
    return CircleCiSource(config, http_get=transport)


def test_kind_and_name() -> None:
    src = _make(_FakeTransport(200, _PIPELINE_BODY))
    assert src.KIND == "pipeline"
    assert src.name == f"circleci:{_SLUG}"


def test_default_base_url() -> None:
    src = _make(_FakeTransport(200, _PIPELINE_BODY))
    assert src.base_url == "https://circleci.com"


def test_missing_project_slug_raises() -> None:
    with pytest.raises(ValueError):
        CircleCiSource({}, http_get=_FakeTransport(200, {}))


def test_query_normalizes_records() -> None:
    src = _make(_FakeTransport(200, _PIPELINE_BODY))
    records = src.query({})
    assert len(records) == 2

    first = records[0]
    assert first["kind"] == "pipeline"
    assert first["source"] == f"circleci:{_SLUG}"
    assert first["level_or_status"] == "created"
    assert first["message"] == "abc123def @ main"
    assert first["value"] is None
    assert first["labels"] == {"id": "pipe-1", "number": 42}
    assert first["raw"] is _PIPELINE_BODY["items"][0]

    expected = datetime(2026, 6, 10, 8, 0, 0, 500000, tzinfo=UTC).timestamp()
    assert first["ts"] == expected

    assert records[1]["level_or_status"] == "errored"
    assert records[1]["message"] == "999 @ release"


def test_auth_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIRCLE_TOKEN", "circ-secret")
    t = _FakeTransport(200, _PIPELINE_BODY)
    _make(t).query({})
    assert t.headers is not None
    assert t.headers["Circle-Token"] == "circ-secret"


def test_no_auth_header_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CIRCLE_TOKEN", raising=False)
    t = _FakeTransport(200, _PIPELINE_BODY)
    _make(t).query({})
    assert t.headers is not None
    assert "Circle-Token" not in t.headers


def test_custom_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_CIRCLE", "xyz")
    t = _FakeTransport(200, _PIPELINE_BODY)
    _make(t, token_env="MY_CIRCLE").query({})
    assert t.headers is not None
    assert t.headers["Circle-Token"] == "xyz"


def test_query_url() -> None:
    t = _FakeTransport(200, _PIPELINE_BODY)
    _make(t).query({})
    assert t.url is not None
    # The slug contains slashes which are percent-encoded into a single path
    # segment so the API receives the project as one component.
    assert t.url.endswith("/api/v2/project/gh%2Facme%2Fwidgets/pipeline")


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/api",
        "http://127.0.0.1",
        "http://169.254.169.254",
        "http://172.16.0.1",
        "file:///etc/passwd",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        CircleCiSource(
            {"project_slug": _SLUG, "base_url": bad_url},
            http_get=_FakeTransport(200, {}),
        )


def test_health_ok() -> None:
    src = _make(_FakeTransport(200, _PIPELINE_BODY))
    assert src.health() == {"ok": True, "detail": "HTTP 200"}


def test_health_not_ok() -> None:
    src = _make(_FakeTransport(404, {"message": "not found"}))
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "HTTP 404"


def test_health_never_raises() -> None:
    def boom(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        raise RuntimeError("dns failure")

    src = cast(Any, _make)(boom)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


def test_query_and_workflow_fetch_never_raise_on_transport_failure() -> None:
    def boom(_url: str, _headers: dict[str, str]) -> tuple[int, Any]:
        raise RuntimeError("network failed")

    src = CircleCiSource({"project_slug": _SLUG}, http_get=boom)

    assert src.query({}) == []
    assert src.fetch_workflows("pipeline") == []


def test_query_empty_on_non_dict_body() -> None:
    src = _make(_FakeTransport(200, [1, 2, 3]))
    assert src.query({}) == []


def test_fetch_workflows_normalizes() -> None:
    t = _FakeTransport(200, _WORKFLOW_BODY)
    out = _make(t).fetch_workflows("pipe-1")
    assert len(out) == 1
    wf = out[0]
    assert wf["kind"] == "pipeline"
    assert wf["level_or_status"] == "success"
    assert wf["message"] == "build-test-deploy"
    assert wf["labels"] == {"id": "wf-1", "number": 42}
    expected = datetime(2026, 6, 10, 8, 2, 0, tzinfo=UTC).timestamp()
    assert wf["ts"] == expected
    assert t.url is not None
    assert t.url.endswith("/api/v2/pipeline/pipe-1/workflow")


def test_fetch_workflows_empty_on_error() -> None:
    src = _make(_FakeTransport(500, None))
    assert src.fetch_workflows("pipe-1") == []


@pytest.mark.parametrize("value", (None, 0, object(), "not-a-timestamp"))
def test_parse_ts_rejects_empty_non_string_and_invalid_values(value: object) -> None:
    assert _parse_ts(value) is None


def test_parse_ts_bounds_fraction_and_preserves_negative_timezone() -> None:
    parsed = _parse_ts("2026-06-10T08:00:00.123456789-04:00")

    assert parsed == datetime.fromisoformat("2026-06-10T08:00:00.123456-04:00").timestamp()


def test_parse_ts_accepts_fraction_without_timezone_marker() -> None:
    assert _parse_ts("2026-06-10T08:00:00.123") == datetime.fromisoformat(
        "2026-06-10T08:00:00.123"
    ).timestamp()


def test_parse_ts_drops_non_digit_fraction_before_timezone() -> None:
    assert _parse_ts("2026-06-10T08:00:00.nope+00:00") == datetime.fromisoformat(
        "2026-06-10T08:00:00+00:00"
    ).timestamp()


def test_query_and_workflow_filter_malformed_items() -> None:
    query = _make(_FakeTransport(200, {"items": [None, {"id": "ok", "vcs": []}]})).query({})
    workflows = _make(_FakeTransport(200, {"items": [None, {"id": "wf"}]})).fetch_workflows("p")

    assert [record["labels"]["id"] for record in query] == ["ok"]
    assert [record["labels"]["id"] for record in workflows] == ["wf"]


def test_query_and_workflow_reject_non_list_items() -> None:
    assert _make(_FakeTransport(200, {"items": "invalid"})).query({}) == []
    assert _make(_FakeTransport(200, {"items": "invalid"})).fetch_workflows("p") == []


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (b'{"ok": true}', {"ok": True}),
        (b"not-json", None),
        (b"", None),
    ),
)
def test_default_transport_bounds_json_decoding(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    expected: object,
) -> None:
    response = SimpleNamespace(status_code=200, content=content)

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str, *, headers: dict[str, str]) -> object:
            assert headers["Accept"] == "application/json"
            return response

    monkeypatch.setattr(circleci.httpx, "Client", Client)

    assert circleci._default_http_get("https://circleci.com", {"Accept": "application/json"}) == (
        200,
        expected,
    )
