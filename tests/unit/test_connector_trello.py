"""Unit tests for the self-contained Trello task-board connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.trello import TrelloSource, TRELLO_BASE_URL

BOARD_ID = "board-uuid-abc"
KEY_ENV = "TRELLO_KEY"
TOKEN_ENV = "TRELLO_TOKEN"


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
            return FakeResponse(200, [])
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


CANNED_CARDS = [
    {
        "id": "card-1",
        "name": "Fix login bug",
        "desc": "Users cannot authenticate.",
        "closed": False,
        "idList": "list-1",
        "due": "2026-07-13T12:00:00.000Z",
        "dateLastActivity": "2026-07-12T10:00:00.000Z",
        "url": "https://trello.com/c/card-1",
    },
    {
        "id": "card-2",
        "name": "Add rate limiting",
        "desc": "Throttle API requests.",
        "closed": False,
        "idList": "list-1",
        "due": None,
        "dateLastActivity": "2026-07-12T11:00:00.000Z",
        "url": "https://trello.com/c/card-2",
    },
]


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv(KEY_ENV, "trello-api-key-secret")
    monkeypatch.setenv(TOKEN_ENV, "trello-api-token-secret")
    return {"key": "trello-api-key-secret", "token": "trello-api-token-secret"}


def _src(transport: Any, **extra: Any) -> TrelloSource:
    cfg: dict[str, Any] = {"board_id": BOARD_ID, "key_env": KEY_ENV, "token_env": TOKEN_ENV}
    cfg.update(extra)
    return TrelloSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert TrelloSource.KIND == "tasks"
    src = TrelloSource(
        {"board_id": BOARD_ID, "key_env": KEY_ENV, "token_env": TOKEN_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.name == "trello"


def test_requires_board_id() -> None:
    with pytest.raises(ValueError, match="board_id"):
        TrelloSource(
            {"key_env": KEY_ENV, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


def test_default_base_url() -> None:
    src = TrelloSource(
        {"board_id": BOARD_ID, "key_env": KEY_ENV, "token_env": TOKEN_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.base_url == TRELLO_BASE_URL


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
        TrelloSource(
            {
                "board_id": BOARD_ID,
                "key_env": KEY_ENV,
                "token_env": TOKEN_ENV,
                "base_url": bad,
            },
            transport=lambda *a, **k: None,
        )


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        TrelloSource(
            {
                "board_id": BOARD_ID,
                "key_env": KEY_ENV,
                "token_env": TOKEN_ENV,
                "base_url": "file:///etc/passwd",
            },
            transport=lambda *a, **k: None,
        )


# -- auth -----------------------------------------------------------------


def test_auth_params_in_request(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_CARDS)])
    _src(transport).query()
    sent_params = transport.calls[0]["params"]
    assert sent_params["key"] == creds["key"]
    assert sent_params["token"] == creds["token"]


def test_missing_key_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setenv(TOKEN_ENV, "t")
    transport = RecordingTransport([FakeResponse(200, CANNED_CARDS)])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query()


def test_missing_token_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "k")
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, CANNED_CARDS)])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query()


def test_auth_not_in_url(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_CARDS)])
    _src(transport).query()
    call = transport.calls[0]
    assert creds["key"] not in call["url"]
    assert creds["token"] not in call["url"]


# -- query / normalization -------------------------------------------------


def test_query_normalizes(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_CARDS)])
    rows = _src(transport).query()

    assert len(rows) == 2
    first = rows[0]
    assert first["ts"] == "2026-07-12T10:00:00.000Z"
    assert first["source"] == "trello"
    assert first["kind"] == "tasks"
    assert first["message"] == "Fix login bug"
    assert first["value"] == 1
    assert first["labels"] == {
        "id": "card-1",
        "idList": "list-1",
        "due": "2026-07-13T12:00:00.000Z",
        "closed": False,
    }
    assert first["raw"]["id"] == "card-1"

    second = rows[1]
    assert second["level_or_status"] == "open"


def test_query_uses_board_cards_url(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, [])])
    _src(transport).query()
    assert transport.calls[0]["url"] == f"{TRELLO_BASE_URL}/boards/{BOARD_ID}/cards"


def test_query_uses_list_cards_url(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, [])])
    _src(transport).query({"list_id": "list-xyz"})
    assert transport.calls[0]["url"] == f"{TRELLO_BASE_URL}/lists/list-xyz/cards"


def test_query_raises_on_http_error(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query()


def test_query_raises_on_4xx(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(404, {})])
    with pytest.raises(RuntimeError, match="HTTP 404"):
        _src(transport).query()


# -- level_or_status -------------------------------------------------------


def test_closed_card_status(creds: dict[str, str]) -> None:
    card = dict(CANNED_CARDS[0], closed=True, due=None)
    transport = RecordingTransport([FakeResponse(200, [card])])
    rows = _src(transport).query()
    assert rows[0]["level_or_status"] == "closed"


def test_due_soon_status(creds: dict[str, str]) -> None:
    from datetime import datetime, timedelta, timezone

    tomorrow = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    card = dict(CANNED_CARDS[0], due=tomorrow, closed=False)
    transport = RecordingTransport([FakeResponse(200, [card])])
    rows = _src(transport).query()
    assert rows[0]["level_or_status"] == "due_soon"


def test_open_status_no_due(creds: dict[str, str]) -> None:
    card = dict(CANNED_CARDS[0], due=None, closed=False)
    transport = RecordingTransport([FakeResponse(200, [card])])
    rows = _src(transport).query()
    assert rows[0]["level_or_status"] == "open"


# -- pagination ------------------------------------------------------------


def test_pagination_follows_before(creds: dict[str, str]) -> None:
    page1 = FakeResponse(200, [CANNED_CARDS[0]])
    page2 = FakeResponse(200, [CANNED_CARDS[1]])
    transport = RecordingTransport([page1, page2])
    rows = _src(transport, page_size=1).query()
    assert len(rows) == 2
    assert len(transport.calls) == 3
    assert transport.calls[1]["params"]["before"] == "card-1"


def test_pagination_bounded_by_max_pages(creds: dict[str, str]) -> None:
    looping = FakeResponse(200, [CANNED_CARDS[0]])
    transport = RecordingTransport([looping] * 50)
    rows = _src(transport, max_pages=3, page_size=1).query()
    assert len(transport.calls) == 3
    assert len(rows) == 3


def test_pagination_stops_on_small_page(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, [CANNED_CARDS[0]])])
    rows = _src(transport, page_size=50).query()
    assert len(rows) == 1
    assert len(transport.calls) == 1


# -- health ---------------------------------------------------------------


def test_health_ok(creds: dict[str, str]) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, {"id": BOARD_ID, "name": "Test Board"})]
    )
    h = _src(transport).health()
    assert h["ok"] is True
    assert "trello reachable" in h["detail"]


def test_health_probes_board_endpoint(creds: dict[str, str]) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, {"id": BOARD_ID, "name": "Test Board"})]
    )
    _src(transport).health()
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{TRELLO_BASE_URL}/boards/{BOARD_ID}"
    assert "key" in call["params"]
    assert "token" in call["params"]
    assert call["params"]["cards"] == "none"


def test_health_not_ok_on_4xx(creds: dict[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(403, {"error": "forbidden"})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "trello HTTP 403"


def test_health_never_raises(creds: dict[str, str]) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"
