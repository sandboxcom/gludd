"""Runtime protocol checks for the owned model-worker composition seams."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import general_ludd.infra.azure_gpu_model_worker_service as service_module
import general_ludd.infra.model_worker_gateway_dispatch as dispatch_module
import general_ludd.models.gateway as gateway_module
from general_ludd.models.gateway import ModelResponse


class _RuntimeBoundaryProbe:
    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        return ModelResponse(content=profile_id, model_name="probe")

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Iterator[object]:
        return iter(())

    def close(self) -> None:
        return None

    def acquire(self) -> object:
        return object()

    def release(self, lease: object) -> None:
        return None

    def run_playbook(self, playbook_name: str, **kwargs: Any) -> dict[str, Any]:
        return {"playbook_name": playbook_name}


def test_owned_model_worker_protocols_support_runtime_boundary_validation() -> None:
    probe = _RuntimeBoundaryProbe()

    assert isinstance(probe, dispatch_module._EndpointCaller)
    assert isinstance(probe, service_module._CredentialSource)
    assert isinstance(probe, service_module._PlaybookRunner)
    assert isinstance(probe, gateway_module._RuntimeModelGateway)
