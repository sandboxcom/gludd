from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.infra.slurm import SlurmAdapter
from general_ludd.routers.slurm import register


def test_slurm_hardware_endpoint_returns_live_scheduler_inventory() -> None:
    app = FastAPI()
    register(app, {})
    nodes = [
        {
            "name": "node-01",
            "state": "IDLE",
            "partitions": ["gpu"],
            "gres": "gpu:future-profile:2,tpu:v6e:4",
        }
    ]

    with (
        patch.object(SlurmAdapter, "list_nodes", return_value=nodes) as list_nodes,
        TestClient(app) as client,
    ):
        response = client.get("/admin/slurm/hardware")

    assert response.status_code == 200
    assert response.json()["total_count"] == 6
    assert {item["kind"] for item in response.json()["resources"]} == {"gpu", "tpu"}
    list_nodes.assert_called_once()


def test_slurm_hardware_endpoint_maps_discovery_failure() -> None:
    app = FastAPI()
    register(app, {})
    with (
        patch.object(SlurmAdapter, "list_nodes", side_effect=RuntimeError("bad payload")),
        TestClient(app) as client,
    ):
        response = client.get("/admin/slurm/hardware")
    assert response.status_code == 500
    assert response.json()["detail"] == "Slurm hardware discovery request failed"
