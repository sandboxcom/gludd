#!/usr/bin/env python3
"""Non-provisioning Azure accelerator credential, SKU, and quota harness.

Dry-run mode is entirely local and is safe for CI. ``--live`` performs only
read operations through Azure Compute (SKU discovery and regional/family quota
usage); it never invokes Terraform and never creates or deletes resources.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import cast

from general_ludd.infra.azure_accelerator import (
    AzureAcceleratorPreflight,
    AzureComputeClient,
    resolve_accelerator,
)
from general_ludd.infra.compute import GPUType


class HarnessConfigError(ValueError):
    """Raised when a smoke-harness input is missing or invalid."""


def _required(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise HarnessConfigError(f"{name} is required")
    return value


def _positive_int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise HarnessConfigError(f"{name} must be an integer") from exc
    if value < 1:
        raise HarnessConfigError(f"{name} must be greater than zero")
    return value


def _azure_config(
    env: dict[str, str],
    *,
    live: bool,
) -> tuple[dict[str, object], GPUType, int, str]:
    subscription_id = env.get("AZURE_SUBSCRIPTION_ID", "").strip()
    tenant_id = env.get("AZURE_TENANT_ID", "").strip()
    client_id = env.get("AZURE_CLIENT_ID", "").strip()
    client_secret = env.get("AZURE_CLIENT_SECRET", "").strip()
    if live:
        subscription_id = _required(env, "AZURE_SUBSCRIPTION_ID")
    if client_secret and not (tenant_id and client_id):
        raise HarnessConfigError(
            "AZURE_CLIENT_SECRET requires AZURE_TENANT_ID and AZURE_CLIENT_ID"
        )

    gpu_raw = env.get("AZURE_GPU_TYPE", GPUType.A100_80.value).strip()
    try:
        gpu_type = GPUType(gpu_raw)
    except ValueError as exc:
        raise HarnessConfigError(f"unsupported AZURE_GPU_TYPE: {gpu_raw}") from exc
    gpu_count = _positive_int(env, "AZURE_GPU_COUNT", 1)
    location = env.get("AZURE_LOCATION", "eastus").strip() or "eastus"
    service_principal_complete = all(
        (subscription_id, tenant_id, client_id, client_secret)
    )
    managed_identity_complete = bool(
        subscription_id and client_id and not client_secret
    )
    if service_principal_complete:
        auth_mode = "service-principal"
    elif managed_identity_complete:
        auth_mode = "managed-identity"
    else:
        auth_mode = "default-credential-chain"
    config: dict[str, object] = {
        "subscription_id": subscription_id or None,
        "tenant_id": tenant_id or None,
        "client_id": client_id or None,
        "client_secret_configured": bool(client_secret),
        "credentials_complete": (
            service_principal_complete or managed_identity_complete
        ),
        "auth_mode": auth_mode,
        "resource_group": env.get("AZURE_RESOURCE_GROUP", "").strip() or None,
        "location": location,
    }
    return config, gpu_type, gpu_count, location


def _live_preflight(
    *,
    subscription_id: str,
    gpu_type: GPUType,
    gpu_count: int,
    location: str,
) -> dict[str, object]:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient
    except ImportError as exc:
        raise HarnessConfigError(
            "Azure SDK unavailable; install general-ludd-agent[azure]"
        ) from exc

    try:
        client = ComputeManagementClient(
            credential=DefaultAzureCredential(),
            subscription_id=subscription_id,
        )
        result = AzureAcceleratorPreflight(
            cast(AzureComputeClient, client)
        ).check(
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            location=location,
        )
    except Exception as exc:
        raise HarnessConfigError(f"Azure read-only preflight failed: {exc}") from exc
    return result.as_dict()


def _post_telemetry(
    result: dict[str, object],
    env: dict[str, str],
) -> dict[str, object]:
    ingest_url = env.get("GLUDD_INGEST_URL", "").strip()
    if not ingest_url:
        return {"enabled": False}
    token = _required(env, "GLUDD_INGEST_TOKEN")
    request = urllib.request.Request(
        ingest_url.rstrip("/") + "/ingest/webhook",
        data=json.dumps(
            {
                "events": [
                    {
                        "kind": "events",
                        "event": "provider_harness.azure_accelerator_preflight",
                        "provider": "azure",
                        "result": result,
                        "timestamp": time.time(),
                    },
                    {
                        "kind": "logs",
                        "message": "Azure accelerator smoke harness completed",
                        "provider": "azure",
                        "timestamp": time.time(),
                    },
                ]
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HarnessConfigError(f"Gludd telemetry delivery failed: {exc}") from exc
    return {"enabled": True, "records": 2, "url": ingest_url}


def run_harness(
    provider: str,
    env: dict[str, str] | None = None,
    *,
    live: bool = False,
) -> dict[str, object]:
    """Run a dry or read-only-live Azure accelerator preflight."""

    if provider.strip().casefold() != "azure":
        raise HarnessConfigError("provider must be azure")
    values = dict(os.environ if env is None else env)
    configuration, gpu_type, gpu_count, location = _azure_config(
        values,
        live=live,
    )
    try:
        accelerator = resolve_accelerator(gpu_type, gpu_count)
    except ValueError as exc:
        raise HarnessConfigError(str(exc)) from exc

    if live:
        subscription = configuration["subscription_id"]
        if not isinstance(subscription, str):
            raise HarnessConfigError("AZURE_SUBSCRIPTION_ID is required")
        checks: dict[str, object] = _live_preflight(
            subscription_id=subscription,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            location=location,
        )
        ok = bool(checks["ready"])
        live_operations = ["resource_skus.list", "usage.list"]
    else:
        checks = {
            "ready": None,
            "note": "dry-run resolved the VM shape; use LIVE=1 for read-only SKU/quota checks",
        }
        ok = True
        live_operations = []

    result: dict[str, object] = {
        "ok": ok,
        "provider": "azure",
        "mode": "live-read-only" if live else "dry-run",
        "configuration": configuration,
        "accelerator": accelerator.as_dict(),
        "checks": checks,
        "live_operations": live_operations,
    }
    result["telemetry"] = _post_telemetry(result, values)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("azure",))
    parser.add_argument(
        "--live",
        action="store_true",
        help="perform only read-only Azure credential, SKU, and quota checks",
    )
    args = parser.parse_args(argv)
    try:
        result = run_harness(args.provider, live=args.live)
    except HarnessConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
