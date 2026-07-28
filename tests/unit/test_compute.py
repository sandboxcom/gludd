"""Azure compute model and API registration coverage."""

from __future__ import annotations

from fastapi import FastAPI

from general_ludd.infra.compute import ComputeProvider, GPUType
from general_ludd.routers.compute import register


def test_azure_a100_shapes_are_public_compute_values() -> None:
    assert ComputeProvider("azure") is ComputeProvider.AZURE
    assert GPUType("a100_40") is GPUType.A100_40
    assert GPUType("a100_80") is GPUType.A100_80


def test_compute_router_registers_read_only_azure_preflight() -> None:
    app = FastAPI()
    register(app, {})

    routes = {
        (route.path, frozenset(route.methods or set()))
        for route in app.routes
    }
    assert (
        "/admin/compute/azure/preflight",
        frozenset({"POST"}),
    ) in routes
