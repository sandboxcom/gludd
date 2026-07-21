"""Provider smoke-test CLI contract tests."""

from __future__ import annotations

import argparse
import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest


def _args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "provider": "openai",
        "test": "config",
        "model": None,
        "timeout": 5.0,
        "dry_run": False,
        "include_request": False,
        "json": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _load_stdout_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


def test_parser_registers_smoke_provider_test_shape() -> None:
    from general_ludd.cli import build_parser

    parser, subcommand_map = build_parser()
    args = parser.parse_args(["smoke", "openai", "chat", "--dry-run", "--json"])

    assert "smoke" in subcommand_map
    assert args.command == "smoke"
    assert args.provider == "openai"
    assert args.test == "chat"
    assert args.dry_run is True
    assert args.func is not None


def test_config_smoke_all_covers_every_provider_with_metrics_and_events(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke
    from general_ludd.models.provider_presets import PROVIDER_PRESETS

    _cmd_smoke(_args(provider="all", test="config"))

    payload = _load_stdout_json(capsys)
    assert payload["ok"] is True
    assert payload["summary"]["total"] == len(PROVIDER_PRESETS)
    assert payload["summary"]["passed"] == len(PROVIDER_PRESETS)
    assert payload["summary"]["failed"] == 0
    assert payload["results"][0]["events"][0]["name"] == "smoke.started"
    assert payload["results"][0]["metrics"]["configured_providers_total"] == len(PROVIDER_PRESETS)


def test_chat_dry_run_prepares_one_token_probe_without_network(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    with patch.dict("os.environ", {"OPENAI_API_KEY": "secret-value"}), patch("httpx.request") as request:
        _cmd_smoke(_args(test="chat", dry_run=True, include_request=True))

    request.assert_not_called()
    payload = _load_stdout_json(capsys)
    result = payload["results"][0]
    assert result["ok"] is True
    assert result["status"] == "dry_run"
    assert result["request"]["headers"]["Authorization"] == "Bearer <redacted>"
    assert result["request"]["json"]["max_tokens"] == 1
    assert result["metrics"]["estimated_completion_tokens"] == 1
    assert [event["name"] for event in result["events"]] == [
        "smoke.started",
        "smoke.credential.detected",
        "smoke.request.prepared",
        "smoke.completed",
    ]


def test_missing_single_chat_credential_exits_skip_with_actionable_log(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    with patch.dict("os.environ", {}, clear=True), pytest.raises(SystemExit) as exc:
        _cmd_smoke(_args(test="chat"))

    assert exc.value.code == 2
    payload = _load_stdout_json(capsys)
    result = payload["results"][0]
    assert result["ok"] is False
    assert result["status"] == "skipped"
    assert "OPENAI_API_KEY" in result["logs"][0]["message"]


def test_models_smoke_uses_catalog_endpoint_and_records_response_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"data": [{"id": "free-model"}]}

    with (
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "secret-value"}),
        patch("httpx.request", return_value=response),
    ):
        _cmd_smoke(_args(provider="openrouter", test="models"))

    payload = _load_stdout_json(capsys)
    result = payload["results"][0]
    assert result["ok"] is True
    assert result["status"] == "passed"
    assert result["metrics"]["http_status_code"] == 200
    assert result["metrics"]["catalog_models_count"] == 1
    assert result["events"][-1]["name"] == "smoke.completed"


def test_unknown_provider_fails_with_known_provider_count(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke
    from general_ludd.models.provider_presets import PROVIDER_PRESETS

    with pytest.raises(SystemExit) as exc:
        _cmd_smoke(_args(provider="nope", test="config"))

    assert exc.value.code == 1
    payload = _load_stdout_json(capsys)
    result = payload["results"][0]
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["metrics"]["known_providers_total"] == len(PROVIDER_PRESETS)


def test_human_output_summarizes_results(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    _cmd_smoke(_args(json=False))

    out = capsys.readouterr().out
    assert "provider smoke: 1 passed, 0 skipped, 0 failed" in out
    assert "openai config: passed" in out


def test_all_test_runs_config_models_and_chat_with_skip_summary() -> None:
    from general_ludd.cli_smoke import run_smoke_command

    payload = run_smoke_command(
        provider="openai",
        test="all",
        model=None,
        timeout=5.0,
        dry_run=False,
        include_request=False,
        environ={},
    )

    assert payload["summary"] == {"total": 3, "passed": 1, "skipped": 2, "failed": 0}
    assert payload["ok"] is True


def test_anthropic_chat_dry_run_uses_native_messages_shape(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "secret-value"}), patch("httpx.request") as request:
        _cmd_smoke(_args(provider="anthropic", test="chat", dry_run=True, include_request=True))

    request.assert_not_called()
    payload = _load_stdout_json(capsys)
    request_payload = payload["results"][0]["request"]
    assert request_payload["url"].endswith("/messages")
    assert request_payload["headers"]["x-api-key"] == "<redacted>"
    assert request_payload["headers"]["anthropic-version"] == "<redacted>"
    assert request_payload["json"]["max_tokens"] == 1


def test_live_chat_success_records_usage_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.content = b'{"choices":[{"message":{"content":"OK"}}]}'
    response.json.return_value = {
        "choices": [{"message": {"content": "OK"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
    }

    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "secret-value"}),
        patch("httpx.request", return_value=response),
    ):
        _cmd_smoke(_args(provider="openai", test="chat"))

    payload = _load_stdout_json(capsys)
    result = payload["results"][0]
    assert result["status"] == "passed"
    assert result["metrics"]["usage_completion_tokens"] == 1
    assert result["metrics"]["usage_total_tokens"] == 5


def test_models_smoke_without_catalog_endpoint_is_skipped(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    with pytest.raises(SystemExit) as exc:
        _cmd_smoke(_args(provider="openai", test="models"))

    assert exc.value.code == 2
    result = _load_stdout_json(capsys)["results"][0]
    assert result["status"] == "skipped"
    assert "no catalog" in result["logs"][0]["message"]


def test_models_smoke_http_error_fails(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    response = MagicMock(spec=httpx.Response)
    response.status_code = 503
    response.content = b"unavailable"
    response.json.side_effect = ValueError("not json")

    with patch("httpx.request", return_value=response), pytest.raises(SystemExit) as exc:
        _cmd_smoke(_args(provider="openrouter", test="models"))

    assert exc.value.code == 1
    result = _load_stdout_json(capsys)["results"][0]
    assert result["status"] == "failed"
    assert result["metrics"]["http_status_code"] == 503


def test_chat_http_error_fails(capsys: pytest.CaptureFixture[str]) -> None:
    from general_ludd.cli_smoke import _cmd_smoke

    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "secret-value"}),
        patch("httpx.request", side_effect=httpx.ConnectError("refused")),
        pytest.raises(SystemExit) as exc,
    ):
        _cmd_smoke(_args(provider="openai", test="chat"))

    assert exc.value.code == 1
    result = _load_stdout_json(capsys)["results"][0]
    assert result["status"] == "failed"
    assert "chat request failed" in result["logs"][0]["message"]
