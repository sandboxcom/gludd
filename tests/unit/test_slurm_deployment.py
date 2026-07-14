"""Tests for Slurm model-server deployment (vllm/llamacpp sbatch templates + submit/poll).

TDD: written BEFORE the implementation. Validates:

- sbatch template variable substitution (${VAR} placeholders)
- gpu_type / gpu_count validation
- VllmSlurmDeployment.render_script + submit() shape
- LlamacppSlurmDeployment same shape
- poll_until_servable happy path (artifact file appears with servable_url)
- poll_until_servable failure path (job FAILED -> raises / returns None)
- poll timeout
- artifact file contract (servable_url, model_id, engine, slurm_job_id, port)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.infra.slurm_deployment import (
    DeploymentError,
    LlamacppSlurmDeployment,
    VllmSlurmDeployment,
    _BaseSlurmDeployment,
)

# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestVllmTemplateRendering:
    def test_render_substitutes_all_variables(self) -> None:
        dep = VllmSlurmDeployment()
        script = dep.render_script(
            model_id="meta-llama/Llama-3.1-8B-Instruct",
            gpu_count=2,
            gpu_type="a100",
            port=8000,
            max_hours=4,
            mem_gb=64,
            partition="gpu",
            max_ctx=32768,
        )
        assert "#SBATCH --partition=gpu" in script
        assert "#SBATCH --gres=gpu:2:a100" in script
        assert "#SBATCH --time=4:00:00" in script
        assert "#SBATCH --mem=64G" in script
        assert "vllm serve meta-llama/Llama-3.1-8B-Instruct" in script
        assert "--port 8000" in script
        assert "--tensor-parallel-size 2" in script
        assert "--max-model-len 32768" in script
        # The render-time placeholders (MODEL_ID, GPU_*, PORT, etc.) must all
        # be substituted. ${ARTIFACT_DIR} and ${SLURM_JOB_ID} are
        # deliberately left as runtime shell vars (exported by sbatch /
        # set by Slurm itself), so only assert our own placeholders are gone.
        for placeholder in (
            "${MODEL_ID}", "${GPU_COUNT}", "${GPU_TYPE}", "${PORT}",
            "${MAX_HOURS}", "${MEM_GB}", "${PARTITION}", "${MAX_CTX}",
            "${MODULE_LOADS}", "${EXTRA_ARGS}",
        ):
            assert placeholder not in script

    def test_render_includes_preflight_nvidia_smi(self) -> None:
        dep = VllmSlurmDeployment()
        script = dep.render_script(
            model_id="m",
            gpu_count=1,
            gpu_type="a100",
            port=8000,
            max_hours=1,
            mem_gb=16,
            partition="gpu",
            max_ctx=4096,
        )
        assert "nvidia-smi" in script

    def test_render_includes_health_probe(self) -> None:
        dep = VllmSlurmDeployment()
        script = dep.render_script(
            model_id="m",
            gpu_count=1,
            gpu_type="a100",
            port=8000,
            max_hours=1,
            mem_gb=16,
            partition="gpu",
            max_ctx=4096,
        )
        assert "/health" in script
        assert "ARTIFACT_DIR" in script

    def test_render_includes_diagnostics_on_failure(self) -> None:
        dep = VllmSlurmDeployment()
        script = dep.render_script(
            model_id="m",
            gpu_count=1,
            gpu_type="a100",
            port=8000,
            max_hours=1,
            mem_gb=16,
            partition="gpu",
            max_ctx=4096,
        )
        assert "scontrol show job" in script
        assert "sacct" in script

    def test_render_rejects_bad_gpu_count(self) -> None:
        dep = VllmSlurmDeployment()
        with pytest.raises((ValueError, TypeError)):
            dep.render_script(
                model_id="m",
                gpu_count=0,
                gpu_type="a100",
                port=8000,
                max_hours=1,
                mem_gb=16,
                partition="gpu",
                max_ctx=4096,
            )

    def test_render_rejects_bad_gpu_type(self) -> None:
        dep = VllmSlurmDeployment()
        with pytest.raises(ValueError):
            dep.render_script(
                model_id="m",
                gpu_count=1,
                gpu_type="bad;type",  # shell metachar
                port=8000,
                max_hours=1,
                mem_gb=16,
                partition="gpu",
                max_ctx=4096,
            )

    def test_render_rejects_bad_model_id(self) -> None:
        dep = VllmSlurmDeployment()
        with pytest.raises(ValueError):
            dep.render_script(
                model_id="m; rm -rf /",
                gpu_count=1,
                gpu_type="a100",
                port=8000,
                max_hours=1,
                mem_gb=16,
                partition="gpu",
                max_ctx=4096,
            )

    def test_render_module_loads_parameterized(self) -> None:
        dep = VllmSlurmDeployment()
        script = dep.render_script(
            model_id="m",
            gpu_count=1,
            gpu_type="a100",
            port=8000,
            max_hours=1,
            mem_gb=16,
            partition="gpu",
            max_ctx=4096,
            module_loads=["cuda/12.3", "python/3.11"],
        )
        assert "module load cuda/12.3" in script
        assert "module load python/3.11" in script

    def test_extra_args_passed_through(self) -> None:
        dep = VllmSlurmDeployment()
        script = dep.render_script(
            model_id="m",
            gpu_count=1,
            gpu_type="a100",
            port=8000,
            max_hours=1,
            mem_gb=16,
            partition="gpu",
            max_ctx=4096,
            extra_args=["--quantization", "awq"],
        )
        assert "--quantization awq" in script


class TestLlamacppTemplateRendering:
    def test_render_substitutes_variables(self) -> None:
        dep = LlamacppSlurmDeployment()
        script = dep.render_script(
            model_id="/data/models/foo.gguf",
            gpu_count=1,
            gpu_type="a100",
            port=8080,
            max_hours=2,
            mem_gb=32,
            partition="gpu",
            max_ctx=4096,
        )
        assert "#SBATCH --gres=gpu:1:a100" in script
        assert "#SBATCH --time=2:00:00" in script
        assert "llama_cpp.server" in script
        assert '--model "/data/models/foo.gguf"' in script
        assert "--port 8080" in script
        # Render-time placeholders are all substituted; ${ARTIFACT_DIR} and
        # ${SLURM_JOB_ID} are runtime shell vars and intentionally remain.
        for placeholder in (
            "${MODEL_ID}", "${GPU_COUNT}", "${GPU_TYPE}", "${PORT}",
            "${MAX_HOURS}", "${MEM_GB}", "${PARTITION}", "${MAX_CTX}",
        ):
            assert placeholder not in script


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_vllm_submit_returns_job_id(self) -> None:
        dep = VllmSlurmDeployment()
        with patch.object(dep._adapter, "submit", return_value="12345") as mock_submit:
            job_id = dep.submit(
                model_id="meta-llama/Llama-3.1-8B-Instruct",
                gpu_count=1,
                gpu_type="a100",
                port=8000,
                max_hours=1,
                mem_gb=16,
                partition="gpu",
                max_ctx=4096,
                artifact_dir="/scratch/gludd/job-12345",
            )
        assert job_id == "12345"
        # Adapter.submit was called with the rendered script body + Slurm metadata
        kwargs = mock_submit.call_args.kwargs
        assert "vllm serve" in kwargs["command"]
        assert kwargs["partition"] == "gpu"

    def test_vllm_submit_validates_artifact_dir(self) -> None:
        dep = VllmSlurmDeployment()
        with pytest.raises(ValueError):
            dep.submit(
                model_id="m",
                gpu_count=1,
                gpu_type="a100",
                port=8000,
                max_hours=1,
                mem_gb=16,
                partition="gpu",
                max_ctx=4096,
                artifact_dir="bad\npath",  # newline injection
            )


# ---------------------------------------------------------------------------
# Poll until servable
# ---------------------------------------------------------------------------


class TestPollUntilServable:
    def test_returns_url_when_artifact_appears(self, tmp_path: Path) -> None:
        dep = VllmSlurmDeployment()
        artifact_dir = tmp_path / "art"
        artifact_dir.mkdir()
        # Simulate the sbatch script writing the artifact after first poll
        call_count = {"n": 0}

        def fake_status(job_id: str) -> MagicMock:
            call_count["n"] += 1
            info = MagicMock()
            info.state.value = "RUNNING"
            return info

        def fake_read(path: Path) -> str | None:
            if call_count["n"] >= 2:
                return json.dumps(
                    {
                        "servable_url": "http://cn01:8000",
                        "model_id": "m",
                        "engine": "vllm",
                        "slurm_job_id": "12345",
                        "port": 8000,
                        "started_at": 1719600000,
                    }
                )
            return None

        with patch.object(dep._adapter, "status", side_effect=fake_status), \
             patch.object(dep, "_read_artifact", side_effect=fake_read):
            url = dep.poll_until_servable(
                job_id="12345",
                artifact_dir=str(artifact_dir),
                timeout=10,
                poll_interval=0.01,
            )
        assert url == "http://cn01:8000"

    def test_raises_on_job_failed(self, tmp_path: Path) -> None:
        dep = VllmSlurmDeployment()
        info = MagicMock()
        info.state.value = "FAILED"

        with patch.object(dep._adapter, "status", return_value=info), pytest.raises(DeploymentError):
            dep.poll_until_servable(
                job_id="12345",
                artifact_dir=str(tmp_path),
                timeout=2,
                poll_interval=0.01,
            )

    def test_raises_on_timeout(self, tmp_path: Path) -> None:
        dep = VllmSlurmDeployment()
        info = MagicMock()
        info.state.value = "RUNNING"

        with patch.object(dep._adapter, "status", return_value=info), \
             patch.object(dep, "_read_artifact", return_value=None), pytest.raises(DeploymentError, match="timeout"):
            dep.poll_until_servable(
                job_id="12345",
                artifact_dir=str(tmp_path),
                timeout=0.05,
                poll_interval=0.01,
            )

    def test_surfaces_failure_diagnostics(self, tmp_path: Path) -> None:
        dep = VllmSlurmDeployment()
        artifact_dir = tmp_path / "art"
        artifact_dir.mkdir()
        info = MagicMock()
        info.state.value = "FAILED"
        # Artifact written with servable_url=null + diagnostics
        failure_json = json.dumps(
            {
                "servable_url": None,
                "model_id": "m",
                "engine": "vllm",
                "slurm_job_id": "12345",
                "error": "nvidia-smi exit 1",
                "diagnostics": "no GPU detected",
            }
        )

        with (
            patch.object(dep._adapter, "status", return_value=info),
            patch.object(dep, "_read_artifact", return_value=failure_json),
            pytest.raises(DeploymentError, match="nvidia-smi"),
        ):
            dep.poll_until_servable(
                job_id="12345",
                artifact_dir=str(artifact_dir),
                timeout=2,
                poll_interval=0.01,
            )


# ---------------------------------------------------------------------------
# Sbatch file on disk
# ---------------------------------------------------------------------------


class TestSbatchFiles:
    def test_vllm_sbatch_file_exists(self) -> None:
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        assert (repo / "infra" / "slurm" / "vllm.sbatch").is_file()

    def test_llamacpp_sbatch_file_exists(self) -> None:
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        assert (repo / "infra" / "slurm" / "llamacpp.sbatch").is_file()

    def test_vllm_sbatch_has_placeholders(self) -> None:
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        text = (repo / "infra" / "slurm" / "vllm.sbatch").read_text()
        assert "${GPU_COUNT}" in text
        assert "${GPU_TYPE}" in text
        assert "${MODEL_ID}" in text
        assert "${PORT}" in text
        assert "${MAX_HOURS}" in text
        assert "${MEM_GB}" in text
        assert "${PARTITION}" in text
        assert "${ARTIFACT_DIR}" in text


# ---------------------------------------------------------------------------
# _BaseSlurmDeployment — abstract base
# ---------------------------------------------------------------------------
class TestBaseSlurmDeploymentAbstract:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            _BaseSlurmDeployment()  # type: ignore[abstract]
        assert True  # reached — ABC prevents direct instantiation
