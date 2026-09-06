#!/usr/bin/env python3
"""Run a hermetic or explicitly authorized Azure Container App model proof."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentials,
    load_azure_accelerator_credentials,
)
from general_ludd.infra.azure_containerapp_arm import (
    HttpxARMJSONTransport,
    HttpxContainerAppARMTransport,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppProofRuntime,
    LiveProofTrace,
    run_azure_containerapp_live_proof,
)
from general_ludd.infra.azure_containerapp_make_runtime import (
    AzureContainerAppMakeRuntime,
    MakeRuntimeEvent,
)
from general_ludd.infra.azure_containerapp_preflight import (
    ARM_SCOPE,
    AzureContainerAppReadOnlyPreflight,
    PreflightTrace,
)
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

_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
_IMAGE = (
    "vllm/vllm-openai@sha256:"
    "df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6"
)
_PARAMETER_COUNT = 494_032_768
_PROMPT = "Suggest one deterministic edge-case test for a public Python function."
_WORK_ROOT = Path("/tmp/gludd-azure-containerapp-live-proof")
_REPO_ROOT = Path(__file__).resolve().parents[1]
_AZURE_AUTHORITY = "login.microsoftonline.com"


class _ClosableCredential(Protocol):
    def get_token(self, *scopes: str) -> Any: ...

    def close(self) -> None: ...


class _LiveResources(Protocol):
    runtime: AzureContainerAppProofRuntime

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
        description="Prove one right-sized Azure GPU candidate and app-only cleanup.",
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


def _trace(prefix: str, value: object) -> None:
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
    return AzureContainerAppLiveProofPolicy(
        subscription_id=cast(str, args.subscription_id),
        resource_group=cast(str, args.resource_group),
        environment_name=cast(str, args.environment),
        workload_profile_name=cast(str, args.workload_profile_name),
        workload_profile_type="Consumption-GPU-NC8as-T4",
        location=cast(str, args.location),
        app_name=app_name,
        allowed_cidr=cast(str, args.allowed_cidr),
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


class _DefaultResources:
    def __init__(
        self,
        *,
        runtime: AzureContainerAppMakeRuntime,
        credential: _ClosableCredential,
        environment_transport: HttpxARMJSONTransport,
        app_transport: HttpxContainerAppARMTransport,
    ) -> None:
        self.runtime = runtime
        self._credential = credential
        self._environment_transport = environment_transport
        self._app_transport = app_transport
        self._closed = False

    def backend_factory(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]:
        return build_azure_containerapp_candidate_backend(
            identity,
            discovery_timeout_seconds=120.0,
            trace_sink=lambda event: _trace(
                "AZURE_CONTAINERAPP_BACKEND_TRACE",
                cast(ContainerAppBackendTrace, event),
            ),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failed = False
        for client in (
            self._app_transport,
            self._environment_transport,
            self._credential,
        ):
            try:
                client.close()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("Azure live resource cleanup failed")


def _credential_client(credentials: AzureAcceleratorCredentials) -> _ClosableCredential:
    try:
        from azure.identity import ClientSecretCredential
    except ImportError:
        raise RuntimeError("Azure Identity dependency is unavailable") from None
    return cast(
        _ClosableCredential,
        ClientSecretCredential(
            tenant_id=credentials.tenant_id,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            authority=_AZURE_AUTHORITY,
            disable_instance_discovery=True,
            retry_total=0,
        ),
    )


def _ready(document: object | None) -> bool:
    if not isinstance(document, Mapping):
        return False
    properties = document.get("properties")
    return bool(
        isinstance(properties, Mapping)
        and properties.get("provisioningState") == "Succeeded"
        and isinstance(properties.get("latestReadyRevisionName"), str)
        and properties.get("latestReadyRevisionName")
    )


def _default_live_resources(
    args: argparse.Namespace,
    policy: AzureContainerAppLiveProofPolicy,
    requirement: ModelServingRequirement,
) -> _LiveResources:
    credentials = load_azure_accelerator_credentials(
        cast(str, args.auth_file),
        expected_subscription_id=policy.subscription_id,
    )
    credential: _ClosableCredential | None = None
    environment_transport: HttpxARMJSONTransport | None = None
    app_transport: HttpxContainerAppARMTransport | None = None
    try:
        credential = _credential_client(credentials)
        environment_transport = HttpxARMJSONTransport(
            subscription_id=policy.subscription_id,
            resource_group=policy.resource_group,
            environment_name=policy.environment_name,
        )
        app_transport = HttpxContainerAppARMTransport(
            subscription_id=policy.subscription_id,
            resource_group=policy.resource_group,
            app_name=policy.app_name,
        )

        def preflight(
            active_policy: AzureContainerAppLiveProofPolicy,
            active_requirement: ModelServingRequirement,
        ) -> None:
            AzureContainerAppReadOnlyPreflight(
                cast(Any, credential),
                cast(Any, environment_transport),
                trace_sink=lambda event: _trace(
                    "AZURE_CONTAINERAPP_PREFLIGHT_TRACE",
                    cast(PreflightTrace, event),
                ),
            ).check(
                subscription_id=active_policy.subscription_id,
                resource_group=active_policy.resource_group,
                environment_name=active_policy.environment_name,
                workload_profile_name=active_policy.workload_profile_name,
                location=active_policy.location,
                requirement=active_requirement,
            )

        def read_app(
            _active_policy: AzureContainerAppLiveProofPolicy,
            expect_absent: bool,
        ) -> object | None:
            deadline = time.monotonic() + (600.0 if expect_absent else 900.0)
            last_document: object | None = None
            while True:
                token = credential.get_token(ARM_SCOPE).token
                last_document = app_transport.get_json(token)
                if expect_absent:
                    if last_document is None:
                        return None
                elif _ready(last_document):
                    return last_document
                if time.monotonic() >= deadline:
                    return last_document
                print(
                    "AZURE_CONTAINERAPP_ARM_TRACE phase="
                    f"{'absence' if expect_absent else 'readiness'} "
                    "state=heartbeat secret_output=false",
                    flush=True,
                )
                time.sleep(10.0)

        runtime = AzureContainerAppMakeRuntime(
            repo_root=_REPO_ROOT,
            work_root=_WORK_ROOT,
            credentials=credentials,
            requirement=requirement,
            preflight_check=preflight,
            read_app=read_app,
            trace_sink=lambda event: _trace(
                "AZURE_CONTAINERAPP_MAKE_TRACE",
                cast(MakeRuntimeEvent, event),
            ),
        )
        return _DefaultResources(
            runtime=runtime,
            credential=credential,
            environment_transport=environment_transport,
            app_transport=app_transport,
        )
    except BaseException:
        for client in (app_transport, environment_transport, credential):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        raise


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
            resources = live_resources_factory(args, policy, requirement)
            runtime = resources.runtime
            backend_factory = resources.backend_factory
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
                cast(LiveProofTrace, event),
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
