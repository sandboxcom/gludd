"""Slurm-backed model-server deployment.

Renders sbatch script templates (`infra/slurm/vllm.sbatch`,
`infra/slurm/llamacpp.sbatch`) with operator-supplied parameters, submits them
via :class:`general_ludd.infra.slurm.SlurmAdapter`, and polls for the
``servable.json`` artifact the batch script writes on the shared filesystem.

This module composes with (does not extend) ``SlurmAdapter``: the adapter
remains the single authority for argv-validated ``sbatch``/``sacct``/``squeue``
calls. The deployment classes here are responsible for *what* to launch and
*when* it is ready; the adapter is responsible for *how* a Slurm command is
issued safely.

See ``docs/design/MODEL_SERVING_DEPLOYMENT.md`` for the design rationale
(Slurm batch script is the right path when the operator has a Slurm cluster).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobState,
)

logger = logging.getLogger(__name__)

# Repository root (this file is at src/general_ludd/infra/slurm_deployment.py).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_VLLM_TEMPLATE = _REPO_ROOT / "infra" / "slurm" / "vllm.sbatch"
_DEFAULT_LLMACPP_TEMPLATE = _REPO_ROOT / "infra" / "slurm" / "llamacpp.sbatch"

# Conservative charset for GPU type / partition / module tokens embedded into
# the rendered script. Mirrors the slurm._NAME_RE philosophy: no leading dash,
# no whitespace, no shell metacharacters, no newlines (a newline would inject
# a fresh #SBATCH directive).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/-]*$")

# Model ids for vLLM are HF hub ids (`org/name`), local file paths
# (`/data/models/foo.gguf`), or short names (`m`). Allow slash, dot, dash,
# underscore, alphanumerics, and a leading slash for absolute paths. Still
# reject whitespace, control chars, and any shell metacharacter that could
# break out of the `vllm serve ${MODEL_ID}` argv.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_/][A-Za-z0-9_./\-]*$")

# Artifact dir: a filesystem path. Same rules as SlurmAdapter's `_require_output`
# — no newlines (would inject directives if interpolated into a heredoc), no NUL.
_PATH_FORBIDDEN = frozenset("\n\r\x00")


class DeploymentError(Exception):
    """Raised when a Slurm model-server deployment fails to become servable."""


def _validate_token(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or not _TOKEN_RE.match(value):
        raise ValueError(
            f"invalid {name} {value!r}: must match a safe charset "
            "(no leading dash, whitespace, newlines or shell metacharacters)"
        )
    return value


def _validate_model_id(value: str) -> str:
    if not isinstance(value, str) or not value or not _MODEL_ID_RE.match(value):
        raise ValueError(
            f"invalid model_id {value!r}: must be alphanumeric + slash/dot/dash/underscore"
        )
    return value


def _validate_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    # Reject bool explicitly (bool is a subclass of int).
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")
    if not (minimum <= value <= maximum):
        raise ValueError(f"{name} out of range [{minimum}, {maximum}]: {value}")
    return value


def _validate_path(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if any(ch in _PATH_FORBIDDEN for ch in value):
        raise ValueError(f"invalid {name} {value!r}: no newlines/NUL")
    return value


def _validate_extra_args(value: list[str]) -> list[str]:
    out: list[str] = []
    for arg in value:
        if not isinstance(arg, str) or not arg:
            raise ValueError("extra_args entries must be non-empty strings")
        if any(ch in _PATH_FORBIDDEN for ch in arg):
            raise ValueError(f"extra_args entry contains forbidden characters: {arg!r}")
        out.append(arg)
    return out


def _validate_module_loads(value: list[str]) -> list[str]:
    out: list[str] = []
    for mod in value:
        # module names: `cuda/12.3`, `python/3.11`. Same charset as a Slurm name
        # plus the slash separator.
        if not isinstance(mod, str) or not _TOKEN_RE.match(mod):
            raise ValueError(f"invalid module load {mod!r}")
        out.append(mod)
    return out


class _BaseSlurmDeployment:
    """Shared render/submit/poll logic for vLLM and llama.cpp deployments."""

    engine: str = ""

    def __init__(
        self,
        adapter: SlurmAdapter | None = None,
        template_path: Path | None = None,
    ) -> None:
        self._adapter = adapter if adapter is not None else SlurmAdapter()
        self._template_path = template_path if template_path is not None else self._default_template()

    @classmethod
    def _default_template(cls) -> Path:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Template rendering
    # ------------------------------------------------------------------

    def render_script(
        self,
        *,
        model_id: str,
        gpu_count: int,
        gpu_type: str,
        port: int,
        max_hours: int,
        mem_gb: int,
        partition: str,
        max_ctx: int,
        module_loads: list[str] | None = None,
        extra_args: list[str] | None = None,
        artifact_dir: str = "${ARTIFACT_DIR}",
    ) -> str:
        """Render the sbatch template by substituting ${VAR} placeholders.

        Validates every operator-supplied value fail-closed BEFORE
        interpolation. ``artifact_dir`` defaults to the literal
        ``${ARTIFACT_DIR}`` so the rendered script still references the env
        var the submitter exports (the poller supplies a concrete path only
        at submit time).
        """
        model_id = _validate_model_id(model_id)
        gpu_count = _validate_int(gpu_count, "gpu_count", minimum=1, maximum=64)
        gpu_type = _validate_token(gpu_type, "gpu_type")
        port = _validate_int(port, "port", minimum=1, maximum=65535)
        max_hours = _validate_int(max_hours, "max_hours", minimum=1, maximum=240)
        mem_gb = _validate_int(mem_gb, "mem_gb", minimum=1, maximum=4096)
        partition = _validate_token(partition, "partition")
        max_ctx = _validate_int(max_ctx, "max_ctx", minimum=512, maximum=2_000_000)
        module_loads = _validate_module_loads(list(module_loads or []))
        extra_args = _validate_extra_args(list(extra_args or []))

        template = self._template_path.read_text()
        return self._substitute(
            template,
            model_id=model_id,
            gpu_count=gpu_count,
            gpu_type=gpu_type,
            port=port,
            max_hours=max_hours,
            mem_gb=mem_gb,
            partition=partition,
            max_ctx=max_ctx,
            module_loads=module_loads,
            extra_args=extra_args,
            artifact_dir=artifact_dir,
        )

    @staticmethod
    def _substitute(
        template: str,
        **values: Any,
    ) -> str:
        """Substitute ${VAR} placeholders with validated values.

        ``module_loads`` and ``extra_args`` are rendered into multi-line shell
        fragments here (validated upstream). All other values are stringified.
        """
        out = template
        module_fragment = "\n".join(f"module load {m}" for m in values["module_loads"])
        extra_fragment = " ".join(values["extra_args"])
        scalar_values = {k: v for k, v in values.items() if k not in ("module_loads", "extra_args")}
        scalar_values["MODULE_LOADS"] = module_fragment
        scalar_values["EXTRA_ARGS"] = extra_fragment
        for key, val in scalar_values.items():
            out = out.replace("${" + key.upper() + "}", str(val))
        return out

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def submit(
        self,
        *,
        model_id: str,
        gpu_count: int,
        gpu_type: str,
        port: int,
        max_hours: int,
        mem_gb: int,
        partition: str,
        max_ctx: int,
        artifact_dir: str,
        module_loads: list[str] | None = None,
        extra_args: list[str] | None = None,
        job_name: str | None = None,
    ) -> str:
        """Render + submit. Returns the Slurm job id.

        ``artifact_dir`` is the concrete on-shared-filesystem path the batch
        script writes its ``servable.json`` to. It is exported in the job
        environment as ``ARTIFACT_DIR`` so the same variable name works
        whether the template is rendered inline or run as-is.
        """
        _validate_path(artifact_dir, "artifact_dir")
        script = self.render_script(
            model_id=model_id,
            gpu_count=gpu_count,
            gpu_type=gpu_type,
            port=port,
            max_hours=max_hours,
            mem_gb=mem_gb,
            partition=partition,
            max_ctx=max_ctx,
            module_loads=module_loads,
            extra_args=extra_args,
            artifact_dir=artifact_dir,
        )
        name = job_name or f"gludd-{self.engine}-serve"
        job_id = self._adapter.submit(
            command=script,
            job_name=name,
            partition=partition,
            time_limit=f"{max_hours}:00:00",
            memory=f"{mem_gb}G",
            gpus=f"{gpu_count}:{gpu_type}",
        )
        logger.info(
            "submitted %s slurm deployment job_id=%s model=%s gpus=%d:%s",
            self.engine,
            job_id,
            model_id,
            gpu_count,
            gpu_type,
        )
        return job_id

    # ------------------------------------------------------------------
    # Poll
    # ------------------------------------------------------------------

    def poll_until_servable(
        self,
        *,
        job_id: str,
        artifact_dir: str,
        timeout: float = 300.0,
        poll_interval: float = 5.0,
    ) -> str:
        """Block until the artifact file appears with a servable_url.

        Returns the servable URL. Raises :class:`DeploymentError` on timeout,
        job failure, or a failure artifact (``servable_url: null`` +
        ``error`` field).
        """
        deadline = time.time() + timeout
        artifact_path = Path(artifact_dir) / "servable.json"
        last_state = "UNKNOWN"
        while time.time() < deadline:
            info = self._adapter.status(job_id)
            last_state = info.state.value if hasattr(info.state, "value") else str(info.state)
            # Terminal failure states short-circuit.
            if info.state in (
                SlurmJobState.FAILED,
                SlurmJobState.CANCELLED,
                SlurmJobState.TIMEOUT,
                SlurmJobState.NODE_FAIL,
            ):
                # Check whether the script wrote diagnostics before dying.
                artifact = self._read_artifact(artifact_path)
                if artifact is not None:
                    data = json.loads(artifact)
                    err = data.get("error") or data.get("diagnostics") or last_state
                    raise DeploymentError(
                        f"slurm job {job_id} entered terminal state {last_state}: {err}"
                    )
                raise DeploymentError(
                    f"slurm job {job_id} entered terminal state {last_state} with no artifact"
                )
            artifact = self._read_artifact(artifact_path)
            if artifact is not None:
                data = json.loads(artifact)
                url = data.get("servable_url")
                if url:
                    logger.info(
                        "slurm deployment job_id=%s servable at %s", job_id, url
                    )
                    return str(url)
                # Artifact present but servable_url is null — terminal failure.
                err = data.get("error") or data.get("diagnostics") or "no servable_url"
                raise DeploymentError(
                    f"slurm job {job_id} wrote failure artifact: {err}"
                )
            time.sleep(poll_interval)
        raise DeploymentError(
            f"timeout after {timeout}s waiting for slurm job {job_id} "
            f"(last_state={last_state}, artifact={artifact_path})"
        )

    @staticmethod
    def _read_artifact(path: Path) -> str | None:
        """Return the artifact file contents, or None if it does not exist yet."""
        try:
            return path.read_text()
        except FileNotFoundError:
            return None
        except OSError:
            return None


class VllmSlurmDeployment(_BaseSlurmDeployment):
    """Deploy ``vllm serve`` as a Slurm batch job."""

    engine = "vllm"

    @classmethod
    def _default_template(cls) -> Path:
        return _DEFAULT_VLLM_TEMPLATE


class LlamacppSlurmDeployment(_BaseSlurmDeployment):
    """Deploy ``llama_cpp.server`` as a Slurm batch job."""

    engine = "llamacpp"

    @classmethod
    def _default_template(cls) -> Path:
        return _DEFAULT_LLMACPP_TEMPLATE
