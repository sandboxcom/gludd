"""Tests for local_inference validation functions and edge cases not covered elsewhere."""

from __future__ import annotations

import pytest

from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServer,
    LocalServerConfig,
    _has_shell_metachars,
    _validate_extra_args,
    _validate_host,
    _validate_model,
    _validate_port,
)

# _build_command is a method on LocalInferenceManager, not a standalone function


class TestValidateModel:
    def test_valid_model(self):
        result = _validate_model("llama-3-8b")
        assert result == "llama-3-8b"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _validate_model("")

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            _validate_model(123)

    def test_leading_dash_raises(self):
        with pytest.raises(ValueError, match="flag injection"):
            _validate_model("--help")

    def test_shell_metachar_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_model("model;rm-rf")


class TestValidateHost:
    def test_accepts_localhost(self):
        assert _validate_host("localhost") == "localhost"

    def test_accepts_127_0_0_1(self):
        assert _validate_host("127.0.0.1") == "127.0.0.1"

    def test_rejects_non_loopback_by_default(self):
        with pytest.raises(ValueError, match="not a loopback"):
            _validate_host("0.0.0.0")

    def test_accepts_non_loopback_with_flag(self):
        result = _validate_host("0.0.0.0", allow_nonloopback=True)
        assert result == "0.0.0.0"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            _validate_host("")

    def test_rejects_shell_chars(self):
        with pytest.raises(ValueError):
            _validate_host("host;cmd")


class TestValidatePort:
    def test_valid_port(self):
        assert _validate_port(8000) == 8000

    def test_rejects_bool(self):
        with pytest.raises(ValueError, match="must be an int"):
            _validate_port(True)

    def test_rejects_string(self):
        with pytest.raises(ValueError, match="must be an int"):
            _validate_port("8000")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="out of range"):
            _validate_port(0)

    def test_rejects_above_65535(self):
        with pytest.raises(ValueError, match="out of range"):
            _validate_port(99999)


class TestValidateExtraArgs:
    def test_valid_args(self):
        result = _validate_extra_args(["--flag", "value"])
        assert result == ["--flag", "value"]

    def test_empty_list(self):
        assert _validate_extra_args([]) == []

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be strings"):
            _validate_extra_args([123])

    def test_rejects_shell_chars(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_extra_args(["safe", "bad;cmd"])


class TestHasShellMetachars:
    def test_no_metachars(self):
        assert _has_shell_metachars("safe") is False

    def test_semicolon(self):
        assert _has_shell_metachars("x;y") is True

    def test_pipe(self):
        assert _has_shell_metachars("x|y") is True

    def test_dollar(self):
        assert _has_shell_metachars("$(whoami)") is True


class TestLocalServerConfig:
    def test_allow_nonloopback_default(self):
        cfg = LocalServerConfig()
        assert cfg.allow_nonloopback is False

    def test_startup_timeout_default(self):
        cfg = LocalServerConfig()
        assert cfg.startup_timeout == 120.0


class TestBuildCommandSlurm:
    def test_slurm_engine_builds_sbatch_command(self):
        cfg = LocalServerConfig(
            engine="slurm",
            model_name="test-model",
            host="localhost",
            port=8000,
            gpu_layers=40,
            context_size=4096,
            allow_nonloopback=True,
            extra_args=["--partition=gpu"],
        )
        mgr = LocalInferenceManager()
        cmd = mgr._build_command(cfg)
        assert cmd[0] == "sbatch"
        assert any("--partition" in arg for arg in cmd)
        assert "--wrap" in cmd

    def test_unsupported_engine_raises(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="ollama", model_name="m")
        with pytest.raises(ValueError, match="Unsupported engine"):
            mgr._build_command(cfg)


class TestLocalServer:
    def test_default_attributes(self):
        cfg = LocalServerConfig()
        s = LocalServer(server_id="test-1", config=cfg)
        assert s.status == "stopped"
        assert s.started_at == 0.0
        assert s.pid is None
        assert s.process is None
