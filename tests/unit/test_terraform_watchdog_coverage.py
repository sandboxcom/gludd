"""Verify every Terraform worker stack composes the gpu-cost-watchdog module.

Coverage invariants:
  - Every stack under infra/terraform/stacks/ has a main.tf.
  - Every worker main.tf MUST contain a ``module "gpu_cost_watchdog"`` block
    whose ``source = "../../modules/gpu-cost-watchdog"``.
  - Every worker stack MUST reference ``module.gpu_cost_watchdog.user_data``
    in at least one output (main.tf or outputs.tf).
  - Control-plane-only stacks MUST declare their runtime class and contain no
    embedded model worker.
"""

from pathlib import Path

STACKS_DIR = Path("infra/terraform/stacks")
WATCHDOG_SOURCE = '"../../modules/gpu-cost-watchdog"'
MODULE_REF = 'module "gpu_cost_watchdog"'
CONTROL_PLANE_STACKS = {"azure-container-app-environment"}


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_stacks() -> list[Path]:
    """Return Paths to every ``<stack>/main.tf`` under STACKS_DIR."""
    stacks = sorted(STACKS_DIR.glob("*/main.tf"))
    assert len(stacks) >= 18, f"expected >=18 stacks, found {len(stacks)}"
    return stacks


def all_stack_tf_files(stack_dir: Path) -> list[Path]:
    """Return every .tf file in the stack directory."""
    return sorted(stack_dir.glob("*.tf"))


def collect_worker_stacks() -> list[Path]:
    """Return stacks that launch a model-worker runtime."""
    return [
        stack
        for stack in collect_stacks()
        if stack.parent.name not in CONTROL_PLANE_STACKS
    ]


# ── tests ──────────────────────────────────────────────────────────────


class TestWatchdogCoverage:
    """Every model-worker stack composes the gpu-cost-watchdog module."""

    def test_all_stacks_have_watchdog_module(self):
        """Every main.tf has ``module "gpu_cost_watchdog"`` with the correct source."""
        missing: list[str] = []
        for stack_main in collect_worker_stacks():
            content = _read_file(stack_main)
            if MODULE_REF not in content:
                missing.append(stack_main.parent.name)
            elif WATCHDOG_SOURCE not in content:
                missing.append(f"{stack_main.parent.name} (wrong source)")

        assert not missing, (
            f"Stacks missing gpu_cost_watchdog module: {missing}"
        )

    def test_all_stacks_emit_watchdog_output(self):
        """Every stack includes an output referencing module.gpu_cost_watchdog.user_data."""
        missing: list[str] = []
        for stack_main in collect_worker_stacks():
            stack_dir = stack_main.parent
            stack_name = stack_dir.name
            found = False
            for tf_file in all_stack_tf_files(stack_dir):
                content = _read_file(tf_file)
                if "module.gpu_cost_watchdog.user_data" in content and \
                   "output " in content:
                    found = True
                    break
            if not found:
                missing.append(stack_name)

        assert not missing, (
            f"Stacks missing watchdog_user_data output: {missing}"
        )

    def test_exact_stack_count(self):
        """Pin the expected number of stacks — prevents silent regression."""
        stacks = collect_stacks()
        assert len(stacks) == 19, (
            f"Expected 19 stacks, found {len(stacks)}: "
            + ", ".join(s.parent.name for s in stacks)
        )
        assert len(collect_worker_stacks()) == 18

    def test_control_plane_stacks_are_explicitly_non_worker(self):
        """Control-plane exceptions are declared and cannot hide GPU workers."""
        for stack_name in CONTROL_PLANE_STACKS:
            stack_dir = STACKS_DIR / stack_name
            main = _read_file(stack_dir / "main.tf")
            outputs = _read_file(stack_dir / "outputs.tf")

            assert 'output "runtime_class"' in outputs
            assert 'value       = "control-plane"' in outputs
            assert MODULE_REF not in main
            assert "azure-container-app-environment" in main
            assert "azure-container-app-vllm" not in main
            assert "azure-container-app-llamacpp" not in main
            for lifecycle_field in (
                "owner_digest",
                "plan_digest",
                "expires_at_utc",
            ):
                assert lifecycle_field in main

    def test_watchdog_cloud_validation_includes_kubernetes(self):
        """The module must accept cloud=kubernetes for K8s stacks."""
        vars_file = Path(
            "infra/terraform/modules/gpu-cost-watchdog/variables.tf"
        )
        content = _read_file(vars_file)
        assert '"kubernetes"' in content, (
            "gpu-cost-watchdog variable validation must include kubernetes"
        )
