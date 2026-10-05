"""Typed transport and output coverage for the account CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from general_ludd import cli_account


class _Response:
    """Minimal httpx response surface."""

    def __init__(
        self,
        status_code: int,
        *,
        content: bytes = b"body",
        text: str = "body",
        payload: object = None,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.text = text
        self._payload = payload
        self._json_error = json_error

    def json(self) -> object:
        """Return JSON or model a decoding failure."""
        if self._json_error:
            raise ValueError("invalid json")
        return self._payload


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_Response(204), None),
        (_Response(200, content=b""), None),
        (_Response(200, payload={"ok": True}), {"ok": True}),
        (_Response(200, text="plain", json_error=True), "plain"),
    ],
)
def test_http_success_shapes(response: _Response, expected: object) -> None:
    """Decode JSON, text, empty, and no-content daemon responses."""
    with patch.object(httpx, "request", return_value=response):
        assert cli_account._http("GET", "http://daemon/account") == expected


def test_http_transport_and_status_failures_exit_observably(capsys: pytest.CaptureFixture[str]) -> None:
    """Convert connection and HTTP failures into bounded CLI exits."""
    with (
        patch.object(httpx, "request", side_effect=RuntimeError("offline")),
        pytest.raises(SystemExit) as transport_exit,
    ):
        cli_account._http("GET", "http://daemon/account")
    assert transport_exit.value.code == 1
    assert "offline" in capsys.readouterr().err

    with (
        patch.object(httpx, "request", return_value=_Response(503, text="unavailable")),
        pytest.raises(SystemExit) as status_exit,
    ):
        cli_account._http("GET", "http://daemon/account")
    assert status_exit.value.code == 1
    assert "503 unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("handler", "args", "body", "expected"),
    [
        (
            cli_account._cmd_delete,
            argparse.Namespace(daemon_url="http://daemon", user_id="u", confirm=True, json=False),
            {
                "user_id": "u",
                "deleted_at": "now",
                "todos_deleted": 1,
                "returns_deleted": 2,
                "memory_deleted": 3,
                "settings_namespaces_deleted": 4,
            },
            "todos_deleted",
        ),
        (
            cli_account._cmd_delete,
            argparse.Namespace(daemon_url="http://daemon", user_id="u", confirm=True, json=False),
            "deleted",
            "account deleted",
        ),
        (
            cli_account._cmd_policy,
            argparse.Namespace(daemon_url="http://daemon", service="aws", json=False),
            "policy unavailable",
            "policy unavailable",
        ),
        (
            cli_account._cmd_create,
            argparse.Namespace(daemon_url="http://daemon", provider="aws", budget=1.0, ephemeral=False, json=False),
            "created",
            "created",
        ),
        (
            cli_account._cmd_cleanup,
            argparse.Namespace(daemon_url="http://daemon", json=False),
            "cleaned",
            "cleaned",
        ),
    ],
)
def test_text_handlers_cover_mapping_and_scalar_responses(
    handler: Callable[[argparse.Namespace], None],
    args: argparse.Namespace,
    body: object,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render both structured and scalar account responses."""
    with patch.object(cli_account, "_http", return_value=body):
        handler(args)
    assert expected in capsys.readouterr().out
