"""Native Make grammar controls for the existing variable-consistency guard."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit import test_gate_failure_promotion as promotion
from tests.unit import test_makefile_targets_deep as targets


@pytest.mark.parametrize(("genuine", "recipe", "leading_space", "refuses"), (
    (0, 21, False, False),
    (20, 0, False, False),
    (20, 21, False, False),
    (21, 0, False, True),
    (21, 21, False, True),
    (21, 0, True, True),
), ids=("recipe-only", "at-limit", "at-limit-with-recipe", "over-limit", "over-with-recipe", "spaced-top"))
def test_variable_guard_distinguishes_native_recipe_context_without_raising_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, genuine: int, recipe: int,
    leading_space: bool, refuses: bool,
) -> None:
    """Real assignments still consume the unchanged20 allowance; recipe code does not."""
    prefix = " " if leading_space else ""
    assignments = "".join(f"{prefix}FIXTURE_{index} = {index}\n" for index in range(genuine))
    slash = chr(92)
    python_lines = "".join(f"\tvalue_{index} = {index}; {slash}\n" for index in range(recipe))
    source = (
        assignments + ".PHONY: fixture\nfixture:\n"
        + f"\t@{shlex.quote(sys.executable)} -c 'print(end=\"\"); {slash}\n"
        + python_lines + "\tprint(\"fixture-native-pass\")'\n"
    )
    makefile = tmp_path / "native-context.mk"
    makefile.write_text(source, encoding="utf-8")
    private_home = tmp_path / "private-home"
    private_home.mkdir()
    environment = {
        name: value for name, value in os.environ.items()
        if name not in {"HOME", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MAKEFILES", "GNUMAKEFLAGS"}
    }
    environment["HOME"] = str(private_home)
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(makefile), "fixture"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "fixture-native-pass\n"
    assert result.stderr == ""
    assert makefile.read_text(encoding="utf-8") == source
    monkeypatch.setattr(targets, "_read_makefile", lambda: source)
    guard = targets.TestVariableConsistency()
    if refuses:
        with pytest.raises(AssertionError, match="bare '=' assignments found"):
            guard.test_no_bare_equals()
    else:
        guard.test_no_bare_equals()


@pytest.mark.parametrize(("family_id", "node"), (
    ("make-guide-required-owner-command",
     "tests/unit/test_make_target_contract.py::test_key_make_targets_recommends_guarded_release_owners"),
    ("make-variable-recipe-context",
     "tests/unit/test_makefile_targets_deep.py::TestVariableConsistency::test_no_bare_equals"),
))
def test_fast_admission_executes_native_make_consumers_before_broad_replay(
    tmp_path: Path, family_id: str, node: str,
) -> None:
    """The actual cheap child must include both previously late failures."""
    promotion.test_promotion_checker_executes_both_structural_pins_only_after_valid_manifest(tmp_path, "")
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [row["phase"] for row in rows] == ["validator", "tests", "after"]
    assert rows[1]["files"].count(node) == 1
    assert len(rows[1]["files"]) == 8
    manifest = json.loads((promotion.ROOT / "config/gate_failure_promotions.json").read_text())
    family = next((item for item in manifest["families"] if item["id"] == family_id), None)
    assert family == {
        "id": family_id, "owner_target": "check-gate-failure-promotions",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    }
