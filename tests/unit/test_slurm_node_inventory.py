from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.infra.slurm import SlurmAdapter, SlurmNotInstalledError


def test_remote_node_inventory_reuses_authenticated_slurm_rest_client() -> None:
    adapter = SlurmAdapter(api_url="https://slurm.example", auth_token="opaque")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"nodes": [{"name": "gpu-01", "gres": "gpu:a100:4"}]}

    with patch.object(adapter, "_request", return_value=response) as request:
        nodes = adapter.list_nodes()

    assert nodes == [{"name": "gpu-01", "gres": "gpu:a100:4"}]
    request.assert_called_once_with(
        "GET",
        "https://slurm.example/slurm/v0.0.40/nodes",
        headers=adapter._headers(),
        timeout=15.0,
    )


def test_remote_node_inventory_rejects_http_failure() -> None:
    adapter = SlurmAdapter(api_url="https://slurm.example", auth_token="opaque")
    response = MagicMock(status_code=503, text="unavailable")
    with (
        patch.object(adapter, "_request", return_value=response),
        pytest.raises(RuntimeError, match="node inventory failed"),
    ):
        adapter.list_nodes()


def test_local_node_inventory_uses_sinfo_json() -> None:
    adapter = SlurmAdapter()
    result = MagicMock(
        returncode=0,
        stdout='{"nodes":[{"name":"gpu-01","gres":"gpu:a100:4"}]}',
        stderr="",
    )
    with patch("general_ludd.infra.slurm.subprocess.run", return_value=result) as run:
        nodes = adapter.list_nodes()
    assert nodes[0]["name"] == "gpu-01"
    run.assert_called_once_with(
        ["sinfo", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_local_node_inventory_reports_missing_slurm() -> None:
    adapter = SlurmAdapter()
    with (
        patch(
            "general_ludd.infra.slurm.subprocess.run",
            side_effect=FileNotFoundError,
        ),
        pytest.raises(SlurmNotInstalledError, match="sinfo"),
    ):
        adapter.list_nodes()


@pytest.mark.parametrize(
    "result",
    [
        MagicMock(returncode=1, stdout="", stderr="permission denied"),
        MagicMock(returncode=0, stdout="not-json", stderr=""),
        MagicMock(returncode=0, stdout='{"nodes":"wrong"}', stderr=""),
    ],
)
def test_local_node_inventory_rejects_command_or_schema_failure(result: MagicMock) -> None:
    adapter = SlurmAdapter()
    with (
        patch("general_ludd.infra.slurm.subprocess.run", return_value=result),
        pytest.raises(RuntimeError, match="node inventory"),
    ):
        adapter.list_nodes()


def test_local_node_inventory_timeout_is_explicit() -> None:
    adapter = SlurmAdapter()
    with (
        patch(
            "general_ludd.infra.slurm.subprocess.run",
            side_effect=subprocess.TimeoutExpired("sinfo", 30),
        ),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        adapter.list_nodes()
