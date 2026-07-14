"""Tests for terraform_http_backend.py — HTTP backend block generation and state URL construction.

Pin behaviour:
  - generate_http_backend_block() produces valid HCL with address/lock/unlock
  - Lock and unlock URLs are identical to address (lock-as-state pattern)
  - write_http_backend_file() creates backend.tf and returns absolute path
  - Stack name is correctly embedded in URL
  - Trailing slash on api_url is handled correctly
  - StateBackendSelector http-kind priority over openbao_kv and local
  - render_backend_block() http kind with/without credentials
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


class TestGenerateHttpBackendBlock:
    """Structural and behavioral tests for generate_http_backend_block()."""

    def test_block_contains_http_backend_keyword(self) -> None:
        block = generate_http_backend_block("my-stack", "https://api.example.com")
        assert 'backend "http"' in block
        assert "terraform {" in block

    def test_address_lock_unlock_all_same_url(self) -> None:
        block = generate_http_backend_block("prod", "https://gludd.example.com")
        lines = block.splitlines()
        addr_line = [l for l in lines if "address" in l][0]
        lock_line = [l for l in lines if "lock_address" in l][0]
        unlock_line = [l for l in lines if "unlock_address" in l][0]
        addr_val = addr_line.split('"')[1]
        lock_val = lock_line.split('"')[1]
        unlock_val = unlock_line.split('"')[1]
        assert lock_val == addr_val
        assert unlock_val == addr_val
        assert "/api/terraform/state/prod" in addr_val

    def test_stack_name_embedded_in_endpoint(self) -> None:
        block = generate_http_backend_block("staging-cluster", "https://api.example.com")
        assert "staging-cluster" in block
        assert "/api/terraform/state/staging-cluster" in block

    def test_api_url_with_trailing_slash(self) -> None:
        block = generate_http_backend_block("x", "https://api.example.com/")
        assert "/api/terraform/state/x" in block

    def test_psk_parameter_accepted_and_ignored_for_now(self) -> None:
        block = generate_http_backend_block("x", "https://a.com", psk="secret")
        assert 'backend "http"' in block

    def test_block_is_valid_hcl_with_closing_braces(self) -> None:
        block = generate_http_backend_block("s", "https://a.com")
        assert block.count("{") == block.count("}")
        assert block.endswith("}\n")

    def test_generate_is_identical_for_same_inputs(self) -> None:
        a = generate_http_backend_block("same", "https://example.com")
        b = generate_http_backend_block("same", "https://example.com")
        assert a == b

    def test_urls_differ_for_different_stacks(self) -> None:
        a = generate_http_backend_block("stack-a", "https://api.example.com")
        b = generate_http_backend_block("stack-b", "https://api.example.com")
        assert a != b
        assert "stack-a" in a
        assert "stack-b" in b


class TestWriteHttpBackendFile:
    """Tests for write_http_backend_file()."""

    def test_writes_backend_tf_with_correct_content(self, tmp_path: Any) -> None:
        stack_dir = str(tmp_path / "tf")
        os.makedirs(stack_dir)
        result = write_http_backend_file("qa", stack_dir, "https://gludd.example.com")
        assert result == os.path.join(stack_dir, "backend.tf")
        with open(result) as f:
            content = f.read()
        assert 'backend "http"' in content
        assert "/api/terraform/state/qa" in content

    def test_returns_absolute_path(self, tmp_path: Any) -> None:
        result = write_http_backend_file("x", str(tmp_path), "https://a.com")
        assert os.path.isabs(result)

    def test_overwrites_existing_backend_tf(self, tmp_path: Any) -> None:
        stack_dir = str(tmp_path / "tf")
        os.makedirs(stack_dir)
        old = os.path.join(stack_dir, "backend.tf")
        with open(old, "w") as f:
            f.write("old content")
        result = write_http_backend_file("new", stack_dir, "https://b.com")
        with open(result) as f:
            content = f.read()
        assert "new" in content
        assert "old content" not in content
        assert "/api/terraform/state/new" in content

    def test_writes_to_existing_directory(self, tmp_path: Any) -> None:
        stack_dir = str(tmp_path / "tf")
        os.makedirs(stack_dir)
        result = write_http_backend_file("foo", stack_dir, "https://api.example.com")
        assert os.path.isfile(result)

    def test_psk_passed_through_to_generate(self, tmp_path: Any) -> None:
        stack_dir = str(tmp_path / "tf")
        os.makedirs(stack_dir)
        result = write_http_backend_file(
            "x", stack_dir, "https://api.example.com", psk="my-psk",
        )
        with open(result) as f:
            content = f.read()
        assert 'backend "http"' in content


class TestStateBackendSelectorHttpPriority:
    """http backend is selected before openbao_kv when api_url is configured."""

    def test_http_wins_over_openbao_even_when_openbao_reachable(self) -> None:
        reachable = MagicMock()
        reachable.health_check.return_value = True
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=reachable,
            config={"api_url": "https://gludd.example.com"},
        )
        cfg = MagicMock()
        cfg.max_cost_usd = 500.0
        result = selector.select(cfg, deployment_id="dep-1")
        assert result.kind == "http"

    def test_http_wins_over_local_when_api_url_set(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
            config={"api_url": "https://gludd.example.com"},
        )
        result = selector.select(MagicMock(), deployment_id="dep-x")
        assert result.kind == "http"
        assert "dep-x" in result.path

    def test_http_backend_includes_lock_and_unlock_addresses(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
            config={"api_url": "https://gludd.example.com"},
        )
        cfg = MagicMock()
        cfg.max_cost_usd = 0.0
        result = selector.select(cfg, deployment_id="dep-42")
        assert result.kind == "http"
        assert result.lock_address == result.path
        assert result.unlock_address == result.path

    def test_http_backend_includes_username_password_when_configured(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
            config={
                "api_url": "https://gludd.example.com",
                "username": "alice",
                "password": "s3cret",
            },
        )
        result = selector.select(MagicMock(), deployment_id="dep-1")
        assert result.username == "alice"
        assert result.password == "s3cret"

    def test_http_backend_omits_credentials_when_not_configured(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
            config={"api_url": "https://gludd.example.com"},
        )
        result = selector.select(MagicMock(), deployment_id="dep-1")
        assert result.username == ""
        assert result.password == ""

    def test_no_api_url_falls_through_to_cost_based_selection(self) -> None:
        reachable = MagicMock()
        reachable.health_check.return_value = True
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=reachable,
            config={},
        )
        cfg = MagicMock()
        cfg.max_cost_usd = 500.0
        result = selector.select(cfg, deployment_id="dep-1")
        assert result.kind == "openbao_kv"

    def test_no_api_url_and_low_cost_returns_local(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
            config={},
        )
        cfg = MagicMock()
        cfg.max_cost_usd = 10.0
        result = selector.select(cfg)
        assert result.kind == "local"

    def test_deployment_id_auto_generated_when_not_provided(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
            config={"api_url": "https://gludd.example.com"},
        )
        cfg = MagicMock()
        cfg.max_cost_usd = 0.0
        result = selector.select(cfg)
        assert result.kind == "http"
        assert len(result.path.split("/")[-1]) == 12


class TestRenderBackendBlockHttp:
    """render_backend_block() for http kind."""

    def test_http_block_with_minimal_config(self) -> None:
        cfg = StateBackendConfig(
            kind="http",
            path="https://api.example.com/state/dep-1",
            lock_address="https://api.example.com/state/dep-1",
            unlock_address="https://api.example.com/state/dep-1",
        )
        block = render_backend_block(cfg)
        assert 'backend "http"' in block
        assert 'address = "https://api.example.com/state/dep-1"' in block
        assert "username" not in block
        assert "password" not in block

    def test_http_block_with_credentials(self) -> None:
        cfg = StateBackendConfig(
            kind="http",
            path="https://api.example.com/state/dep-1",
            lock_address="https://api.example.com/state/dep-1",
            unlock_address="https://api.example.com/state/dep-1",
            username="bob",
            password="p4ss",
        )
        block = render_backend_block(cfg)
        assert 'username = "bob"' in block
        assert 'password = "p4ss"' in block

    def test_http_block_username_only_no_password(self) -> None:
        cfg = StateBackendConfig(
            kind="http",
            path="https://api.example.com/state/dep-1",
            lock_address="https://api.example.com/state/dep-1",
            unlock_address="https://api.example.com/state/dep-1",
            username="bob",
        )
        block = render_backend_block(cfg)
        assert 'username = "bob"' in block
        assert "password" not in block

    def test_http_block_password_only_no_username(self) -> None:
        cfg = StateBackendConfig(
            kind="http",
            path="https://api.example.com/state/dep-1",
            lock_address="https://api.example.com/state/dep-1",
            unlock_address="https://api.example.com/state/dep-1",
            password="p4ss",
        )
        block = render_backend_block(cfg)
        assert 'password = "p4ss"' in block
        assert "username" not in block
