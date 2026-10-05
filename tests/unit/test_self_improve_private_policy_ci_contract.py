"""Release-lane contract for the project-private self-improvement E2E suite."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
E2E_TEST = "tests/e2e/test_self_improve_private_policy_e2e.py"


def _load_shard_registry() -> ModuleType:
    script = ROOT / "scripts" / "ci_named_shard_files.py"
    spec = importlib.util.spec_from_file_location("private_policy_ci_shards", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_private_policy_e2e_has_one_local_and_hosted_release_contract() -> None:
    """The same warnings-as-errors suite must run directly and in GHA."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    shard_registry = _load_shard_registry()

    assert (ROOT / E2E_TEST).is_file()
    assert "test-self-improve-private-policy" in makefile.split(".PHONY:", 1)[1]
    assert "Run hermetic fake-local/fake-Azure project privacy E2E" in makefile
    recipe = makefile.split("test-self-improve-private-policy:", 1)[1].split(
        "\n\n", 1
    )[0]
    assert E2E_TEST in recipe
    assert "-W error" in recipe

    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "test-self-improve-private-policy"
    )
    assert entry == {
        "name": "test-self-improve-private-policy",
        "make_variables": [],
        "behavior": "make test-self-improve-private-policy",
    }

    assert "tests/e2e/" in shard_registry.expand_shard("other")
    hosted_recipe = workflow.split(
        "- name: Test (shard ${{ matrix.shard }}", 1
    )[1].split("- name: Collect failure diagnostics", 1)[0]
    assert "other]" in workflow
    assert "scripts/run_ci_shards_serial.py" in hosted_recipe
    assert '--shards "${{ matrix.shard }}"' in hosted_recipe
    assert '--pytest-args="-W error"' in hosted_recipe
    for shard in shard_registry.SHARDS:
        if shard != "other":
            assert "tests/e2e/" not in shard_registry.expand_shard(shard)
