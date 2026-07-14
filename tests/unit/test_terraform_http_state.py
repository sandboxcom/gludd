"""Unit tests for terraform_http_backend + http-kind paths in terraform_state.

Covers:
- StateBackendSelector.select() with api_url config
- render_backend_block() for all three kinds
- generate_http_backend_block()
- write_http_backend_file()
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.infra.terraform_http_backend import (
    generate_http_backend_block,
    write_http_backend_file,
)
from general_ludd.infra.terraform_state import (
    StateBackendConfig,
    StateBackendSelector,
    render_backend_block,
)


def _make_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if overrides:
        cfg.update(overrides)
    return cfg


def _fake_compute_config(max_cost_usd: float = 10.0) -> Any:
    """Minimal stub with just the attribute selector accesses."""
    cfg = MagicMock()
    cfg.max_cost_usd = max_cost_usd
    return cfg


# ── StateBackendSelector tests ──────────────────────────────────────


def test_select_with_api_url_returns_http_kind() -> None:
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=MagicMock(),
        config=_make_config({"api_url": "https://example.com"}),
    )
    result = selector.select(_fake_compute_config(), deployment_id="dep-1")
    assert result.kind == "http"


def test_select_without_api_url_falls_back_to_local() -> None:
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=MagicMock(),
    )
    result = selector.select(_fake_compute_config(max_cost_usd=10.0))
    assert result.kind == "local"


def test_select_with_api_url_includes_username_password() -> None:
    selector = StateBackendSelector(
        openbao_client=MagicMock(),
        secrets_manager=MagicMock(),
        config=_make_config({
            "api_url": "https://gludd.example.com",
            "username": "alice",
            "password": "s3cret",
        }),
    )
    result = selector.select(_fake_compute_config(), deployment_id="dep-42")
    assert result.kind == "http"
    assert result.username == "alice"
    assert result.password == "s3cret"
    assert result.lock_address == result.path
    assert result.unlock_address == result.path


# ── render_backend_block tests ──────────────────────────────────────


def test_render_backend_block_http_kind() -> None:
    cfg = StateBackendConfig(
        kind="http",
        path="https://api.example.com/api/terraform/state/dep-1",
        lock_address="https://api.example.com/api/terraform/state/dep-1",
        unlock_address="https://api.example.com/api/terraform/state/dep-1",
    )
    block = render_backend_block(cfg)
    assert 'backend "http"' in block
    assert 'address = "https://api.example.com/api/terraform/state/dep-1"' in block
    assert 'lock_address = "https://api.example.com/api/terraform/state/dep-1"' in block
    assert 'unlock_address = "https://api.example.com/api/terraform/state/dep-1"' in block


def test_render_backend_block_http_kind_with_credentials() -> None:
    cfg = StateBackendConfig(
        kind="http",
        path="https://api.example.com/api/terraform/state/dep-1",
        lock_address="https://api.example.com/api/terraform/state/dep-1",
        unlock_address="https://api.example.com/api/terraform/state/dep-1",
        username="bob",
        password="p4ss",
    )
    block = render_backend_block(cfg)
    assert 'username = "bob"' in block
    assert 'password = "p4ss"' in block


def test_render_backend_block_local_kind() -> None:
    cfg = StateBackendConfig(kind="local", path="terraform.tfstate")
    block = render_backend_block(cfg)
    assert 'backend "local"' in block
    assert 'path = "terraform.tfstate"' in block


def test_render_backend_block_openbao_kv_kind() -> None:
    cfg = StateBackendConfig(
        kind="openbao_kv",
        path="secret/data/gludd/tfstate/abc",
    )
    block = render_backend_block(cfg)
    assert 'backend "http"' in block
    assert "secret/data/gludd/tfstate/abc" in block
    assert "lock_address" not in block
    assert "unlock_address" not in block


def test_render_backend_block_unknown_kind_raises() -> None:
    cfg = StateBackendConfig(kind="nonesuch", path="/x")
    with pytest.raises(ValueError, match="unknown state backend kind"):
        render_backend_block(cfg)


# ── generate_http_backend_block tests ───────────────────────────────


def test_generate_http_backend_block_has_address_lock_unlock() -> None:
    block = generate_http_backend_block(
        stack_name="my-stack",
        api_url="https://gludd.example.com",
    )
    assert 'backend "http"' in block
    assert "/api/terraform/state/my-stack" in block
    assert 'address = "https://gludd.example.com/api/terraform/state/my-stack"' in block
    assert 'lock_address = "https://gludd.example.com/api/terraform/state/my-stack"' in block
    assert 'unlock_address = "https://gludd.example.com/api/terraform/state/my-stack"' in block


def test_generate_http_backend_block_lock_same_as_address() -> None:
    block = generate_http_backend_block("prod", "https://api.example.com")
    lines = block.splitlines()
    addr_line = next(line for line in lines if "address" in line)
    lock_line = next(line for line in lines if "lock_address" in line)
    unlock_line = next(line for line in lines if "unlock_address" in line)
    addr_val = addr_line.split('"')[1]
    lock_val = lock_line.split('"')[1]
    unlock_val = unlock_line.split('"')[1]
    assert lock_val == addr_val
    assert unlock_val == addr_val


# ── write_http_backend_file tests ───────────────────────────────────


def test_write_http_backend_file_writes_and_returns_path(tmp_path: Any) -> None:
    stack_dir = str(tmp_path)
    result_path = write_http_backend_file(
        stack_name="staging",
        stack_dir=stack_dir,
        api_url="https://gludd.example.com",
    )
    assert result_path == os.path.join(stack_dir, "backend.tf")
    assert os.path.isfile(result_path)
    with open(result_path) as f:
        content = f.read()
    assert 'backend "http"' in content
    assert "/api/terraform/state/staging" in content
