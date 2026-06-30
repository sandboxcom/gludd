"""TDD for terraform state backend selection (design doc §10 #2).

A deployment above the cost threshold with a reachable OpenBao gets a remote
backend that persists ``.tfstate`` into OpenBao's KV store; everything else
falls back to local state in the per-deployment tempdir.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.terraform_state import (
    StateBackendConfig,
    StateBackendSelector,
    render_backend_block,
)


def _config(max_cost_usd: float) -> ComputeConfig:
    return ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=GPUType.T4,
        gpu_count=1,
        engine=InferenceEngine.VLLM,
        model_name="org/model",
        max_cost_usd=max_cost_usd,
    )


def _reachable_openbao() -> Any:
    """Return a SecretsManager stub whose health_check reports reachable."""
    mgr = MagicMock()
    mgr.health_check.return_value = True
    return mgr


def test_local_state_when_cost_below_threshold() -> None:
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=_reachable_openbao(),
    )
    cfg = selector.select(_config(max_cost_usd=10.0))
    assert cfg.kind == "local"
    assert "terraform.tfstate" in cfg.path


def test_remote_state_when_cost_above_threshold_and_openbao_reachable() -> None:
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=_reachable_openbao(),
    )
    cfg = selector.select(_config(max_cost_usd=100.0), deployment_id="dep-123")
    assert cfg.kind == "openbao_kv"
    assert cfg.path.startswith("secret/data/gludd/tfstate/")
    assert "dep-123" in cfg.path


def test_local_state_when_openbao_unreachable() -> None:
    unreachable = MagicMock()
    unreachable.health_check.return_value = False
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=unreachable,
    )
    cfg = selector.select(_config(max_cost_usd=100.0))
    # OpenBao unreachable -> must fall back to local even though cost is high.
    assert cfg.kind == "local"


def test_render_backend_block_local() -> None:
    cfg = StateBackendConfig(kind="local", path="terraform.tfstate")
    block = render_backend_block(cfg)
    assert 'backend "local"' in block
    assert 'path = "terraform.tfstate"' in block


def test_render_backend_block_openbao_kv() -> None:
    cfg = StateBackendConfig(
        kind="openbao_kv",
        path="secret/data/gludd/tfstate/dep-123",
    )
    block = render_backend_block(cfg)
    assert 'backend "http"' in block
    assert "secret/data/gludd/tfstate/dep-123" in block


def test_deployment_id_in_remote_path() -> None:
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=_reachable_openbao(),
    )
    a = selector.select(_config(max_cost_usd=100.0), deployment_id="aaa")
    b = selector.select(_config(max_cost_usd=100.0), deployment_id="bbb")
    assert a.path != b.path
    assert "aaa" in a.path
    assert "bbb" in b.path


def test_threshold_default_is_50_usd() -> None:
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=_reachable_openbao(),
    )
    assert selector.cost_threshold_usd == 50.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
