#!/usr/bin/env python3
"""Run a traced, read-only Azure Container Apps GPU capacity preflight."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Protocol, cast

from general_ludd.azure.accelerator_credential_store import (
    load_preserved_azure_accelerator_credentials as load_azure_accelerator_credentials,
)
from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentialError,
)
from general_ludd.infra.azure_containerapp_arm import HttpxARMJSONTransport
from general_ludd.infra.azure_containerapp_gpu import (
    AzureContainerAppGPUUnavailable,
    ModelServingRequirement,
    select_smallest_sufficient_profile,
)
from general_ludd.infra.azure_containerapp_preflight import (
    AzureContainerAppPreflightError,
    AzureContainerAppReadOnlyPreflight,
    PreflightTrace,
    TokenCredential,
)

_AZURE_PUBLIC_AUTHORITY = "login.microsoftonline.com"


class _ClosableCredential(TokenCredential, Protocol):
    def close(self) -> None: ...


class _ClosableTransport(Protocol):
    def get_json(self, path: str, bearer_token: str) -> object: ...

    def close(self) -> None: ...


CredentialFactory = Callable[..., _ClosableCredential]
TransportFactory = Callable[[], _ClosableTransport]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Azure Container Apps GPU profile and quota check.",
        allow_abbrev=False,
    )
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--workload-profile-name", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--parameter-count", required=True, type=int)
    parser.add_argument("--weight-bits", required=True, type=int)
    parser.add_argument("--kv-cache-mib", required=True, type=int)
    parser.add_argument("--runtime-overhead-mib", required=True, type=int)
    parser.add_argument("--live", required=True, choices=(0, 1), type=int)
    return parser


def _requirement(args: argparse.Namespace) -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id=cast(str, args.model_id),
        revision=cast(str, args.model_revision),
        parameter_count=cast(int, args.parameter_count),
        weight_bits=cast(int, args.weight_bits),
        kv_cache_mib=cast(int, args.kv_cache_mib),
        runtime_overhead_mib=cast(int, args.runtime_overhead_mib),
    )


def _trace_sink(trace: PreflightTrace) -> None:
    fields = asdict(trace)
    record_names = json.dumps(
        fields.pop("record_names"),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    rendered = " ".join(
        f"{name}={value}"
        for name, value in fields.items()
        if value is not None
    )
    print(
        "AZURE_CONTAINERAPP_PREFLIGHT_TRACE "
        f"{rendered} record_names={record_names} secret_output=false"
    )


def _default_credential_factory() -> CredentialFactory:
    try:
        from azure.identity import ClientSecretCredential
    except ImportError:
        raise RuntimeError("Azure Identity dependency is unavailable") from None
    return cast(CredentialFactory, ClientSecretCredential)


def _close_clients(
    credential: _ClosableCredential | None,
    transport: _ClosableTransport | None,
) -> bool:
    failed = False
    for client in (transport, credential):
        if client is None:
            continue
        try:
            client.close()
        except Exception:
            failed = True
    return failed


def _live_check(
    args: argparse.Namespace,
    requirement: ModelServingRequirement,
    *,
    credential_factory: CredentialFactory | None,
    transport_factory: TransportFactory,
) -> int:
    try:
        credentials = load_azure_accelerator_credentials(
            cast(str, args.auth_file),
            expected_subscription_id=cast(str, args.subscription_id),
        )
    except AzureAcceleratorCredentialError as exc:
        print(f"AZURE_CONTAINERAPP_PREFLIGHT_INVALID reason={exc}", file=sys.stderr)
        return 2

    credential_client: _ClosableCredential | None = None
    transport: _ClosableTransport | None = None
    result = None
    failure: str | None = None
    try:
        factory = credential_factory or _default_credential_factory()
        credential_client = factory(
            tenant_id=credentials.tenant_id,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            authority=_AZURE_PUBLIC_AUTHORITY,
            disable_instance_discovery=True,
            retry_total=0,
        )
        transport = transport_factory()
        result = AzureContainerAppReadOnlyPreflight(
            credential_client,
            transport,
            trace_sink=_trace_sink,
        ).check(
            subscription_id=credentials.subscription_id,
            resource_group=cast(str, args.resource_group),
            environment_name=cast(str, args.environment),
            workload_profile_name=cast(str, args.workload_profile_name),
            location=cast(str, args.location),
            requirement=requirement,
        )
    except AzureContainerAppPreflightError as exc:
        failure = str(exc)
    except Exception:
        failure = "Azure preflight client initialization failed"
    cleanup_failed = _close_clients(credential_client, transport)
    if cleanup_failed:
        failure = "Azure preflight client cleanup failed"
    if failure is not None:
        print(f"AZURE_CONTAINERAPP_PREFLIGHT_INVALID reason={failure}", file=sys.stderr)
        return 2
    if result is None:
        print(
            "AZURE_CONTAINERAPP_PREFLIGHT_INVALID reason=missing preflight result",
            file=sys.stderr,
        )
        return 2
    print(
        "AZURE_CONTAINERAPP_PREFLIGHT_READY "
        f"profile={result.profile.name} "
        f"required_vram_mib={result.required_vram_mib} "
        f"quota_scope={result.quota_scope} "
        f"quota_verified={str(result.quota_verified).lower()} "
        "quota_remaining="
        f"{'pending' if result.quota_remaining is None else f'{result.quota_remaining:g}'} "
        "secret_output=false"
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_factory: CredentialFactory | None = None,
    transport_factory: TransportFactory | None = None,
) -> int:
    """Validate inputs, optionally authenticate, and emit secret-free evidence."""

    args = _parser().parse_args(argv)
    try:
        requirement = _requirement(args)
        selection = select_smallest_sufficient_profile(requirement)
    except (ValueError, AzureContainerAppGPUUnavailable) as exc:
        print(f"AZURE_CONTAINERAPP_PREFLIGHT_INVALID reason={exc}", file=sys.stderr)
        return 2
    if args.live == 0:
        print(
            "AZURE_CONTAINERAPP_PREFLIGHT_PLAN "
            f"profile={selection.profile.name} "
            f"required_vram_mib={selection.required_vram_mib} "
            "secret_output=false"
        )
        return 0
    selected_transport_factory = transport_factory or (
        lambda: HttpxARMJSONTransport(
            subscription_id=cast(str, args.subscription_id),
            resource_group=cast(str, args.resource_group),
            environment_name=cast(str, args.environment),
        )
    )
    return _live_check(
        args,
        requirement,
        credential_factory=credential_factory,
        transport_factory=selected_transport_factory,
    )


if __name__ == "__main__":
    raise SystemExit(main())
