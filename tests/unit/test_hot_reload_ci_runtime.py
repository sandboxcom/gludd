"""Regression coverage for the hosted hot-reload build runtime."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SETUP_NODE = "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
ESBUILD_INSTALL = "npm install --no-save --no-package-lock esbuild@0.28.1"
OPENCODE_INSTALL = "npm install --global opencode-ai@1.18.9"
OPA_DOWNLOAD = (
    "https://openpolicyagent.org/downloads/"
    "v${OPA_VERSION}/opa_linux_amd64_static"
)


def _job_steps(job: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"][job]["steps"]
    assert isinstance(steps, list)
    return steps


def _gate_steps() -> list[dict[str, Any]]:
    return _job_steps("gate")


def _hot_build_index(steps: list[dict[str, Any]]) -> int:
    return next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Build hot-reload modules"
    )


def test_gate_pins_supported_node_before_hot_reload_build() -> None:
    """Both Python matrix legs must use the supported Node runtime."""
    steps = _gate_steps()
    build_index = _hot_build_index(steps)
    setup = [
        (index, step)
        for index, step in enumerate(steps)
        if step.get("uses") == SETUP_NODE
    ]

    assert setup, "gate job relies on the hosted runner's ambient Node version"
    setup_index, setup_step = setup[0]
    assert setup_index < build_index
    assert setup_step.get("with", {}).get("node-version") == "26"


def test_gate_installs_pinned_esbuild_before_hot_reload_build() -> None:
    """The builder must use esbuild instead of its emergency regex fallback."""
    steps = _gate_steps()
    build_index = _hot_build_index(steps)
    installs = [
        index
        for index, step in enumerate(steps)
        if ESBUILD_INSTALL in str(step.get("run", ""))
    ]

    assert installs, "gate job does not provision the mature TypeScript transpiler"
    assert installs[0] < build_index


def test_all_test_shards_pin_node_26() -> None:
    steps = _job_steps("test-shard")
    setup = next(step for step in steps if step.get("uses") == SETUP_NODE)

    assert setup.get("with", {}).get("node-version") == "26"


def test_test_shards_only_inject_generated_version_for_release_tags() -> None:
    """Branch CI must test the committed package version, not a synthetic alpha."""
    for job in ("gate", "test-shard"):
        steps = _job_steps(job)
        inject = next(step for step in steps if step.get("name") == "Inject version")
        assert inject.get("if") == "startsWith(github.ref, 'refs/tags/v')", job


def test_other_shard_installs_pinned_opencode_before_pytest() -> None:
    steps = _job_steps("test-shard")
    test_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("name", "")).startswith("Test (shard ")
    )
    installs = [
        (index, step)
        for index, step in enumerate(steps)
        if OPENCODE_INSTALL in str(step.get("run", ""))
    ]

    assert installs, "other-shard OpenCode e2e tests require the upstream CLI"
    install_index, install_step = installs[0]
    assert install_index < test_index
    assert "matrix.shard == 'other'" in str(install_step.get("if", ""))


def test_other_shard_installs_pinned_opa_before_pytest() -> None:
    steps = _job_steps("test-shard")
    test_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("name", "")).startswith("Test (shard ")
    )
    installs = [
        (index, step)
        for index, step in enumerate(steps)
        if OPA_DOWNLOAD in str(step.get("run", ""))
    ]

    assert installs, "other-shard Rego validation requires the upstream OPA CLI"
    install_index, install_step = installs[0]
    assert install_index < test_index
    assert install_step.get("env", {}).get("OPA_VERSION") == "1.18.2"
    assert "matrix.shard == 'other'" in str(install_step.get("if", ""))


def test_all_test_shards_install_pinned_esbuild_before_pytest() -> None:
    """Matrix legs must not fall back to regex TypeScript transpilation."""
    steps = _job_steps("test-shard")
    test_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("name", "")).startswith("Test (shard ")
    )
    installs = [
        index
        for index, step in enumerate(steps)
        if ESBUILD_INSTALL in str(step.get("run", ""))
    ]

    assert installs, (
        "test-shard matrix does not provision pinned esbuild, so unit-2 reaches "
        "the emergency regex transpiler and hot-reload proxy tests fail"
    )
    assert installs[0] < test_index
