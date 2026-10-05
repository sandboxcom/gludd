"""Compatibility tests for split model-worker dispatch support."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    "phase",
    [
        None,
        "",
        "x" * 2_049,
        "bad\x00phase",
        "bad\rphase",
        "bad\nphase",
    ],
)
def test_dispatch_error_rejects_every_unbounded_phase_shape(phase: object) -> None:
    """Censored dispatch errors accept only bounded single-line phase names."""
    with pytest.raises(ValueError):
        support.ModelWorkerDispatchError(phase)  # type: ignore[arg-type]
