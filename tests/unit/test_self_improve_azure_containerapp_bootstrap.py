"""Configuration-to-runtime coverage for autonomous Azure model bootstrapping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentials,
)
from general_ludd.azure.resource_group_bootstrap import (
    AzureResourceGroupBootstrapPolicy,
    AzureResourceGroupBootstrapState,
    AzureResourceGroupBootstrapTrace,
)
from general_ludd.infra.azure_containerapp_gpu import A100_PROFILE, T4_PROFILE
from general_ludd.infra.azure_containerapp_make_types import (
    MakeRuntimeEvent,
    MakeRuntimeState,
)
from general_ludd.infra.azure_containerapp_owned_candidate import (
    owned_candidate_deployment_digest,
)
from general_ludd.infra.azure_idle_retention import (
    AzureIdleRetentionPolicy,
    AzureRetentionPreset,
)
from general_ludd.self_improve import azure_containerapp_bootstrap as bootstrap
from general_ludd.self_improve import runtime as self_improve_runtime
from general_ludd.self_improve.azure_containerapp_bootstrap import (
    AzureContainerAppBootstrapWiring,
    AzureCredentialAcquisition,
    FileAzureCredentialProvider,
    OpenBaoAzureCredentialProvider,
    build_azure_containerapp_bootstrap_wiring,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider

SUBSCRIPTION = "11111111-2222-3333-4444-555555555555"
TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT = "99999999-8888-7777-6666-555555555555"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
IMAGE = "vllm/vllm-openai@sha256:" + ("a" * 64)
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _retention_config(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "preset": "zero_cost_only",
        "max_idle_hourly_cost_microusd": 0,
        "max_idle_monthly_cost_microusd": 0,
        "max_retention_cost_microusd": 0,
        "max_retention_seconds": 21_600,
        "max_price_age_seconds": 31_536_000,
        "max_latency_age_seconds": 86_400,
        "max_cost_per_saved_hour_microusd": 0,
        "expected_next_demand_seconds": None,
    }
    result.update(overrides)
    return result


def test_runtime_trace_forwards_only_structured_infrastructure_facts() -> None:
    messages: list[str] = []
    event = MakeRuntimeEvent(
        phase="apply",
        state=MakeRuntimeState.HEARTBEAT,
        operation_digest="a" * 64,
        elapsed_seconds=73,
        event_source="opentofu_ui",
        resource_type="azapi_resource",
        action="create",
        event_kind="apply_progress",
    )

    bootstrap._runtime_trace(messages.append, "environment_terraform", event)

    assert len(messages) == 1
    assert "event_source=opentofu_ui" in messages[0]
    assert "resource_type=azapi_resource" in messages[0]
    assert "action=create" in messages[0]
    assert "event_kind=apply_progress" in messages[0]
    assert "secret_output=false" in messages[0]


def _config(**overrides: object) -> dict[str, object]:
    azure: dict[str, object] = {
        "schema_version": 1,
        "enabled": True,
        "acknowledgement": "DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        "auth_file": "/private/azure-auth.json",
        "subscription_id": SUBSCRIPTION,
        "resource_group": "gludd-models-eastus",
        "environment_name": "gludd-gpu-environment",
        "location": "eastus",
        "allowed_cidr": "192.0.2.41/32",
        "container_image": IMAGE,
        "model_name": "Qwen/Qwen2.5-0.5B-Instruct",
        "model_revision": REVISION,
        "parameter_count": 494_032_768,
        "weight_bits": 16,
        "kv_cache_mib": 2048,
        "runtime_overhead_mib": 3072,
        "peak_concurrency": 3,
        "per_replica_concurrency": 2,
        "t4_max_replicas": 4,
        "a100_max_replicas": 2,
        "t4_hourly_cost_microusd": 900_000,
        "a100_hourly_cost_microusd": 3_500_000,
        "max_hourly_cost_microusd": 4_000_000,
        "max_cost_usd": 5.0,
        "ttl_minutes": 30,
        "max_input_tokens": 512,
        "max_output_tokens": 128,
        "max_total_tokens": 640,
        "max_cost_microusd": 500_000,
        "timeout_seconds": 60.0,
        "estimated_request_cost_microusd": 100_000,
        "idle_retention": _retention_config(),
    }
    azure.update(overrides)
    return {"interval": 10, "azure_containerapp": azure}


def test_runtime_trace_surfaces_retention_decision_without_resource_identity() -> None:
    messages: list[str] = []
    bootstrap._runtime_trace(
        messages.append,
        "owned_lifecycle",
        SimpleNamespace(
            event="environment_retention_planned",
            operation_digest="d" * 64,
            retention_plan_digest="e" * 64,
            retention_seconds=21_600,
            retention_hourly_cost_microusd=0,
        ),
    )

    assert len(messages) == 1
    assert "retention_plan_digest=" + "e" * 64 in messages[0]
    assert "retention_seconds=21600" in messages[0]
    assert "retention_hourly_cost_microusd=0" in messages[0]
    assert "resource_group" not in messages[0]
    assert "secret_output=false" in messages[0]


@dataclass
class _Backend:
    name: str


class _CredentialProvider:
    def __init__(self) -> None:
        self.acquisitions = 0
        self.releases = 0

    def acquire(self) -> AzureCredentialAcquisition:
        self.acquisitions += 1
        return AzureCredentialAcquisition(
            credentials=AzureAcceleratorCredentials(
                client_id=CLIENT,
                client_secret="unit-secret",
                subscription_id=SUBSCRIPTION,
                tenant_id=TENANT,
            ),
            release=self._release,
        )

    def _release(self) -> None:
        self.releases += 1


class _Resources:
    def __init__(self, events: list[str], backend: _Backend) -> None:
        self.runtime = object()
        self.environment_runtime = object()
        self.backend_factory = object()
        self._events = events
        self._backend = backend

    def close(self) -> None:
        self._events.append("resources.close")


class _OwnedFactory:
    digest_override: str | None = None
    created: ClassVar[list[_OwnedFactory]] = []

    def __init__(
        self,
        *,
        app_policy: object,
        environment_policy: object,
        environment_runtime: object,
        app_runtime: object,
        backend_factory: object,
        resource_release: Any,
        trace_sink: Any,
        idle_retention_policy: AzureIdleRetentionPolicy,
        expected_next_demand_seconds: int | None,
    ) -> None:
        self.deployment_digest = self.digest_override or owned_candidate_deployment_digest(
            app_policy,
            environment_policy,
            idle_retention_policy,
            expected_next_demand_seconds,
        )
        del app_policy, environment_policy, environment_runtime, app_runtime
        del backend_factory, trace_sink
        self.idle_retention_policy = idle_retention_policy
        self.expected_next_demand_seconds = expected_next_demand_seconds
        self._release = resource_release
        self._backend = _Backend(f"backend-{len(self.created) + 1}")
        self.created.append(self)

    def __call__(self) -> _Backend:
        return self._backend


def _build(
    tmp_path: Path,
    config: dict[str, object] | None = None,
    *,
    credential_provider: _CredentialProvider | None = None,
    resources_builder: Any = None,
    resource_group_bootstrapper: Any = None,
) -> AzureContainerAppBootstrapWiring | None:
    provider = credential_provider or _CredentialProvider()
    group_bootstrapper = resource_group_bootstrapper or (
        lambda _policy, _credentials, **_kwargs: None
    )
    return build_azure_containerapp_bootstrap_wiring(
        tmp_path,
        _config() if config is None else config,
        progress_sink=lambda _message: None,
        credential_provider=provider,
        resources_builder=resources_builder,
        resource_group_bootstrapper=group_bootstrapper,
        owned_factory_type=_OwnedFactory,
        now=lambda: NOW,
    )


def test_same_scoped_credential_bootstraps_owned_group_before_runtime_resources(
    tmp_path: Path,
) -> None:
    order: list[str] = []
    group_calls: list[tuple[object, AzureAcceleratorCredentials]] = []
    progress: list[str] = []
    provider = _CredentialProvider()

    def group_bootstrap(
        policy: object,
        credentials: AzureAcceleratorCredentials,
        *,
        trace_sink: Any,
    ) -> None:
        order.append("group")
        group_calls.append((policy, credentials))
        trace_sink(
            AzureResourceGroupBootstrapTrace(
                AzureResourceGroupBootstrapState.CREATE_STARTED
            )
        )

    def resources(**_kwargs: object) -> _Resources:
        order.append("resources")
        return _Resources(order, _Backend("unused"))

    wiring = build_azure_containerapp_bootstrap_wiring(
        tmp_path,
        _config(),
        progress_sink=progress.append,
        credential_provider=provider,
        resources_builder=resources,
        resource_group_bootstrapper=group_bootstrap,
        owned_factory_type=_OwnedFactory,
        now=lambda: NOW,
    )
    assert isinstance(wiring, AzureContainerAppBootstrapWiring)

    wiring.bootstrap_factory()

    assert order[:2] == ["group", "resources"]
    policy, credentials = group_calls[0]
    assert isinstance(policy, AzureResourceGroupBootstrapPolicy)
    assert policy.subscription_id == SUBSCRIPTION
    assert policy.resource_group == "gludd-models-eastus"
    assert policy.location == "eastus"
    assert policy.owner_digest == wiring.environment_policy.owner_digest
    assert credentials.subscription_id == SUBSCRIPTION
    assert any(
        "component=resource_group" in event
        and "state=create_started" in event
        and "failure_class=none" in event
        for event in progress
    )
    assert "unit-secret" not in repr(progress)


def test_group_bootstrap_failure_releases_same_credential_before_resources(
    tmp_path: Path,
) -> None:
    provider = _CredentialProvider()
    wiring = _build(
        tmp_path,
        credential_provider=provider,
        resource_group_bootstrapper=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("provider-secret")
        ),
        resources_builder=lambda **_kwargs: pytest.fail("must remain unreachable"),
    )
    assert isinstance(wiring, AzureContainerAppBootstrapWiring)

    with pytest.raises(RuntimeError, match="resource-group acquisition failed") as caught:
        wiring.bootstrap_factory()

    assert "provider-secret" not in str(caught.value)
    assert provider.acquisitions == provider.releases == 1


def test_disabled_or_absent_configuration_has_no_live_capability(tmp_path: Path) -> None:
    provider = _CredentialProvider()

    assert _build(tmp_path, {}, credential_provider=provider) is None
    assert _build(
        tmp_path,
        {"azure_containerapp": {"schema_version": 1, "enabled": False}},
        credential_provider=provider,
    ) is None
    assert provider.acquisitions == 0


def test_config_derives_t4_topology_and_bootstraps_fresh_owned_sessions(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    provider = _CredentialProvider()
    resource_calls: list[dict[str, object]] = []

    def build_resources(**kwargs: object) -> _Resources:
        resource_calls.append(dict(kwargs))
        for name, event in (
            (
                "preflight_trace_sink",
                SimpleNamespace(
                    phase="readiness",
                    state="heartbeat",
                    operation_digest="c" * 64,
                    elapsed_seconds=2,
                ),
            ),
            (
                "terraform_trace_sink",
                SimpleNamespace(event="plan", candidate_identity_digest="d" * 64),
            ),
            ("environment_terraform_trace_sink", object()),
            ("backend_trace_sink", SimpleNamespace(event="request")),
        ):
            sink = kwargs[name]
            assert callable(sink)
            sink(event)
        progress = kwargs["progress_sink"]
        assert callable(progress)
        progress("phase=readiness state=heartbeat")
        return _Resources(events, _Backend("unused"))

    _OwnedFactory.created = []
    wiring = _build(
        tmp_path,
        credential_provider=provider,
        resources_builder=build_resources,
    )

    assert isinstance(wiring, AzureContainerAppBootstrapWiring)
    assert provider.acquisitions == 0
    assert wiring.topology.apps[0].workload_profile_type == T4_PROFILE.workload_profile_type
    assert wiring.topology.apps[0].max_replicas == 2
    assert wiring.app_policy.max_replicas == 2
    assert wiring.app_policy.min_replicas == 1
    assert wiring.app_policy.http_concurrent_requests == 2
    assert wiring.environment_policy.profiles[0].profile_name == "gpu-t4"
    assert wiring.environment_policy.expires_at_utc == "2026-09-07T12:30:00Z"
    assert wiring.idle_retention_policy.preset is AzureRetentionPreset.ZERO_COST_ONLY
    assert wiring.idle_retention_policy.max_retention_seconds == 21_600
    assert wiring.policy.required_providers == (
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_CONTAINER_APP,
    )
    assert (
        wiring.policy.containerapp_bootstrap_digest
        == wiring.bootstrap_factory.deployment_digest
    )

    first = wiring.bootstrap_factory()
    second = wiring.bootstrap_factory()

    assert first.name == "backend-1"
    assert second.name == "backend-2"
    assert provider.acquisitions == 2
    assert len(resource_calls) == 2
    assert all(call["policy"] is wiring.app_policy for call in resource_calls)
    assert all(call["requirement"] is wiring.requirement for call in resource_calls)
    assert all("repo_root" not in call for call in resource_calls)
    assert resource_calls[0]["work_root"] != resource_calls[0]["environment_work_root"]
    assert str(tmp_path) not in repr(wiring)
    assert all(
        owner.idle_retention_policy is wiring.idle_retention_policy
        for owner in _OwnedFactory.created
    )


def test_large_model_selects_a100_without_user_selecting_a_gpu(tmp_path: Path) -> None:
    wiring = _build(
        tmp_path,
        _config(
            model_name="Qwen/Qwen2.5-32B-Instruct",
            parameter_count=32_000_000_000,
            weight_bits=8,
            peak_concurrency=1,
            per_replica_concurrency=1,
        ),
        resources_builder=lambda **_kwargs: pytest.fail("must stay lazy"),
    )

    assert isinstance(wiring, AzureContainerAppBootstrapWiring)
    assert wiring.topology.apps[0].workload_profile_type == A100_PROFILE.workload_profile_type
    assert wiring.app_policy.workload_profile_name == "gpu-a100"


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"unexpected": "field"}, "exact schema"),
        ({"acknowledgement": "yes"}, "acknowledgement"),
        ({"t4_hourly_cost_microusd": True}, "bounded integer"),
        (
            {
                "peak_concurrency": 9,
                "t4_max_replicas": 2,
                "a100_max_replicas": 8,
            },
            "profile quota",
        ),
        ({"auth_file": "relative.json"}, "absolute"),
        ({"schema_version": True}, "schema_version"),
        ({"enabled": "true"}, "enabled"),
        ({"resource_group": " padded"}, "non-empty text"),
        ({"model_name": 7}, "non-empty text"),
        ({"weight_bits": 4.5}, "bounded integer"),
        ({"max_cost_usd": True}, "bounded number"),
        ({"timeout_seconds": "60"}, "bounded number"),
        ({"auth_file": "/private/../azure-auth.json"}, "absolute"),
        ({"idle_retention": {}}, "retention exact schema"),
        (
            {"idle_retention": _retention_config(preset="guess")},
            "retention preset",
        ),
        (
            {
                "idle_retention": _retention_config(
                    max_idle_monthly_cost_microusd=True
                )
            },
            "max_idle_monthly_cost_microusd",
        ),
        (
            {
                "idle_retention": _retention_config(
                    expected_next_demand_seconds=0
                )
            },
            "expected_next_demand_seconds",
        ),
    ],
)
def test_invalid_config_fails_before_credentials_or_paid_resources(
    tmp_path: Path,
    override: dict[str, object],
    match: str,
) -> None:
    provider = _CredentialProvider()

    with pytest.raises(ValueError, match=match):
        _build(
            tmp_path,
            _config(**override),
            credential_provider=provider,
            resources_builder=lambda **_kwargs: pytest.fail("must remain unreachable"),
        )

    assert provider.acquisitions == 0


def test_resource_construction_failure_releases_credential_once(tmp_path: Path) -> None:
    provider = _CredentialProvider()
    wiring = _build(
        tmp_path,
        credential_provider=provider,
        resources_builder=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    assert isinstance(wiring, AzureContainerAppBootstrapWiring)

    with pytest.raises(RuntimeError, match="Azure bootstrap resource construction failed"):
        wiring.bootstrap_factory()

    assert provider.acquisitions == 1
    assert provider.releases == 1


def test_credential_providers_enforce_exact_acquire_release_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = _CredentialProvider().acquire().credentials

    with pytest.raises(ValueError, match="credential contract"):
        AzureCredentialAcquisition(cast(Any, object()), lambda: None)
    with pytest.raises(ValueError, match="callable"):
        AzureCredentialAcquisition(credentials, cast(Any, object()))

    loaded: list[tuple[Path, str]] = []

    def load(path: Path, *, expected_subscription_id: str) -> AzureAcceleratorCredentials:
        loaded.append((path, expected_subscription_id))
        return credentials

    monkeypatch.setattr(bootstrap, "load_azure_accelerator_credentials", load)
    file_acquisition = FileAzureCredentialProvider(
        Path("/private/azure-auth.json"),
        SUBSCRIPTION,
    ).acquire()
    file_acquisition.release()
    assert file_acquisition.credentials is credentials
    assert loaded == [(Path("/private/azure-auth.json"), SUBSCRIPTION)]

    lease = AzureAcceleratorCredentialLease(
        credentials=credentials,
        lease_duration_seconds=60,
        renewable=False,
        _lease_id="azure/creds/gludd/test",
    )
    released: list[AzureAcceleratorCredentialLease] = []
    source = SimpleNamespace(
        acquire=lambda: lease,
        release=released.append,
    )
    openbao_acquisition = OpenBaoAzureCredentialProvider(source).acquire()
    openbao_acquisition.release()
    assert openbao_acquisition.credentials is credentials
    assert released == [lease]

    with pytest.raises(ValueError, match="exact acquire and release"):
        OpenBaoAzureCredentialProvider(cast(Any, object()))
    with pytest.raises(ValueError, match="exact acquire and release"):
        OpenBaoAzureCredentialProvider(SimpleNamespace(acquire=lambda: lease))


def test_configuration_boundaries_fail_before_credential_acquisition(
    tmp_path: Path,
) -> None:
    provider = _CredentialProvider()

    with pytest.raises(ValueError, match="must be a mapping"):
        build_azure_containerapp_bootstrap_wiring(
            tmp_path,
            cast(Any, object()),
            progress_sink=lambda _message: None,
            credential_provider=provider,
        )
    with pytest.raises(ValueError, match="configuration must be a mapping"):
        _build(
            tmp_path,
            {"azure_containerapp": "enabled"},
            credential_provider=provider,
        )
    with pytest.raises(ValueError, match="schema_version"):
        _build(
            tmp_path,
            {"azure_containerapp": {"schema_version": True, "enabled": False}},
            credential_provider=provider,
        )
    with pytest.raises(ValueError, match="progress_sink"):
        build_azure_containerapp_bootstrap_wiring(
            tmp_path,
            _config(),
            progress_sink=cast(Any, object()),
            credential_provider=provider,
        )
    file_root = tmp_path / "not-a-directory"
    file_root.write_text("bounded", encoding="utf-8")
    with pytest.raises(ValueError, match="existing directory"):
        build_azure_containerapp_bootstrap_wiring(
            file_root,
            _config(),
            progress_sink=lambda _message: None,
            credential_provider=provider,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        build_azure_containerapp_bootstrap_wiring(
            tmp_path,
            _config(),
            progress_sink=lambda _message: None,
            credential_provider=provider,
            now=lambda: datetime(2026, 9, 7, 12, 0),
        )

    assert provider.acquisitions == 0


def test_bootstrap_rejects_credential_subscription_drift_and_releases_it(
    tmp_path: Path,
) -> None:
    releases: list[str] = []

    class DriftedProvider:
        def acquire(self) -> AzureCredentialAcquisition:
            return AzureCredentialAcquisition(
                AzureAcceleratorCredentials(
                    client_id=CLIENT,
                    client_secret="unit-secret",
                    subscription_id="22222222-3333-4444-5555-666666666666",
                    tenant_id=TENANT,
                ),
                lambda: releases.append("released"),
            )

    wiring = build_azure_containerapp_bootstrap_wiring(
        tmp_path,
        _config(),
        progress_sink=lambda _message: None,
        credential_provider=DriftedProvider(),
        resources_builder=lambda **_kwargs: pytest.fail("must remain unreachable"),
        owned_factory_type=_OwnedFactory,
        now=lambda: NOW,
    )
    assert isinstance(wiring, AzureContainerAppBootstrapWiring)

    with pytest.raises(RuntimeError, match="credential scope mismatch"):
        wiring.bootstrap_factory()

    assert releases == ["released"]


def test_factory_digest_drift_releases_resources_before_return(tmp_path: Path) -> None:
    events: list[str] = []
    provider = _CredentialProvider()
    wiring = _build(
        tmp_path,
        credential_provider=provider,
        resources_builder=lambda **_kwargs: _Resources(events, _Backend("unused")),
    )
    assert isinstance(wiring, AzureContainerAppBootstrapWiring)
    _OwnedFactory.digest_override = "f" * 64

    with pytest.raises(RuntimeError, match="Azure bootstrap configuration drift"):
        wiring.bootstrap_factory()

    assert events == ["resources.close"]
    _OwnedFactory.digest_override = None


def test_managed_runner_composes_configured_bootstrap_before_live_wiring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = object()
    bootstrap_factory = object()
    calls: list[tuple[object, object]] = []
    def sink(_message: str) -> None:
        return None

    monkeypatch.setattr(
        self_improve_runtime,
        "build_azure_containerapp_bootstrap_wiring",
        lambda repo_root, config, *, progress_sink: (
            SimpleNamespace(policy=policy, bootstrap_factory=bootstrap_factory)
            if (repo_root, config, progress_sink) == (tmp_path, {"enabled": True}, sink)
            else pytest.fail("unexpected configured bootstrap arguments")
        ),
        raising=False,
    )

    def build_live(active_policy: object, **kwargs: object) -> None:
        calls.append((active_policy, kwargs["containerapp_bootstrap_factory"]))
        assert kwargs["progress_sink"] is sink
        return None

    monkeypatch.setattr(
        self_improve_runtime,
        "build_live_managed_candidate_wiring",
        build_live,
    )

    self_improve_runtime.build_managed_self_improve_runner(
        tmp_path,
        self_improve_config={"enabled": True},
        progress_sink=sink,
    )

    assert calls == [(policy, bootstrap_factory)]


def test_daemon_runner_factory_snapshots_global_self_improve_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd import daemon

    sentinel = object()
    configured: dict[str, object] = {
        "azure_containerapp": {"schema_version": 1, "enabled": False}
    }
    calls: list[tuple[Path, object]] = []

    def build(repo_root: Path, *, self_improve_config: object) -> object:
        calls.append((repo_root, self_improve_config))
        return sentinel

    monkeypatch.setattr(self_improve_runtime, "build_managed_self_improve_runner", build)
    factory = daemon._build_self_improve_runner_factory(configured)
    assert type(factory).__name__ == "ConfiguredManagedRunnerFactory"
    cast_config = configured["azure_containerapp"]
    assert isinstance(cast_config, dict)
    cast_config["enabled"] = True

    assert factory(tmp_path) is sentinel
    assert calls == [
        (
            tmp_path,
            {"azure_containerapp": {"schema_version": 1, "enabled": False}},
        )
    ]
