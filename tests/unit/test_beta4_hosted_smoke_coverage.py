"""Hosted branch regressions for provider smoke diagnostics."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import general_ludd.smoke as smoke
from general_ludd.infra.compute import InferenceEngine


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        text: str | None = None,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text
        self.json_error = json_error

    def json(self) -> object:
        if self.json_error:
            raise ValueError("invalid json")
        return self.payload


def _recorder() -> smoke.SmokeRecorder:
    return smoke.SmokeRecorder(
        "test-provider",
        "model-ping",
        mode="live",
        estimated_cost_usd=0.0,
        coverage_depth="functional",
        functional_scope=("chat_request",),
    )


def _spec() -> smoke.SmokeSpec:
    return smoke.SmokeSpec(
        provider="test-provider",
        test="model-ping",
        category="model-api",
        description="test",
        required_env=(("TEST_PROVIDER_TOKEN",),),
        endpoint="https://example.test/v1",
        model="test-model",
    )


def test_probe_endpoint_failure_is_observable_and_bounded() -> None:
    def _failed_get(_url: str, _timeout: float) -> object:
        raise TimeoutError("bounded timeout")

    recorder = _recorder()
    result = smoke._probe_endpoint(
        recorder,
        kind="health",
        url="https://example.test/health",
        timeout=0.1,
        http_get=_failed_get,
    )

    assert result == {
        "status_code": None,
        "elapsed_ms": None,
        "json": None,
        "text": "",
        "raw_text": "",
    }
    assert recorder.report["metrics"]["http_requests"] == 1
    assert any(event["name"] == "endpoint.probe.error" for event in recorder.report["events"])


def test_endpoint_and_response_helpers_cover_all_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMOKE_BASE", "https://localhost:11434")
    assert smoke._chat_completions_endpoint(
        "https://example.test/v1/chat/completions"
    ) == "https://example.test/v1/chat/completions"
    assert smoke._endpoint_root("https://example.test/models") == "https://example.test"
    assert smoke._endpoint_root("https://example.test/root") == "https://example.test/root"
    assert smoke._expand_endpoint("$SMOKE_BASE/v1/models") == (
        "https://localhost:11434/v1/models"
    )
    assert smoke._models_endpoint("") is None
    assert smoke._models_endpoint("https://example.test/chat/completions") is None

    assert smoke._response_text(_Response(200, None, text="body")) == "body"
    assert smoke._response_text(_Response(200, None)) == ""
    assert '"models"' in smoke._response_text(
        _Response(200, {"models": ["a"]})
    )
    assert "object" in smoke._response_text(
        _Response(200, {"value": object()})
    )
    assert smoke._text_snippet("x" * 2001).endswith("...<truncated>")
    assert smoke._response_json(_Response(200, None, json_error=True)) is None
    assert smoke._elapsed_ms(
        SimpleNamespace(elapsed=SimpleNamespace(total_seconds=lambda: 0.25))
    ) == 250.0


def test_payload_metric_and_redaction_helpers_cover_nested_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "id": "root",
        "data": [
            {"model": "child"},
            {"name": "root"},
            [{"id": "nested"}],
        ],
    }
    assert smoke._extract_model_ids(payload) == ["root", "child", "nested"]
    assert smoke._extract_metric_names(
        "# HELP ignored\n\nvllm_requests_total 1\n"
        "process_cpu_seconds_total{pid=\"1\"} 2\n"
        "vllm_requests_total 3\n"
    ) == ["vllm_requests_total", "process_cpu_seconds_total"]
    assert smoke._engine_metric_seen(
        InferenceEngine.LLAMACPP, ["llama_tokens_total"]
    )
    assert not smoke._engine_metric_seen(
        InferenceEngine.LLAMACPP, ["other_total"]
    )
    assert smoke._has_completion("not-a-mapping") is False
    assert smoke._has_completion({"content": ["ok"]}) is True
    assert smoke._count_models(_Response(200, None, json_error=True)) == 0
    assert smoke._count_models(_Response(200, ["a", "b"])) == 2
    assert smoke._redact(("safe", "token-secret-value")) == (
        "safe",
        "<redacted>",
    )
    def _invalid_url(_url: str) -> object:
        raise ValueError("invalid url")

    with monkeypatch.context() as scoped:
        scoped.setattr(httpx, "URL", _invalid_url)
        assert smoke._sanitize_url("invalid") == "<invalid-url>"
    assert "user:pass" not in smoke._sanitize_url(
        "https://user:pass@example.test/path"
    )


def test_live_model_ping_failure_and_success_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _recorder()
    smoke._run_live_model_ping(
        missing,
        _spec(),
        base_url=None,
        model=None,
        timeout=0.1,
        http_post=lambda *_args: _Response(200, {}),
    )
    assert missing.report["status"] == "fail"

    monkeypatch.setenv("TEST_PROVIDER_TOKEN", "test-token-1234567890")

    def _failed_post(
        _url: str,
        _headers: dict[str, str],
        _payload: dict[str, object],
        _timeout: float,
    ) -> object:
        raise TimeoutError("request timed out")

    failed = _recorder()
    smoke._run_live_model_ping(
        failed,
        _spec(),
        base_url=None,
        model=None,
        timeout=0.1,
        http_post=_failed_post,
    )
    assert failed.report["status"] == "fail"

    rejected = _recorder()
    smoke._run_live_model_ping(
        rejected,
        _spec(),
        base_url=None,
        model=None,
        timeout=0.1,
        http_post=lambda *_args: _Response(401, {}),
    )
    assert rejected.report["status"] == "auth_rejected"

    unavailable = _recorder()
    smoke._run_live_model_ping(
        unavailable,
        _spec(),
        base_url=None,
        model=None,
        timeout=0.1,
        http_post=lambda *_args: _Response(503, {}),
    )
    assert unavailable.report["status"] == "fail"

    succeeded = _recorder()
    smoke._run_live_model_ping(
        succeeded,
        _spec(),
        base_url=None,
        model=None,
        timeout=0.1,
        http_post=lambda *_args: _Response(200, {"content": ["OK"]}),
    )
    assert succeeded.report["status"] == "pass"
    assert succeeded.report["metrics"]["completion_seen"] == 1
