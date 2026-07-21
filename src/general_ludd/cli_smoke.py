"""Provider smoke-test CLI helpers."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

import httpx

from general_ludd.models.provider_presets import (
    PROVIDER_FLAGSHIP_MODELS,
    PROVIDER_PRESETS,
)

SMOKE_TESTS = ("config", "models", "chat", "all")
OPENAI_COMPATIBLE_CLASS = "ChatOpenAI"


def add_smoke_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    """Register the top-level ``gludd smoke`` command."""
    smoke = sub.add_parser("smoke", help="Run a provider smoke test")
    smoke.add_argument("provider", help="Provider key, or 'all'")
    smoke.add_argument("test", choices=SMOKE_TESTS, help="Smoke test: config, models, chat, or all")
    smoke.add_argument("--model", default=None, help="Override the default flagship model")
    smoke.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds")
    smoke.add_argument("--dry-run", action="store_true", help="Prepare the live request without sending it")
    smoke.add_argument("--include-request", action="store_true", help="Include redacted request details in JSON output")
    smoke.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    smoke.set_defaults(func=_cmd_smoke)
    return smoke


def _cmd_smoke(args: argparse.Namespace) -> None:
    """Run provider smoke checks and print logs, metrics, and events."""
    payload = run_smoke_command(
        provider=str(args.provider),
        test=str(args.test),
        model=args.model if isinstance(args.model, str) else None,
        timeout=float(args.timeout),
        dry_run=bool(args.dry_run),
        include_request=bool(args.include_request),
        environ=dict(os.environ),
    )
    if bool(args.json):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
    code = _exit_code(payload)
    if code:
        sys.exit(code)


def run_smoke_command(
    *,
    provider: str,
    test: str,
    model: str | None,
    timeout: float,
    dry_run: bool,
    include_request: bool,
    environ: dict[str, str],
) -> dict[str, object]:
    """Run one or more provider smoke checks."""
    provider_names = _provider_names(provider)
    test_names = _test_names(test)
    results: list[dict[str, object]] = []
    if not provider_names:
        results.append(_unknown_provider_result(provider))
    else:
        for provider_name in provider_names:
            for test_name in test_names:
                results.append(_run_one(
                    provider_name=provider_name,
                    test_name=test_name,
                    model=model,
                    timeout=timeout,
                    dry_run=dry_run,
                    include_request=include_request,
                    environ=environ,
                ))
    return {
        "ok": all(bool(result["ok"]) for result in results if result["status"] != "skipped"),
        "summary": _summary(results),
        "results": results,
    }


def _provider_names(provider: str) -> list[str]:
    normalized = provider.lower()
    if normalized == "all":
        return sorted(PROVIDER_PRESETS)
    if normalized in PROVIDER_PRESETS:
        return [normalized]
    return []


def _test_names(test: str) -> tuple[str, ...]:
    if test == "all":
        return ("config", "models", "chat")
    return (test,)


def _run_one(
    *,
    provider_name: str,
    test_name: str,
    model: str | None,
    timeout: float,
    dry_run: bool,
    include_request: bool,
    environ: dict[str, str],
) -> dict[str, object]:
    started = time.monotonic()
    preset = PROVIDER_PRESETS[provider_name]
    resolved_model = model or PROVIDER_FLAGSHIP_MODELS.get(provider_name)
    result = _base_result(provider_name, test_name)
    _event(result, "smoke.started")
    result["metrics"] = {
        "configured_providers_total": len(PROVIDER_PRESETS),
        "known_providers_total": len(PROVIDER_PRESETS),
        "duration_ms": 0,
    }
    if test_name == "config":
        _run_config_smoke(result, preset, resolved_model, environ)
    elif test_name == "models":
        _run_models_smoke(result, preset, timeout)
    elif test_name == "chat":
        _run_chat_smoke(result, preset, resolved_model, timeout, dry_run, include_request, environ)
    else:
        _fail(result, f"unknown smoke test: {test_name}")
    metrics = result["metrics"]
    assert isinstance(metrics, dict)
    metrics["duration_ms"] = int((time.monotonic() - started) * 1000)
    if result["status"] in {"passed", "dry_run"}:
        _event(result, "smoke.completed")
    return result


def _run_config_smoke(
    result: dict[str, object],
    preset: dict[str, object],
    model: str | None,
    environ: dict[str, str],
) -> None:
    required = (
        "api_base_url",
        "provider_package",
        "provider_class",
        "credential_env_var",
        "credential_alias",
        "api_base_alias",
        "display_name",
    )
    missing = [key for key in required if not preset.get(key)]
    metrics = _metrics(result)
    credential_env_var = str(preset.get("credential_env_var", ""))
    metrics["credential_configured"] = int(bool(environ.get(credential_env_var)))
    metrics["has_flagship_model"] = int(bool(model))
    if missing:
        _fail(result, f"provider preset missing required keys: {', '.join(missing)}")
        return
    if not model:
        _fail(result, "provider has no flagship model for smoke probes")
        return
    result["ok"] = True
    result["status"] = "passed"
    _log(result, "info", f"{result['provider']} preset is complete; live credential is optional for config")


def _run_models_smoke(result: dict[str, object], preset: dict[str, object], timeout: float) -> None:
    endpoint = preset.get("free_models_endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        _skip(result, "provider has no catalog/free-models endpoint in its preset")
        return
    _event(result, "smoke.request.prepared")
    try:
        response = httpx.request("GET", endpoint, timeout=timeout)
    except httpx.HTTPError as exc:
        _fail(result, f"catalog request failed: {exc}")
        return
    _record_response_metrics(result, response)
    if not 200 <= response.status_code < 300:
        _fail(result, f"catalog endpoint returned HTTP {response.status_code}")
        return
    payload = _safe_json(response)
    _metrics(result)["catalog_models_count"] = _count_models(payload)
    result["ok"] = True
    result["status"] = "passed"
    _log(result, "info", "catalog endpoint responded with parseable JSON")


def _run_chat_smoke(
    result: dict[str, object],
    preset: dict[str, object],
    model: str | None,
    timeout: float,
    dry_run: bool,
    include_request: bool,
    environ: dict[str, str],
) -> None:
    if not model:
        _fail(result, "provider has no flagship model for chat smoke")
        return
    provider_class = str(preset.get("provider_class", ""))
    if provider_class not in {OPENAI_COMPATIBLE_CLASS, "ChatAnthropic"}:
        _skip(result, f"{provider_class} does not have a direct low-cost chat probe yet")
        return
    credential_env_var = str(preset["credential_env_var"])
    credential = environ.get(credential_env_var)
    if not credential:
        _skip(result, f"{credential_env_var} is not set; skipping live one-token chat smoke")
        return
    _event(result, "smoke.credential.detected")
    request = _build_chat_request(preset, model, credential, timeout)
    if include_request or dry_run:
        result["request"] = _redact_request(request)
    _metrics(result)["estimated_completion_tokens"] = 1
    _event(result, "smoke.request.prepared")
    if dry_run:
        result["ok"] = True
        result["status"] = "dry_run"
        _log(result, "info", "dry run prepared one-token chat request without network")
        return
    try:
        response = httpx.request(
            request["method"],
            request["url"],
            headers=request["headers"],
            json=request["json"],
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        _fail(result, f"chat request failed: {exc}")
        return
    _record_response_metrics(result, response)
    if not 200 <= response.status_code < 300:
        _fail(result, f"chat endpoint returned HTTP {response.status_code}")
        return
    payload = _safe_json(response)
    _record_usage_metrics(result, payload)
    if not _has_completion(payload):
        _fail(result, "chat response did not contain a completion")
        return
    result["ok"] = True
    result["status"] = "passed"
    _log(result, "info", "one-token chat probe returned a completion")


def _build_chat_request(
    preset: dict[str, object],
    model: str,
    credential: str,
    timeout: float,
) -> dict[str, Any]:
    provider_class = str(preset.get("provider_class", ""))
    base_url = str(preset["api_base_url"]).rstrip("/")
    if provider_class == "ChatAnthropic":
        return {
            "method": "POST",
            "url": f"{base_url}/messages",
            "headers": {
                "x-api-key": credential,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            "json": {
                "model": model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            },
            "timeout": timeout,
        }
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    return {
        "method": "POST",
        "url": url,
        "headers": {
            "Authorization": f"Bearer {credential}",
            "content-type": "application/json",
        },
        "json": {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        },
        "timeout": timeout,
    }


def _redact_request(request: dict[str, Any]) -> dict[str, object]:
    headers = {
        str(key): ("Bearer <redacted>" if str(key).lower() == "authorization" else "<redacted>")
        for key in request["headers"]
    }
    return {
        "method": request["method"],
        "url": request["url"],
        "headers": headers,
        "json": request["json"],
        "timeout": request["timeout"],
    }


def _record_response_metrics(result: dict[str, object], response: httpx.Response) -> None:
    metrics = _metrics(result)
    metrics["http_status_code"] = response.status_code
    metrics["response_bytes"] = len(response.content)


def _record_usage_metrics(result: dict[str, object], payload: object) -> None:
    if not isinstance(payload, dict):
        return
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    metrics = _metrics(result)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int | float):
            metrics[f"usage_{key}"] = value


def _safe_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _count_models(payload: object) -> int:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return len(data)
        models = payload.get("models")
        if isinstance(models, list):
            return len(models)
    if isinstance(payload, list):
        return len(payload)
    return 0


def _has_completion(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        return True
    content = payload.get("content")
    return isinstance(content, list) and bool(content)


def _unknown_provider_result(provider: str) -> dict[str, object]:
    result = _base_result(provider, "config")
    result["metrics"] = {"known_providers_total": len(PROVIDER_PRESETS), "duration_ms": 0}
    _event(result, "smoke.started")
    _fail(result, f"unknown provider: {provider}")
    return result


def _base_result(provider: str, test: str) -> dict[str, object]:
    return {
        "provider": provider,
        "test": test,
        "ok": False,
        "status": "failed",
        "logs": [],
        "metrics": {},
        "events": [],
    }


def _summary(results: Sequence[dict[str, object]]) -> dict[str, int]:
    passed = sum(1 for result in results if result["status"] in {"passed", "dry_run"})
    skipped = sum(1 for result in results if result["status"] == "skipped")
    failed = sum(1 for result in results if result["status"] == "failed")
    return {"total": len(results), "passed": passed, "skipped": skipped, "failed": failed}


def _exit_code(payload: dict[str, object]) -> int:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    if int(summary["failed"]):
        return 1
    if int(summary["passed"]) == 0 and int(summary["skipped"]):
        return 2
    return 0


def _metrics(result: dict[str, object]) -> dict[str, object]:
    metrics = result["metrics"]
    assert isinstance(metrics, dict)
    return metrics


def _log(result: dict[str, object], level: str, message: str) -> None:
    logs = result["logs"]
    assert isinstance(logs, list)
    logs.append({"level": level, "message": message})


def _event(result: dict[str, object], name: str) -> None:
    events = result["events"]
    assert isinstance(events, list)
    events.append({"name": name, "time_ms": int(time.time() * 1000)})


def _fail(result: dict[str, object], message: str) -> None:
    result["ok"] = False
    result["status"] = "failed"
    _log(result, "error", message)


def _skip(result: dict[str, object], message: str) -> None:
    result["ok"] = False
    result["status"] = "skipped"
    _log(result, "warning", message)


def _print_human(payload: dict[str, object]) -> None:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    print(
        "provider smoke: "
        f"{summary['passed']} passed, {summary['skipped']} skipped, {summary['failed']} failed"
    )
    results = payload["results"]
    assert isinstance(results, list)
    for result in results:
        assert isinstance(result, dict)
        print(f"{result['provider']} {result['test']}: {result['status']}")
        logs = result["logs"]
        assert isinstance(logs, list)
        for entry in logs:
            assert isinstance(entry, dict)
            print(f"  {entry['level']}: {entry['message']}")
