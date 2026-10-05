"""Tests for publishing owned model workers through the universal gateway."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

import general_ludd.infra.model_worker_gateway_dispatch as dispatch_module
from general_ludd.infra.model_worker_gateway_dispatch import (
    ModelWorkerDispatchError,
    ModelWorkerGatewayDispatcher,
)
from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerEndpoint,
    ModelWorkerHost,
    ProvisionedModelWorkerPool,
)
from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _deployment(
    deployment_id: str = "deployment-old",
    *,
    addresses: tuple[str, ...] = ("10.42.1.4", "10.42.1.5"),
) -> ProvisionedModelWorkerPool:
    hosts = tuple(
        ModelWorkerHost(
            host_id=f"worker-{index}",
            address=address,
            ansible_user="gludd",
            ssh_private_key_path="/run/gludd/keys/worker",
            endpoint_url=f"http://{address}:8000/v1",
        )
        for index, address in enumerate(addresses)
    )
    return ProvisionedModelWorkerPool(
        deployment_id=deployment_id,
        hosts=hosts,
        owned_resource_ids=tuple(f"/owned/{deployment_id}/{index}" for index in range(len(hosts))),
    )


def _endpoints(deployment: ProvisionedModelWorkerPool) -> tuple[ModelWorkerEndpoint, ...]:
    return tuple(
        ModelWorkerEndpoint(
            host_id=host.host_id,
            endpoint_url=host.endpoint_url,
            service_name="gludd-model-worker-aaaaaaaaaaaa.service",
            attestation_digest=_DIGEST_A if index == 0 else _DIGEST_B,
        )
        for index, host in enumerate(deployment.hosts)
    )


@dataclass
class _EndpointCaller:
    endpoint_url: str
    profile_id: str
    calls: list[tuple[str, list[dict[str, str]], dict[str, Any]]] = field(
        default_factory=list
    )
    closed: bool = False
    entered: threading.Event | None = None
    release: threading.Event | None = None
    fail_close: bool = False

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        self.calls.append((profile_id, messages, kwargs))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(2.0)
        return ModelResponse(content=self.endpoint_url, model_name="owned-model")

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Iterator[object]:
        self.calls.append((profile_id, messages, kwargs))
        yield f"chunk:{self.endpoint_url}"

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("private endpoint detail")
        self.closed = True


@dataclass
class _CallerFactory:
    callers: list[_EndpointCaller] = field(default_factory=list)
    entered: threading.Event | None = None
    release: threading.Event | None = None
    fail_close: bool = False

    def __call__(
        self,
        endpoint_url: str,
        endpoint_profile_id: str,
        model_name: str,
    ) -> _EndpointCaller:
        assert model_name == "org/model-q4"
        caller = _EndpointCaller(
            endpoint_url=endpoint_url,
            profile_id=endpoint_profile_id,
            entered=self.entered,
            release=self.release,
            fail_close=self.fail_close,
        )
        self.callers.append(caller)
        return caller


def _dispatcher(
    gateway: ModelGateway,
    factory: _CallerFactory,
    *,
    profile_id: str = "owned-azure-model",
    drain_timeout_seconds: float = 2.0,
) -> ModelWorkerGatewayDispatcher:
    return ModelWorkerGatewayDispatcher(
        gateway,
        profile_id=profile_id,
        model_name="org/model-q4",
        endpoint_gateway_factory=factory,
        drain_timeout_seconds=drain_timeout_seconds,
        profile_options={
            "context_window": 32_768,
            "max_input_tokens": 8_192,
            "max_output_tokens": 1_024,
            "role_names": ["chemistry", "firmware", "self_improvement"],
        },
    )


def test_publish_registers_one_universal_profile_and_round_robins_endpoints() -> None:
    gateway = ModelGateway()
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment()

    lease = dispatcher.publish(deployment, _endpoints(deployment))

    profile = gateway.get_profile("owned-azure-model")
    assert profile is not None
    assert profile.model_name == "org/model-q4"
    assert profile.api_metered is False
    assert profile.context_window == 32_768
    assert profile.role_names == ["chemistry", "firmware", "self_improvement"]
    assert len(lease) == 64
    assert lease.isascii() and lease.isalnum()
    assert deployment.deployment_id not in lease

    first = gateway.call_model(
        "owned-azure-model",
        [{"role": "user", "content": "design a polymer"}],
        budget_remaining=1.0,
    )
    second = gateway.call_model(
        "owned-azure-model",
        [{"role": "user", "content": "write firmware"}],
        requested_max_output_tokens=128,
    )
    third = gateway.call_model(
        "owned-azure-model",
        [{"role": "user", "content": "improve code"}],
    )

    assert [first.content, second.content, third.content] == [
        deployment.hosts[0].endpoint_url,
        deployment.hosts[1].endpoint_url,
        deployment.hosts[0].endpoint_url,
    ]
    assert factory.callers[0].calls[0][0].startswith("local-owned-worker-")
    assert factory.callers[1].calls[0][2]["requested_max_output_tokens"] == 128

    dispatcher.withdraw(lease)

    assert gateway.get_profile("owned-azure-model") is None
    assert all(caller.closed for caller in factory.callers)
    with pytest.raises(ValueError, match="not found"):
        gateway.call_model("owned-azure-model", [{"role": "user", "content": "x"}])


def test_replacement_is_published_before_old_generation_drains() -> None:
    gateway = ModelGateway()
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    old = _deployment("old", addresses=("10.0.0.1",))
    new = _deployment("new", addresses=("10.0.0.2",))

    old_lease = dispatcher.publish(old, _endpoints(old))
    assert gateway.call_model("owned-azure-model", []).content == old.hosts[0].endpoint_url

    new_lease = dispatcher.publish(new, _endpoints(new))
    assert gateway.call_model("owned-azure-model", []).content == new.hosts[0].endpoint_url

    dispatcher.withdraw(old_lease)
    assert gateway.call_model("owned-azure-model", []).content == new.hosts[0].endpoint_url
    assert factory.callers[0].closed is True
    assert factory.callers[1].closed is False

    dispatcher.withdraw(new_lease)
    assert gateway.get_profile("owned-azure-model") is None


def test_withdrawing_replacement_falls_back_to_prior_generation() -> None:
    gateway = ModelGateway()
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    old = _deployment("old", addresses=("10.0.0.1",))
    replacement = _deployment("replacement", addresses=("10.0.0.2",))
    old_lease = dispatcher.publish(old, _endpoints(old))
    replacement_lease = dispatcher.publish(replacement, _endpoints(replacement))

    dispatcher.withdraw(replacement_lease)

    assert gateway.call_model("owned-azure-model", []).content == old.hosts[0].endpoint_url
    dispatcher.withdraw(old_lease)


def test_withdraw_waits_for_in_flight_call_before_closing_endpoint() -> None:
    entered = threading.Event()
    release = threading.Event()
    factory = _CallerFactory(entered=entered, release=release)
    gateway = ModelGateway()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))
    lease = dispatcher.publish(deployment, _endpoints(deployment))
    response: list[ModelResponse] = []
    withdrawn = threading.Event()

    call_thread = threading.Thread(
        target=lambda: response.append(gateway.call_model("owned-azure-model", [])),
        daemon=True,
    )
    call_thread.start()
    assert entered.wait(1.0)

    def _withdraw() -> None:
        dispatcher.withdraw(lease)
        withdrawn.set()

    withdraw_thread = threading.Thread(target=_withdraw, daemon=True)
    withdraw_thread.start()
    assert not withdrawn.wait(0.05)
    assert factory.callers[0].closed is False

    release.set()
    call_thread.join(1.0)
    withdraw_thread.join(1.0)

    assert response[0].content == deployment.hosts[0].endpoint_url
    assert withdrawn.is_set()
    assert factory.callers[0].closed is True


def test_stream_holds_generation_until_iterator_is_closed() -> None:
    gateway = ModelGateway()
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))
    lease = dispatcher.publish(deployment, _endpoints(deployment))

    stream = gateway.call_model_stream("owned-azure-model", [])
    assert next(stream) == f"chunk:{deployment.hosts[0].endpoint_url}"
    stream.close()
    dispatcher.withdraw(lease)

    assert factory.callers[0].closed is True


@pytest.mark.parametrize(
    "endpoint_url",
    [
        "http://10.0.0.99:8000/v1",
        "ftp://10.0.0.1:8000/v1",
        "http://user:password@10.0.0.1:8000/v1",  # pragma: allowlist secret
        "http://10.0.0.1:8000/v1?token=secret",
        "http://10.0.0.1:8000/v1#fragment",
    ],
)
def test_publish_rejects_endpoint_not_bound_to_exact_owned_host(endpoint_url: str) -> None:
    gateway = ModelGateway()
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))
    endpoint = _endpoints(deployment)[0]
    unsafe = ModelWorkerEndpoint(
        host_id=endpoint.host_id,
        endpoint_url=endpoint_url,
        service_name=endpoint.service_name,
        attestation_digest=endpoint.attestation_digest,
    )

    with pytest.raises(ModelWorkerDispatchError, match="endpoint_contract"):
        dispatcher.publish(deployment, (unsafe,))

    assert gateway.get_profile("owned-azure-model") is None
    assert factory.callers == []


def test_publish_refuses_profile_hijack_and_closes_unpublished_callers() -> None:
    gateway = ModelGateway()
    original = gateway.add_profile(
        "owned-azure-model",
        provider="openai",
        model="operator-owned",
        api_metered=False,
    )
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))

    with pytest.raises(ModelWorkerDispatchError, match="publish"):
        dispatcher.publish(deployment, _endpoints(deployment))

    assert gateway.get_profile("owned-azure-model") is original
    assert len(factory.callers) == 1
    assert factory.callers[0].closed is True


def test_duplicate_deployment_and_unknown_lease_fail_closed() -> None:
    gateway = ModelGateway()
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))
    lease = dispatcher.publish(deployment, _endpoints(deployment))

    with pytest.raises(ModelWorkerDispatchError, match="duplicate_deployment"):
        dispatcher.publish(deployment, _endpoints(deployment))
    with pytest.raises(ModelWorkerDispatchError, match="lease"):
        dispatcher.withdraw("f" * 64)

    dispatcher.withdraw(lease)
    dispatcher.withdraw(lease)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", ""),
        ("model_name", "bad\nmodel"),
        ("drain_timeout_seconds", 0),
        ("drain_timeout_seconds", True),
    ],
)
def test_constructor_rejects_unbounded_or_invalid_policy(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "profile_id": "owned-azure-model",
        "model_name": "org/model-q4",
        "endpoint_gateway_factory": _CallerFactory(),
        "drain_timeout_seconds": 2.0,
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        ModelWorkerGatewayDispatcher(ModelGateway(), **kwargs)  # type: ignore[arg-type]


def test_profile_options_cannot_override_owned_identity_or_transport() -> None:
    with pytest.raises(ValueError, match="profile_options"):
        ModelWorkerGatewayDispatcher(
            ModelGateway(),
            profile_id="owned-azure-model",
            model_name="org/model-q4",
            endpoint_gateway_factory=_CallerFactory(),
            profile_options={"api_base_alias": "foreign"},
        )


def test_default_endpoint_gateway_and_static_resolver_are_owned_and_closeable() -> None:
    resolver = dispatch_module._StaticEndpointResolver("alias", "http://10.0.0.1:8000/v1")
    assert resolver.resolve("alias") == "http://10.0.0.1:8000/v1"
    assert resolver.resolve("other") is None
    gateway = ModelGateway()
    dispatcher = ModelWorkerGatewayDispatcher(
        gateway,
        profile_id="owned-default-model",
        model_name="org/model-q4",
    )
    deployment = _deployment(addresses=("10.0.0.1",))

    lease = dispatcher.publish(deployment, _endpoints(deployment))
    dispatcher.withdraw(lease)

    assert gateway.get_profile("owned-default-model") is None


@pytest.mark.parametrize(
    ("gateway", "factory", "options"),
    [
        (object(), _CallerFactory(), None),
        (ModelGateway(), object(), None),
        (ModelGateway(), _CallerFactory(), {1: "not-text"}),
        (ModelGateway(), _CallerFactory(), {"not_a_profile_field": 1}),
    ],
)
def test_constructor_rejects_invalid_dependencies_and_option_keys(
    gateway: object,
    factory: object,
    options: object,
) -> None:
    with pytest.raises(ValueError):
        ModelWorkerGatewayDispatcher(
            gateway,  # type: ignore[arg-type]
            profile_id="owned-azure-model",
            model_name="org/model-q4",
            endpoint_gateway_factory=factory,  # type: ignore[arg-type]
            profile_options=options,  # type: ignore[arg-type]
        )


def test_endpoint_contract_rejects_wrong_shapes_counts_and_invalid_ports() -> None:
    gateway = ModelGateway()
    dispatcher = _dispatcher(gateway, _CallerFactory())
    deployment = _deployment(addresses=("10.0.0.1",))
    endpoint = _endpoints(deployment)[0]

    with pytest.raises(ModelWorkerDispatchError, match="deployment_contract"):
        dispatcher.publish(object(), (endpoint,))  # type: ignore[arg-type]
    with pytest.raises(ModelWorkerDispatchError, match="endpoint_contract"):
        dispatcher.publish(deployment, ())
    with pytest.raises(ModelWorkerDispatchError, match="endpoint_contract"):
        dispatcher.publish(deployment, (endpoint, endpoint))

    invalid_port_endpoint = ModelWorkerEndpoint(
        host_id=endpoint.host_id,
        endpoint_url="http://10.0.0.1:99999/v1",
        service_name=endpoint.service_name,
        attestation_digest=endpoint.attestation_digest,
    )
    invalid_port_deployment = ProvisionedModelWorkerPool(
        deployment_id="invalid-port",
        hosts=(
            ModelWorkerHost(
                host_id="worker-0",
                address="10.0.0.1",
                ansible_user="gludd",
                ssh_private_key_path="/run/gludd/keys/worker",
                endpoint_url="http://10.0.0.1:99999/v1",
            ),
        ),
        owned_resource_ids=("/owned/invalid-port/0",),
    )
    with pytest.raises(ModelWorkerDispatchError, match="endpoint_contract"):
        dispatcher.publish(invalid_port_deployment, (invalid_port_endpoint,))


def test_endpoint_factory_failure_closes_every_partially_built_route() -> None:
    built: list[_EndpointCaller] = []

    def _failing_factory(url: str, profile_id: str, model_name: str) -> _EndpointCaller:
        if built:
            raise RuntimeError("private factory failure")
        caller = _EndpointCaller(url, profile_id)
        built.append(caller)
        return caller

    dispatcher = ModelWorkerGatewayDispatcher(
        ModelGateway(),
        profile_id="owned-azure-model",
        model_name="org/model-q4",
        endpoint_gateway_factory=_failing_factory,
    )
    deployment = _deployment()

    with pytest.raises(ModelWorkerDispatchError, match="endpoint_runtime"):
        dispatcher.publish(deployment, _endpoints(deployment))

    assert built[0].closed is True


def test_endpoint_factory_rejects_an_incomplete_caller_contract() -> None:
    dispatcher = ModelWorkerGatewayDispatcher(
        ModelGateway(),
        profile_id="owned-azure-model",
        model_name="org/model-q4",
        endpoint_gateway_factory=lambda *_: object(),  # type: ignore[arg-type,return-value]
    )
    deployment = _deployment(addresses=("10.0.0.1",))

    with pytest.raises(ModelWorkerDispatchError, match="endpoint_runtime"):
        dispatcher.publish(deployment, _endpoints(deployment))


def test_drain_timeout_is_censored_and_retry_finishes_after_call_exits() -> None:
    entered = threading.Event()
    release = threading.Event()
    factory = _CallerFactory(entered=entered, release=release)
    gateway = ModelGateway()
    dispatcher = _dispatcher(
        gateway,
        factory,
        drain_timeout_seconds=0.01,
    )
    deployment = _deployment(addresses=("10.0.0.1",))
    lease = dispatcher.publish(deployment, _endpoints(deployment))
    call_thread = threading.Thread(
        target=lambda: gateway.call_model("owned-azure-model", []),
        daemon=True,
    )
    call_thread.start()
    assert entered.wait(1.0)

    with pytest.raises(ModelWorkerDispatchError, match="drain_timeout") as caught:
        dispatcher.withdraw(lease)
    assert "10.0.0.1" not in str(caught.value)

    release.set()
    call_thread.join(1.0)
    dispatcher.withdraw(lease)
    assert factory.callers[0].closed is True


def test_profile_ownership_loss_refuses_to_close_the_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = ModelGateway()
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))
    lease = dispatcher.publish(deployment, _endpoints(deployment))
    original_remove = gateway.remove_runtime_profile
    monkeypatch.setattr(gateway, "remove_runtime_profile", lambda *_: False)

    with pytest.raises(ModelWorkerDispatchError, match="profile_ownership"):
        dispatcher.withdraw(lease)
    assert factory.callers[0].closed is False

    monkeypatch.setattr(gateway, "remove_runtime_profile", original_remove)
    dispatcher.withdraw(lease)


def test_endpoint_close_failure_is_censored_after_route_removal() -> None:
    gateway = ModelGateway()
    factory = _CallerFactory(fail_close=True)
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))
    lease = dispatcher.publish(deployment, _endpoints(deployment))

    with pytest.raises(ModelWorkerDispatchError, match="endpoint_close") as caught:
        dispatcher.withdraw(lease)

    assert "private endpoint detail" not in str(caught.value)
    assert gateway.get_profile("owned-azure-model") is None
    dispatcher.withdraw(lease)


def test_direct_dispatch_calls_fail_closed_without_an_active_owned_route() -> None:
    dispatcher = _dispatcher(ModelGateway(), _CallerFactory())

    with pytest.raises(ModelWorkerDispatchError, match="profile"):
        dispatcher.call_model("other-profile", [])
    with pytest.raises(ModelWorkerDispatchError, match="unavailable"):
        dispatcher.call_model("owned-azure-model", [])
    dispatcher._release_route("unknown")


def test_gateway_rolls_back_runtime_profile_when_observer_rejects_publication() -> None:
    class _RejectingBus:
        def publish(self, event: object) -> None:
            raise RuntimeError("private observer detail")

    gateway = ModelGateway(event_bus=_RejectingBus())
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))

    with pytest.raises(ModelWorkerDispatchError, match="publish") as caught:
        dispatcher.publish(deployment, _endpoints(deployment))

    assert "private observer detail" not in str(caught.value)
    assert gateway.get_profile("owned-azure-model") is None
    assert factory.callers[0].closed is True


def test_gateway_runtime_profile_can_only_be_removed_by_exact_owner() -> None:
    gateway = ModelGateway()
    owner = _dispatcher(gateway, _CallerFactory())
    stranger = _dispatcher(gateway, _CallerFactory(), profile_id="other-model")
    profile = ModelProfile(
        model_profile_id="owned-azure-model",
        model_name="org/model-q4",
        api_metered=False,
        enabled=True,
    )

    gateway.register_runtime_profile(profile, owner)

    assert gateway.remove_runtime_profile("owned-azure-model", stranger) is False
    with pytest.raises(ValueError, match="runtime-owned"):
        gateway.add_profile("owned-azure-model", model="replacement", api_metered=False)
    with pytest.raises(ValueError, match="runtime-owned"):
        gateway.remove_profile("owned-azure-model")
    assert gateway.remove_runtime_profile("owned-azure-model", owner) is True
    assert gateway.get_profile("owned-azure-model") is None


def test_gateway_rejects_invalid_or_recursive_runtime_owner() -> None:
    gateway = ModelGateway()
    profile = ModelProfile(
        model_profile_id="owned-azure-model",
        model_name="org/model-q4",
        api_metered=False,
        enabled=True,
    )

    with pytest.raises(ValueError, match="runtime"):
        gateway.register_runtime_profile(profile, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="runtime"):
        gateway.register_runtime_profile(profile, gateway)  # type: ignore[arg-type]


def test_withdraw_censors_observer_failure_and_can_retry_profile_removal() -> None:
    class _ToggleBus:
        fail_remove = True

        def publish(self, event: object) -> None:
            if self.fail_remove and type(event).__name__ == "ModelRemovedEvent":
                raise RuntimeError("private observer detail")

    bus = _ToggleBus()
    gateway = ModelGateway(event_bus=bus)
    factory = _CallerFactory()
    dispatcher = _dispatcher(gateway, factory)
    deployment = _deployment(addresses=("10.0.0.1",))
    lease = dispatcher.publish(deployment, _endpoints(deployment))

    with pytest.raises(ModelWorkerDispatchError, match="profile_remove") as caught:
        dispatcher.withdraw(lease)

    assert "private observer detail" not in str(caught.value)
    assert gateway.get_profile("owned-azure-model") is not None
    assert factory.callers[0].closed is False

    bus.fail_remove = False
    dispatcher.withdraw(lease)
    assert gateway.get_profile("owned-azure-model") is None
