"""Phase 0 round-trip validation — TERRAFORM_INFRA_STRUCTURE.md §7 / §9.

Smoke-tests the HCL emitted by ``TerraformGenerator`` for the implemented
providers against the real ``terraform`` binary:

* ``terraform fmt``   — rewrites the file to canonical form. Crucially, this
  is a full syntactic parse: any malformed HCL (unclosed block, bad string
  escape, invalid attribute) makes fmt fail non-zero. We use the rewriting
  form rather than ``-check`` because the existing renderer's output is not
  yet fmt-canonical (a Phase 4 cleanup item); the round-trip proves the HCL
  is *parseable*, which is the Phase 0 bar.
* ``terraform validate`` — semantic check; requires ``terraform init`` which
  downloads providers. Run for a single representative provider; SKIPS
  cleanly if ``terraform`` is not on PATH or ``init`` cannot complete (no
  network, registry rate-limit, provider not in registry).

``terraform`` is NOT a hard CI dependency — absence of the binary is the
common case and the suite must remain green in that state.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.terraform import TerraformGenerator


def _base_config(provider: ComputeProvider, **overrides: object) -> ComputeConfig:
    defaults: dict[str, object] = {
        "provider": provider,
        "gpu_type": GPUType.T4,
        "model_name": "meta-llama/Llama-2-7b-hf",
        "allowed_cidr": "0.0.0.0/0",
    }
    defaults.update(overrides)
    return cast(Any, ComputeConfig)(**defaults)


_IMPLEMENTED_PROVIDERS = [
    pytest.param(ComputeProvider.AWS, id="aws"),
    pytest.param(ComputeProvider.GCP, id="gcp"),
    pytest.param(ComputeProvider.AZURE, id="azure"),
    pytest.param(ComputeProvider.RUNPOD, id="runpod"),
    pytest.param(ComputeProvider.VAST_AI, id="vast-ai"),
]


def _terraform_available() -> bool:
    return shutil.which("terraform") is not None


@pytest.mark.skipif(not _terraform_available(), reason="terraform binary not on PATH")
@pytest.mark.parametrize("provider", _IMPLEMENTED_PROVIDERS)
def test_generated_hcl_is_parseable_by_fmt(provider: ComputeProvider) -> None:
    """``terraform fmt`` must accept (parse) the HCL for every implemented provider.

    ``terraform fmt`` runs a full syntactic parse and exits non-zero on
    malformed HCL (unclosed block, bad escape, invalid token). It does not
    touch the provider registry. Failure here means the generator emits
    syntactically broken HCL regardless of cloud credentials.

    We use the rewriting form (not ``-check``) because the existing
    renderer's whitespace/indentation is not yet canonical — a cosmetic
    Phase 4 cleanup. The Phase 0 bar is *parseability*, which this test
    enforces.
    """
    cfg = _base_config(provider)
    hcl = TerraformGenerator().generate(cfg)

    with tempfile.TemporaryDirectory() as td:
        tf = Path(td) / "main.tf"
        tf.write_text(hcl)
        proc = subprocess.run(
            ["terraform", "fmt", "-diff", str(tf)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"terraform fmt failed to parse {provider.value} HCL:\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n"
            f"--- generated HCL ---\n{hcl}\n"
        )


@pytest.mark.skipif(not _terraform_available(), reason="terraform binary not on PATH")
def test_aws_hcl_passes_validate() -> None:
    """Round-trip the AWS provider's HCL through ``terraform validate``.

    Validate is the semantic check (catches duplicate resource names, bad
    attribute types, unresolved references) that ``fmt`` cannot. It requires
    ``terraform init`` to fetch the ``hashicorp/aws`` plugin, so the test
    SKIPS when init cannot complete — never fails — to keep CI hermetic.

    Single-provider scope is intentional: validate's value is proving the
    generator emits semantically valid HCL, not re-proving it across every
    provider (which would each need their own plugin download).
    """
    cfg = _base_config(ComputeProvider.AWS)
    hcl = TerraformGenerator().generate(cfg)

    with tempfile.TemporaryDirectory() as td:
        tf = Path(td) / "main.tf"
        tf.write_text(hcl)

        # Normalise formatting first so cosmetic diffs don't mask semantic errors.
        fmt = subprocess.run(
            ["terraform", "fmt", "-list=false", str(tf)],
            cwd=td,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert fmt.returncode == 0, f"fmt failed to parse: {fmt.stderr}\n{hcl}"

        init = subprocess.run(
            ["terraform", "init", "-backend=false", "-input=false"],
            cwd=td,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if init.returncode != 0:
            pytest.skip(
                "terraform init failed (no network / registry unavailable): "
                f"{init.stderr[:400]}"
            )

        validate = subprocess.run(
            ["terraform", "validate", "-no-color"],
            cwd=td,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert validate.returncode == 0, (
            f"terraform validate failed for AWS:\n"
            f"--- stdout ---\n{validate.stdout}\n"
            f"--- stderr ---\n{validate.stderr}\n"
            f"--- generated HCL ---\n{hcl}\n"
        )


def test_skip_guard_returns_false_when_terraform_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the availability helper so a refactor cannot silently invert it.

    The skipif markers on the round-trip tests capture
    ``_terraform_available()`` at import time; this test pins the helper so
    the suite never starts running the round-trips in an environment without
    ``terraform``.
    """
    import tests.unit.test_terraform_validate as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    assert mod._terraform_available() is False

    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    assert mod._terraform_available() is True
