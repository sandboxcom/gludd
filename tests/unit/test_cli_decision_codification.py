"""Tests for the bounded decision-codification operator CLI."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import cast

import httpx
import pytest

from general_ludd.cli_decision_codification import (
    MAX_ANALYSIS_RESPONSE_BYTES,
    _AnalysisUnavailable,
    _cmd_analyze,
    _post_analysis,
    _request_payload,
    add_decision_codification_subparser,
)

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64
_DIGEST_D = "sha256:" + "d" * 64
_DIGEST_E = "sha256:" + "e" * 64


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "project": "project-alpha",
        "run_ids": ["run-1", "run_2"],
        "training_recipe_digest": _DIGEST_A,
        "dependency_lock_digest": _DIGEST_B,
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-02-01T00:00:00Z",
        "maximum_use_count": 25,
        "estimated_tokens_per_call": 2048,
        "daemon_url": "http://localhost:8000",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _response_payload() -> dict[str, object]:
    return {
        "bundles_read": 2,
        "events_seen": 9,
        "events_eligible": 4,
        "candidate_count": 1,
        "candidates": [
            {
                "cluster_digest": _DIGEST_A,
                "evidence_count": 2,
                "candidate_digest": _DIGEST_C,
                "validation_report_digest": _DIGEST_D,
                "holdout_report_digest": _DIGEST_E,
            }
        ],
        "rejection_counts": [{"reason": "unsigned_bundle", "count": 1}],
    }


@dataclass
class _StreamResponse:
    status_code: int = 200
    body: bytes = field(default_factory=lambda: json.dumps(_response_payload()).encode())
    headers: dict[str, str] = field(default_factory=dict)
    chunks: list[bytes] | None = None

    def __enter__(self) -> _StreamResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def iter_bytes(self) -> Iterator[bytes]:
        yield from self.chunks if self.chunks is not None else [self.body]


@dataclass
class _Client:
    response: _StreamResponse
    calls: list[dict[str, object]] = field(default_factory=list)

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: object) -> _StreamResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def _install_client(monkeypatch: pytest.MonkeyPatch, response: _StreamResponse) -> _Client:
    client = _Client(response)
    monkeypatch.setattr(
        "general_ludd.cli_decision_codification.httpx.Client",
        lambda **_kwargs: client,
    )
    return client


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("parser has no subcommands")


def test_parser_registers_only_analysis_with_exact_inputs() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_decision_codification_subparser(subparsers)

    namespace = parser.parse_args(
        [
            "decision-codification",
            "analyze",
            "--project",
            "project-alpha",
            "--run-id",
            "run-1",
            "--run-id",
            "run-2",
            "--training-recipe-digest",
            _DIGEST_A,
            "--dependency-lock-digest",
            _DIGEST_B,
            "--created-at",
            "2026-01-01T00:00:00Z",
            "--expires-at",
            "2026-02-01T00:00:00Z",
            "--maximum-use-count",
            "25",
        ]
    )

    command_parser = subparsers.choices["decision-codification"]
    assert set(_subcommands(command_parser)) == {"analyze"}
    assert namespace.func is _cmd_analyze
    assert namespace.project == "project-alpha"
    assert namespace.run_ids == ["run-1", "run-2"]
    assert namespace.estimated_tokens_per_call == 0
    assert namespace.daemon_url == "http://localhost:8000"


def test_unified_parser_registers_decision_codification() -> None:
    from general_ludd.cli import build_parser

    parser, subcommand_map = build_parser()
    namespace = parser.parse_args(["decision-codification"])

    assert namespace.command == "decision-codification"
    assert "decision-codification" in subcommand_map


def test_request_payload_preserves_only_bounded_api_fields() -> None:
    payload = _request_payload(_args())

    assert payload == {
        "project_id": "project-alpha",
        "run_ids": ["run-1", "run_2"],
        "training_recipe_digest": _DIGEST_A,
        "dependency_lock_digest": _DIGEST_B,
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-02-01T00:00:00Z",
        "maximum_use_count": 25,
        "estimated_tokens_per_call": 2048,
    }


@pytest.mark.parametrize(
    ("overrides", "sensitive_value"),
    [
        ({"project": "../project"}, "../project"),
        ({"run_ids": ["run-1", "run-1"]}, "run-1"),
        ({"run_ids": ["../unsafe"]}, "../unsafe"),
        ({"run_ids": [f"run-{number}" for number in range(257)]}, "run-256"),
        ({"training_recipe_digest": "raw-key-material"}, "raw-key-material"),
        ({"created_at": "2026-01-01T00:00:00"}, "2026-01-01T00:00:00"),
        ({"expires_at": "2028-01-01T00:00:00Z"}, "2028-01-01T00:00:00Z"),
        ({"maximum_use_count": 1_000_001}, "1000001"),
        ({"estimated_tokens_per_call": 10_000_001}, "10000001"),
    ],
)
def test_invalid_requests_fail_before_http_without_reflection(
    overrides: dict[str, object],
    sensitive_value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args(**overrides))

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert captured.err == "Error: invalid decision analysis request\n"
    assert sensitive_value not in captured.err


def test_success_posts_project_scoped_request_and_prints_only_safe_projection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _install_client(monkeypatch, _StreamResponse())
    monkeypatch.setenv("GLUDD_AUTH_PSK", "super-secret-psk")

    _cmd_analyze(_args(daemon_url="http://daemon.internal:8000/"))

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert captured.err == ""
    assert output == _response_payload()
    assert "super-secret-psk" not in captured.out
    assert client.calls == [
        {
            "method": "POST",
            "url": "http://daemon.internal:8000/api/v1/decision-codification/analyze",
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer project-alpha:super-secret-psk",
            },
            "json": _request_payload(_args()),
        }
    ]


def test_auth_header_is_omitted_when_psk_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _install_client(monkeypatch, _StreamResponse())
    monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)

    _cmd_analyze(_args())

    capsys.readouterr()
    headers = client.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert "Authorization" not in headers


def test_valid_declared_response_length_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = json.dumps(_response_payload()).encode()
    _install_client(
        monkeypatch,
        _StreamResponse(body=body, headers={"content-length": str(len(body))}),
    )

    _cmd_analyze(_args())

    assert json.loads(capsys.readouterr().out) == _response_payload()


@pytest.mark.parametrize("status_code", [300, 400, 401, 404, 422, 500, 503])
def test_non_2xx_errors_never_reflect_backend_body(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "backend-secret-and-request-content"
    _install_client(
        monkeypatch,
        _StreamResponse(status_code=status_code, body=secret.encode()),
    )

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == f"Error: decision analysis request failed (HTTP {status_code})\n"
    assert secret not in captured.err


@pytest.mark.parametrize("content_length", ["not-an-integer", "-1"])
def test_invalid_content_length_fails_closed(
    content_length: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_client(
        monkeypatch,
        _StreamResponse(headers={"content-length": content_length}),
    )

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err == "Error: invalid decision analysis response\n"
    assert content_length not in captured.err


@pytest.mark.parametrize("status_code", [99, 600, cast(int, "200")])
def test_invalid_status_value_fails_closed(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_client(monkeypatch, _StreamResponse(status_code=status_code))

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err == "Error: invalid decision analysis response\n"


def test_non_text_internal_project_scope_fails_before_transport() -> None:
    with pytest.raises(_AnalysisUnavailable):
        _post_analysis({"project_id": 7}, daemon_url="http://localhost:8000")


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"bundles_read":1,"bundles_read":2}',
        json.dumps({**_response_payload(), "private_evidence": "do-not-print"}).encode(),
        json.dumps({**_response_payload(), "candidate_count": 2}).encode(),
        json.dumps(
            {
                **_response_payload(),
                "rejection_counts": [{"reason": "backend_free_text", "count": 1}],
            }
        ).encode(),
    ],
)
def test_malformed_or_unsafe_response_fails_closed_without_reflection(
    body: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_client(monkeypatch, _StreamResponse(body=body))

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "Error: invalid decision analysis response\n"
    assert "do-not-print" not in captured.err
    assert "backend_free_text" not in captured.err


def test_declared_oversize_response_fails_before_reading_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = _StreamResponse(
        headers={"content-length": str(MAX_ANALYSIS_RESPONSE_BYTES + 1)},
        chunks=[b"must-not-be-read"],
    )
    _install_client(monkeypatch, response)

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err == "Error: decision analysis response too large\n"


def test_streamed_oversize_response_fails_without_printing_content(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = b"stream-secret"
    response = _StreamResponse(
        chunks=[b"x" * MAX_ANALYSIS_RESPONSE_BYTES, secret],
    )
    _install_client(monkeypatch, response)

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "Error: decision analysis response too large\n"
    assert secret.decode() not in captured.err


def test_transport_error_is_bounded_and_does_not_reflect_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = httpx.Request("POST", "http://localhost:8000")

    class _FailingClient(_Client):
        def stream(self, method: str, url: str, **kwargs: object) -> _StreamResponse:
            raise httpx.ConnectError("credential=transport-secret", request=request)

    monkeypatch.setattr(
        "general_ludd.cli_decision_codification.httpx.Client",
        lambda **_kwargs: _FailingClient(_StreamResponse()),
    )

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "Error: decision analysis request unavailable\n"
    assert "transport-secret" not in captured.err


def test_parser_exposes_no_lifecycle_or_key_inputs() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_decision_codification_subparser(subparsers)
    analyze = _subcommands(subparsers.choices["decision-codification"])["analyze"]

    destinations = {action.dest for action in analyze._actions}
    forbidden = {
        "approve",
        "activate",
        "artifact",
        "evidence",
        "key",
        "lifecycle",
        "private_key",
        "public_key",
    }
    assert destinations.isdisjoint(forbidden)
