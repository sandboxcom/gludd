#!/usr/bin/env python3
"""Run a hermetic or explicitly authorized Azure Container App model proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentials,
    load_azure_accelerator_credentials,
)
from general_ludd.cloud.azure_game_runtime import resolve_public_ipv4_cidr
from general_ludd.infra.azure_containerapp_arm import (
    HttpxARMJSONTransport,
    HttpxContainerAppARMTransport,
    HttpxContainerAppEnvironmentLifecycleTransport,
)
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
    EnvironmentLifecycleTrace,
)
from general_ludd.infra.azure_containerapp_environment_make_runtime import (
    AzureContainerAppEnvironmentTerraformRuntime,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppProofRuntime,
    LiveProofTrace,
    run_azure_containerapp_live_proof,
)
from general_ludd.infra.azure_containerapp_make_runtime import (
    AzureContainerAppTerraformRuntime,
    MakeRuntimeEvent,
)
from general_ludd.infra.azure_containerapp_owned_lifecycle import (
    run_owned_azure_containerapp_live_proof,
)
from general_ludd.infra.azure_containerapp_preflight import (
    AzureContainerAppReadOnlyPreflight,
    PreflightTrace,
)
from general_ludd.infra.azure_containerapp_runtime_resources import (
    ARM_SCOPE as ARM_SCOPE,
)
from general_ludd.infra.azure_containerapp_runtime_resources import (
    AzureContainerAppRuntimeResources,
    build_azure_containerapp_runtime_resources,
)
from general_ludd.infra.azure_containerapp_runtime_resources import (
    _credential_client as _credential_client,
)
from general_ludd.infra.azure_containerapp_runtime_resources import (
    _environment_ready as _environment_ready,
)
from general_ludd.infra.azure_containerapp_runtime_resources import _ready as _ready
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
    AzurePromptApprovalError,
)
from general_ludd.self_improve.azure_containerapp_backend import (
    ContainerAppBackendTrace,
    build_azure_containerapp_candidate_backend,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendCallBudget,
    CandidateBackend,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

_DefaultResources = AzureContainerAppRuntimeResources

_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
_IMAGE = (
    "vllm/vllm-openai@sha256:"
    "df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6"
)
_PARAMETER_COUNT = 494_032_768
_PROMPT = "Suggest one deterministic edge-case test for a public Python function."
_WORK_ROOT = Path("/tmp/gludd-azure-containerapp-live-proof")
_ENVIRONMENT_WORK_ROOT = Path("/tmp/gludd-azure-containerapp-environments")


class _ClosableCredential(Protocol):
    def get_token(self, *scopes: str) -> Any: ...

    def close(self) -> None: ...


class _CredentialLeaseSource(Protocol):
    def acquire(self) -> AzureAcceleratorCredentialLease: ...

    def release(self, lease: AzureAcceleratorCredentialLease) -> None: ...


class _LiveResources(Protocol):
    runtime: AzureContainerAppProofRuntime
    environment_runtime: AzureContainerAppEnvironmentRuntime

    def backend_factory(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]: ...

    def close(self) -> None: ...


LiveResourcesFactory = Callable[
    [
        argparse.Namespace,
        AzureContainerAppLiveProofPolicy,
        ModelServingRequirement,
    ],
    _LiveResources,
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prove one right-sized Azure GPU candidate with Terraform-owned "
            "environment and app cleanup."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--workload-profile-name", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--allowed-cidr", required=True)
    parser.add_argument("--max-cost-usd", required=True, type=float)
    parser.add_argument("--ttl-minutes", required=True, type=int)
    parser.add_argument("--live", required=True, type=int, choices=(0, 1))
    parser.add_argument("--acknowledgement", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--source-path", required=True)
    return parser


def _trace(
    prefix: str,
    value: (
        ContainerAppBackendTrace
        | EnvironmentLifecycleTrace
        | LiveProofTrace
        | MakeRuntimeEvent
        | PreflightTrace
    ),
) -> None:
    fields = {
        key: item
        for key, item in asdict(value).items()
        if item is not None
    }
    print(
        f"{prefix} "
        + " ".join(
            f"{key}={json.dumps(item, ensure_ascii=True, separators=(',', ':'))}"
            for key, item in sorted(fields.items())
        )
        + " secret_output=false",
        flush=True,
    )


def _policy_trace(message: str) -> None:
    print(f"AZURE_CONTAINERAPP_PRIVACY_TRACE {message} secret_output=false", flush=True)


def _requirement() -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id=_MODEL,
        revision=_MODEL_REVISION,
        parameter_count=_PARAMETER_COUNT,
        weight_bits=16,
        kv_cache_mib=2048,
        runtime_overhead_mib=3072,
    )


def _policy(
    args: argparse.Namespace,
    *,
    app_name: str,
) -> AzureContainerAppLiveProofPolicy:
    requested_cidr = cast(str, args.allowed_cidr)
    allowed_cidr = (
        resolve_public_ipv4_cidr()
        if requested_cidr.casefold() == "auto"
        else requested_cidr
    )
    return AzureContainerAppLiveProofPolicy(
        subscription_id=cast(str, args.subscription_id),
        resource_group=cast(str, args.resource_group),
        environment_name=cast(str, args.environment),
        workload_profile_name=cast(str, args.workload_profile_name),
        workload_profile_type="Consumption-GPU-NC8as-T4",
        location=cast(str, args.location),
        app_name=app_name,
        allowed_cidr=allowed_cidr,
        container_image=_IMAGE,
        model_name=_MODEL,
        model_revision=_MODEL_REVISION,
        max_cost_usd=cast(float, args.max_cost_usd),
        ttl_minutes=cast(int, args.ttl_minutes),
        call_budget=BackendCallBudget(
            max_calls=1,
            max_input_tokens=512,
            max_output_tokens=64,
            max_total_tokens=576,
            max_cost_microusd=500_000,
            timeout_seconds=60.0,
        ),
        estimated_request_cost_microusd=0,
        live=bool(args.live),
        acknowledgement=(
            cast(str, args.acknowledgement) if bool(args.live) else None
        ),
    )


def _environment_policy(
    app_policy: AzureContainerAppLiveProofPolicy,
    *,
    guard: SelfImproveRuntimePolicyGuard,
    project_root: Path,
    now: datetime,
) -> AzureEnvironmentLifecyclePolicy:
    """Bind stable project ownership and a bounded expiry to one environment."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    owner_payload = {
        "environment_id": app_policy.environment_id.casefold(),
        "policy_digest": guard.expected_digest,
        "project_root": str(project_root.resolve()),
        "protocol": "gludd-owned-azure-containerapp-environment-v1",
    }
    owner_digest = hashlib.sha256(
        json.dumps(
            owner_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    expires_at = (
        now.astimezone(UTC) + timedelta(minutes=app_policy.ttl_minutes)
    ).replace(microsecond=0)
    return AzureEnvironmentLifecyclePolicy(
        subscription_id=app_policy.subscription_id,
        resource_group=app_policy.resource_group,
        environment_name=app_policy.environment_name,
        location=app_policy.location,
        profiles=(
            AzureEnvironmentProfile(
                app_policy.workload_profile_name,
                app_policy.workload_profile_type,
            ),
        ),
        owner_digest=owner_digest,
        plan_digest=app_policy.operation_digest,
        expires_at_utc=expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        teardown_when_idle=True,
    )


def _hermetic_plan(policy: AzureContainerAppLiveProofPolicy) -> dict[str, object]:
    return {
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "module.vllm_server.azapi_resource.vllm",
                "mode": "managed",
                "type": "azapi_resource",
                "name": "vllm",
                "provider_name": "registry.terraform.io/azure/azapi",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {
                        "type": "Microsoft.App/containerApps@2025-01-01",
                        "name": policy.app_name,
                        "parent_id": policy.resource_group_id,
                        "location": policy.location,
                        "body": {
                            "properties": {
                                "managedEnvironmentId": policy.environment_id,
                                "workloadProfileName": policy.workload_profile_name,
                                "configuration": {
                                    "activeRevisionsMode": "Single",
                                    "ingress": {
                                        "external": True,
                                        "allowInsecure": False,
                                        "targetPort": 8000,
                                        "transport": "auto",
                                        "ipSecurityRestrictions": [
                                            {
                                                "action": "Allow",
                                                "description": "Exact Gludd live-proof caller",
                                                "ipAddressRange": policy.allowed_cidr,
                                                "name": "gludd-live-proof-client",
                                            }
                                        ],
                                    },
                                },
                                "template": {
                                    "containers": [
                                        {
                                            "image": policy.container_image,
                                            "args": [
                                                "--model",
                                                policy.model_name,
                                                "--revision",
                                                policy.model_revision,
                                                "--tokenizer-revision",
                                                policy.model_revision,
                                            ],
                                        }
                                    ]
                                },
                            }
                        },
                    },
                },
            }
        ],
    }


class _HermeticRuntime:
    def plan(self, policy: AzureContainerAppLiveProofPolicy) -> object:
        return _hermetic_plan(policy)

    def preflight(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        del policy
        raise RuntimeError("dry-run preflight is unreachable")

    def apply(self, policy: AzureContainerAppLiveProofPolicy) -> Any:
        del policy
        raise RuntimeError("dry-run apply is unreachable")

    def destroy(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        del policy
        raise RuntimeError("dry-run destroy is unreachable")

    def exists(self, policy: AzureContainerAppLiveProofPolicy) -> bool:
        del policy
        raise RuntimeError("dry-run absence check is unreachable")


def _default_live_resources(
    args: argparse.Namespace,
    policy: AzureContainerAppLiveProofPolicy,
    requirement: ModelServingRequirement,
    *,
    credentials: AzureAcceleratorCredentials | None = None,
    credential_release: Callable[[], None] | None = None,
) -> _LiveResources:
    """Build concrete runtimes without retaining infrastructure logic in the CLI."""
    if credentials is None:
        credentials = load_azure_accelerator_credentials(
            cast(str, args.auth_file),
            expected_subscription_id=policy.subscription_id,
        )
    elif (
        not isinstance(credentials, AzureAcceleratorCredentials)
        or credentials.subscription_id != policy.subscription_id
    ):
        raise ValueError("Azure credentials do not match the approved subscription")

    def progress(message: str) -> None:
        _component, separator, fields = message.partition(" ")
        print(
            "AZURE_CONTAINERAPP_ARM_TRACE "
            f"{fields if separator else message} secret_output=false",
            flush=True,
        )

    return build_azure_containerapp_runtime_resources(
        credentials=credentials,
        policy=policy,
        requirement=requirement,
        work_root=_WORK_ROOT,
        environment_work_root=_ENVIRONMENT_WORK_ROOT,
        credential_release=credential_release,
        preflight_trace_sink=lambda event: _trace(
            "AZURE_CONTAINERAPP_PREFLIGHT_TRACE",
            event,
        ),
        terraform_trace_sink=lambda event: _trace(
            "AZURE_CONTAINERAPP_TERRAFORM_TRACE",
            event,
        ),
        environment_terraform_trace_sink=lambda event: _trace(
            "AZURE_CONTAINERAPP_ENVIRONMENT_TERRAFORM_TRACE",
            event,
        ),
        backend_trace_sink=lambda event: _trace(
            "AZURE_CONTAINERAPP_BACKEND_TRACE",
            event,
        ),
        progress_sink=progress,
        monotonic=lambda: time.monotonic(),
        sleep=lambda seconds: time.sleep(seconds),
        _credential_factory=_credential_client,
        _environment_transport_factory=HttpxARMJSONTransport,
        _lifecycle_transport_factory=(
            HttpxContainerAppEnvironmentLifecycleTransport
        ),
        _app_transport_factory=HttpxContainerAppARMTransport,
        _preflight_factory=AzureContainerAppReadOnlyPreflight,
        _app_runtime_factory=AzureContainerAppTerraformRuntime,
        _environment_runtime_factory=(
            AzureContainerAppEnvironmentTerraformRuntime
        ),
        _backend_factory=build_azure_containerapp_candidate_backend,
    )


def build_openbao_live_resources_factory(
    source: _CredentialLeaseSource,
) -> LiveResourcesFactory:
    """Build a live-resource factory backed by one exact OpenBao lease per run."""
    if not callable(getattr(source, "acquire", None)) or not callable(
        getattr(source, "release", None)
    ):
        raise ValueError("source must issue and exactly revoke Azure credential leases")

    def build(
        args: argparse.Namespace,
        policy: AzureContainerAppLiveProofPolicy,
        requirement: ModelServingRequirement,
    ) -> _LiveResources:
        lease = source.acquire()
        if not isinstance(lease, AzureAcceleratorCredentialLease):
            raise ValueError("credential source returned an invalid Azure lease")
        try:
            return _default_live_resources(
                args,
                policy,
                requirement,
                credentials=lease.credentials,
                credential_release=lambda: source.release(lease),
            )
        except BaseException:
            with suppress(Exception):
                source.release(lease)
            raise

    return build


def main(
    argv: Sequence[str] | None = None,
    *,
    live_resources_factory: LiveResourcesFactory = _default_live_resources,
    token_hex: Callable[[int], str] = secrets.token_hex,
) -> int:
    """Apply privacy, cost, scope, model, and cleanup proofs from one command."""

    args = _parser().parse_args(argv)
    resources: _LiveResources | None = None
    failure: str | None = None
    result = None
    try:
        app_name = f"gludd-vllm-proof-{token_hex(6)}"
        policy = _policy(args, app_name=app_name)
        project_root = Path(cast(str, args.project_root)).resolve(strict=True)
        source_path = cast(str, args.source_path)
        guard = SelfImproveRuntimePolicyGuard.load(
            project_root,
            _policy_trace,
            AzurePromptApprovalError,
        )
        approved_prompt = AzureApprovedPrompt.approve(
            prompt=_PROMPT,
            source_paths=(source_path,),
            policy_guard=guard,
        )
        requirement = _requirement()
        if policy.live:
            environment_policy = _environment_policy(
                policy,
                guard=guard,
                project_root=project_root,
                now=datetime.now(UTC),
            )
            resources = live_resources_factory(args, policy, requirement)
            result = run_owned_azure_containerapp_live_proof(
                policy,
                environment_policy=environment_policy,
                environment_runtime=resources.environment_runtime,
                app_runtime=resources.runtime,
                approved_prompt=approved_prompt,
                backend_factory=resources.backend_factory,
                environment_trace_sink=lambda event: _trace(
                    "AZURE_CONTAINERAPP_ENVIRONMENT_LIFECYCLE_TRACE",
                    event,
                ),
                app_trace_sink=lambda event: _trace(
                    "AZURE_CONTAINERAPP_LIVE_PROOF_TRACE",
                    event,
                ),
            )
        else:
            runtime = _HermeticRuntime()

            def unreachable_backend(
                _identity: AzureContainerAppCandidateIdentity,
            ) -> CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]:
                raise RuntimeError("dry-run backend is unreachable")

            backend_factory = unreachable_backend
            result = run_azure_containerapp_live_proof(
                policy,
                runtime=runtime,
                approved_prompt=approved_prompt,
                backend_factory=backend_factory,
                trace_sink=lambda event: _trace(
                    "AZURE_CONTAINERAPP_LIVE_PROOF_TRACE",
                    event,
                ),
            )
    except AzureContainerAppLiveProofError as exc:
        failure = exc.failure.value
    except Exception:
        failure = "initialization"
    finally:
        if resources is not None:
            try:
                resources.close()
            except Exception:
                failure = "client-cleanup"
    if failure is not None or result is None:
        print(
            "AZURE_CONTAINERAPP_LIVE_PROOF_INVALID "
            f"reason={failure or 'unknown'} secret_output=false",
            file=sys.stderr,
        )
        return 2
    print(
        "AZURE_CONTAINERAPP_LIVE_PROOF_RESULT "
        f"live={str(bool(args.live)).lower()} "
        f"environment_managed={str(bool(args.live)).lower()} "
        f"plan_audited={str(result.plan_audited).lower()} "
        f"deployment_created={str(result.deployment_created).lower()} "
        f"work_completed={str(result.work_completed).lower()} "
        f"cleanup_verified={str(result.cleanup_verified).lower()} "
        f"input_tokens={result.input_tokens} "
        f"output_tokens={result.output_tokens} "
        f"total_tokens={result.total_tokens} "
        "secret_output=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
