#!/usr/bin/env python3
"""Credential-safe Azure and RunPod deployment smoke-test harness.

The default mode validates configuration only. ``--live`` performs a read-only
credential check; it never creates or deletes provider resources. When
``GLUDD_INGEST_URL`` and ``GLUDD_INGEST_TOKEN`` are configured, the harness
publishes both a normalized provider event and a log record to Gludd's
``/ingest/webhook`` endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class HarnessConfigError(ValueError):
    """Raised when provider credentials or safety bounds are incomplete."""


def _required(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise HarnessConfigError(f"{name} is required")
    return value


def _optional_float(env: dict[str, str], name: str, default: float | None = None) -> float | None:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise HarnessConfigError(f"{name} must be numeric") from exc
    if value <= 0:
        raise HarnessConfigError(f"{name} must be greater than zero")
    return value


def _azure_config(env: dict[str, str], *, live: bool) -> dict[str, Any]:
    subscription = _required(env, "AZURE_SUBSCRIPTION_ID")
    tenant = _required(env, "AZURE_TENANT_ID")
    client_id = _required(env, "AZURE_CLIENT_ID") if live else env.get("AZURE_CLIENT_ID", "").strip()
    client_secret = (
        _required(env, "AZURE_CLIENT_SECRET") if live else env.get("AZURE_CLIENT_SECRET", "").strip()
    )
    return {
        "subscription_id": subscription,
        "tenant_id": tenant,
        "client_id": client_id,
        "credential_configured": bool(client_id and client_secret),
        "resource_group": env.get("AZURE_RESOURCE_GROUP", "").strip() or None,
        "billing_account_id": env.get("AZURE_BILLING_ACCOUNT_ID", "").strip() or None,
        "billing_profile_id": env.get("AZURE_BILLING_PROFILE_ID", "").strip() or None,
        "invoice_section_id": env.get("AZURE_INVOICE_SECTION_ID", "").strip() or None,
    }


def _runpod_config(env: dict[str, str], *, live: bool) -> dict[str, Any]:
    key = _required(env, "RUNPOD_API_KEY") if live else env.get("RUNPOD_API_KEY", "").strip()
    budget = _optional_float(env, "RUNPOD_BUDGET_USD")
    return {
        "endpoint_id": env.get("RUNPOD_ENDPOINT_ID", "").strip() or None,
        "gpu_type": env.get("RUNPOD_GPU_TYPE", "NVIDIA GeForce RTX 4090").strip(),
        "budget_usd": budget,
        "credential_configured": bool(key),
        "account_id": env.get("RUNPOD_ACCOUNT_ID", "").strip() or None,
    }


def _post_json(url: str, payload: Any, headers: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HarnessConfigError(f"provider request failed: {exc}") from exc
    try:
        decoded = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise HarnessConfigError("provider returned non-JSON response") from exc
    if not isinstance(decoded, dict):
        raise HarnessConfigError("provider returned an invalid response")
    return decoded


def _azure_live(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": config["client_id"],
            "client_secret": env["AZURE_CLIENT_SECRET"],
            "scope": "https://management.azure.com/.default",
        }
    ).encode("ascii")
    token_request = urllib.request.Request(
        f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=20) as response:
            token_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HarnessConfigError(f"Azure credential validation failed: {exc}") from exc
    token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
    if not token:
        raise HarnessConfigError("Azure token response did not contain access_token")
    subscription_request = urllib.request.Request(
        f"https://management.azure.com/subscriptions/{config['subscription_id']}?api-version=2020-01-01",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(subscription_request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HarnessConfigError(f"Azure subscription validation failed: {exc}") from exc
    return {"subscription": payload.get("displayName", config["subscription_id"]), "status": payload.get("state")}


def _runpod_live(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    payload = _post_json(
        env.get("RUNPOD_API_URL", "https://api.runpod.io/graphql"),
        {"query": "query { myself { id username } }"},
        {"Authorization": f"Bearer {env['RUNPOD_API_KEY']}", "Content-Type": "application/json"},
    )
    errors = payload.get("errors")
    if errors:
        raise HarnessConfigError("RunPod credential validation returned errors")
    myself = payload.get("data", {}).get("myself", {})
    return {"account": myself.get("id") or myself.get("username") or "authenticated"}


def _emit_telemetry(provider: str, result: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    url = env.get("GLUDD_INGEST_URL", "").strip()
    if not url:
        return {"enabled": False}
    token = _required(env, "GLUDD_INGEST_TOKEN")
    now = time.time()
    records = [
        {
            "kind": "events",
            "event": "provider_harness.validation",
            "provider": provider,
            "result": result,
            "timestamp": now,
        },
        {
            "kind": "logs",
            "message": f"{provider} smoke harness completed",
            "provider": provider,
            "timestamp": now,
        },
    ]
    _post_json(
        url.rstrip("/") + "/ingest/webhook",
        records,
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    return {"enabled": True, "records": len(records), "url": url}


def run_harness(provider: str, env: dict[str, str] | None = None, *, live: bool = False) -> dict[str, Any]:
    values = dict(os.environ if env is None else env)
    normalized = provider.strip().lower()
    if normalized == "azure":
        config = _azure_config(values, live=live)
        checks = _azure_live(config, values) if live else {"mode": "configuration"}
    elif normalized == "runpod":
        config = _runpod_config(values, live=live)
        checks = _runpod_live(config, values) if live else {"mode": "configuration"}
    else:
        raise HarnessConfigError("provider must be azure or runpod")
    result: dict[str, Any] = {
        "ok": True,
        "provider": normalized,
        "mode": "live" if live else "dry-run",
        "configuration": config,
        "checks": checks,
    }
    result["telemetry"] = _emit_telemetry(normalized, result, values)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("azure", "runpod"))
    parser.add_argument("--live", action="store_true", help="perform read-only provider credential checks")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_harness(args.provider, live=args.live), sort_keys=True))
    except HarnessConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
