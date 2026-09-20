"""Compatibility tests for split model-worker dispatch support."""

from __future__ import annotations

import general_ludd.infra.model_worker_gateway_dispatch as dispatch
import general_ludd.infra.model_worker_gateway_dispatch_support as support


def test_dispatch_support_is_owned_once_and_reexported() -> None:
    assert dispatch.EndpointGatewayFactory is support.EndpointGatewayFactory
    assert dispatch.ModelWorkerDispatchError is support.ModelWorkerDispatchError
    assert dispatch._EndpointCaller is support._EndpointCaller


def test_dispatch_support_retains_exact_public_surface() -> None:
    assert set(support.__all__) == {
        "EndpointGatewayFactory",
        "ModelWorkerDispatchError",
    }
