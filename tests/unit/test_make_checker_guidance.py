"""Confined native output/status controls for the existing two result owners."""
from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

import pytest

from tests.unit import test_gate_failure_promotion as promotion
from tests.unit import test_git_merge_nc_safety as merge_safety


class _ContractEntry(TypedDict):
    name: str
    make_variables: list[str]
    environment_variables: list[str]
    behavior: str


class _Contract(TypedDict):
    targets: list[_ContractEntry]


ROOT = Path(__file__).resolve().parents[2]
NATIVE_SOURCES = (
    "__init__.py", "check_make_target_contract.py", "check_gate_failure_promotions.py",
    "check_duplicate_targets.py", "check_make_help.py", "makefile_layout.py",
)


def test_guidance_fixture_keeps_external_config_boundary(tmp_path: Path) -> None:
    """Assert project config and equal physical cutoff in the actual child."""
    merge_safety.test_external_fixture_configuration_is_confined(tmp_path)


@dataclass(frozen=True)
class NativeRepositoryFixture:
    root: Path
    environment: dict[str, str] = field(repr=False)


def _native_repository_state(root: Path, environment: dict[str, str]) -> NativeRepositoryFixture:
    return NativeRepositoryFixture(root, environment)


@pytest.fixture
def native_repository(tmp_path: Path) -> NativeRepositoryFixture:
    root = tmp_path / "native-repository"
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "config").mkdir()
    private_home = tmp_path / "private-home"
    private_home.mkdir()
    for name in NATIVE_SOURCES:
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("PYTHON") and key not in {
            "HOME", "XDG_CONFIG_HOME", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES",
            "MAKELEVEL", "MAKEFILES", "GNUMAKEFLAGS",
        }
    }
    environment.update(
        HOME=str(private_home), XDG_CONFIG_HOME=str(private_home / "xdg"),
        PYTHONPATH=str(root), PYTHONSAFEPATH="1", PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return _native_repository_state(root, environment)


def _native_result(
    root: Path, environment: dict[str, str], owner: str, *arguments: str,
) -> subprocess.CompletedProcess[str]:
    watched_inputs = [
        root / "Makefile", root / "config" / "make_target_contract.json",
        root / "config" / "gate_failure_promotions.json",
    ]
    before = {path: path.read_bytes() for path in watched_inputs if path.is_file()}
    result = subprocess.run(
        [sys.executable, "-W", "error", "-m", "scripts." + owner, *arguments],
        cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=15,
    )
    assert {path: path.read_bytes() for path in before} == before
    assert not (root / "TRIPWIRE").exists()
    assert not (root / ".gate-logs").exists()
    module = importlib.import_module("scripts." + owner)
    handler = cast(Callable[[list[str]], int], module.main)
    standard_output, standard_error = io.StringIO(), io.StringIO()
    with (
        pytest.MonkeyPatch.context() as scoped,
        redirect_stdout(standard_output),
        redirect_stderr(standard_error),
    ):
        scoped.setattr(module, "__file__", str(root / "scripts" / (owner + ".py")))
        scoped.setattr(sys, "argv", [str(root / "scripts" / (owner + ".py")), *arguments])
        try:
            canonical_status = handler(list(arguments))
        except SystemExit as exc:
            assert isinstance(exc.code, int)
            canonical_status = exc.code
    assert canonical_status == result.returncode
    assert standard_output.getvalue() == result.stdout
    assert standard_error.getvalue() == result.stderr
    assert {path: path.read_bytes() for path in before} == before
    print("GUIDANCE_NATIVE_AND_CANONICAL", owner, result.returncode)
    return result


def _contract_fixture(root: Path) -> tuple[Path, _Contract]:
    (root / "Makefile").write_text(
        'help:\n\t@echo "  fixture-owner SAFE=1"\n\n'
        'fixture-owner:\n\t@printf "%s" "$(SAFE)" > TRIPWIRE\n',
        encoding="utf-8",
    )
    payload: _Contract = {
        "targets": [{
            "name": "fixture-owner", "make_variables": ["SAFE"],
            "environment_variables": [],
            "behavior": "make fixture-owner SAFE=1",
        }],
    }
    return root / "config" / "make_target_contract.json", payload


def _guidance_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.startswith("AGENT_NEXT ")]
    assert len(lines) == 1, output
    assert len(lines[0]) <= 360
    return lines[0]


@pytest.mark.parametrize("expected_refusal", [False, True])
def test_contract_native_pass_guidance_preserves_summary_and_limits(
    native_repository: NativeRepositoryFixture, expected_refusal: bool,
) -> None:
    root, environment = native_repository.root, native_repository.environment
    path, payload = _contract_fixture(root)
    if expected_refusal:
        payload["targets"][0]["behavior"] += " # expected refusal: Make2"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = _native_result(root, environment, "check_make_target_contract", str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines()[0] == "make-target-contract: PASS (1 targets)"
    guidance = _guidance_line(result.stdout)
    assert "make-target-contract PASS" in guidance
    assert "documented behavioral example" in guidance
    assert "every variable explicitly" in guidance
    assert "expected refusal" in guidance
    assert "not behavioral, gate or CI proof" in guidance


@pytest.mark.parametrize("broken, reason", [
    ("target", "fixture-owner: target is missing from Makefile"),
    ("help", "fixture-owner: target is missing from make help"),
    ("variable", "fixture-owner: Makefile does not reference MISSING"),
    ("example-variable", "fixture-owner: behavior does not demonstrate SAFE"),
    ("example-target", "fixture-owner: behavior must start with 'make fixture-owner'"),
])
def test_contract_native_fail_guidance_preserves_every_native_reason(
    native_repository: NativeRepositoryFixture, broken: str, reason: str,
) -> None:
    root, environment = native_repository.root, native_repository.environment
    path, payload = _contract_fixture(root)
    entry = payload["targets"][0]
    if broken == "target":
        (root / "Makefile").write_text("other:\n\t@true\n", encoding="utf-8")
    elif broken == "help":
        (root / "Makefile").write_text('fixture-owner:\n\t@echo "$(SAFE)"\n', encoding="utf-8")
    elif broken == "variable":
        entry["make_variables"] = ["MISSING"]
        entry["behavior"] = "make fixture-owner MISSING=1"
    elif broken == "example-variable":
        entry["behavior"] = "make fixture-owner"
    else:
        entry["behavior"] = "make wrong-owner SAFE=1"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = _native_result(root, environment, "check_make_target_contract", str(path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("make-target-contract: FAIL\n")
    assert reason in result.stdout
    guidance = _guidance_line(result.stdout)
    assert "make-target-contract FAIL" in guidance
    assert "listed target/help/variable/example inputs" in guidance
    assert "rerun make check-make-target-contract with the same inputs" in guidance
    assert "do not retry an unchanged broad gate" in guidance
    assert result.stdout.index(reason) < result.stdout.index("AGENT_NEXT ")


@pytest.mark.parametrize("broken, reason", [
    ("missing", "No such file"),
    ("json", "Expecting"),
    ("utf8", "utf-8"),
    ("shape", "contract must contain a targets list"),
    ("makefile-missing", "missing or unreadable Makefile source"),
    ("include", "include must be one explicit make/*.mk path"),
], ids=("missing", "json", "utf8", "shape", "makefile-missing", "include"))
def test_contract_native_input_error_guidance_retains_exit_one(
    native_repository: NativeRepositoryFixture, broken: str, reason: str,
) -> None:
    root, environment = native_repository.root, native_repository.environment
    path, payload = _contract_fixture(root)
    if broken == "json":
        path.write_text("{", encoding="utf-8")
    elif broken == "utf8":
        path.write_bytes(b"\xff")
    elif broken == "shape":
        path.write_text("{}", encoding="utf-8")
    elif broken != "missing":
        path.write_text(json.dumps(payload), encoding="utf-8")
    if broken == "makefile-missing":
        (root / "Makefile").unlink()
    elif broken == "include":
        (root / "Makefile").write_text("include $(UNOWNED)\n", encoding="utf-8")
    result = _native_result(root, environment, "check_make_target_contract", str(path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert reason in result.stdout + result.stderr
    assert result.stdout.startswith("make-target-contract: ERROR: ")
    guidance = _guidance_line(result.stdout)
    assert "make-target-contract ERROR" in guidance
    assert "reported contract/Makefile input or parse error" in guidance
    assert "rerun make check-make-target-contract with the same inputs" in guidance
    assert "do not retry an unchanged broad gate" in guidance
    assert "make-target-contract: PASS" not in result.stdout


def _guidance_promotion_fixture(root: Path) -> tuple[Path, dict[str, object]]:
    """Reuse the canonical fixture schema and all of its original budgets."""
    return promotion._synthetic_repository(root)


@pytest.mark.parametrize("manifest_spelling", ["relative", "absolute"])
def test_promotion_native_pass_guidance_waits_for_six_terminal_children(
    native_repository: NativeRepositoryFixture, manifest_spelling: str,
) -> None:
    root, environment = native_repository.root, native_repository.environment
    _root, payload = _guidance_promotion_fixture(root)
    path = root / "config" / "gate_failure_promotions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    manifest_arg = str(path) if manifest_spelling == "absolute" else "config/gate_failure_promotions.json"
    result = _native_result(root, environment, "check_gate_failure_promotions",
                            "--manifest", manifest_arg, "--repository-root", str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines()[0] == (
        "gate-failure-promotions: PASS families=2 admission=integration-admission "
        "runtime_phases=3 runtime_ceiling_seconds=780 full_gate=gate required=true"
    )
    guidance = _guidance_line(result.stdout)
    assert "gate-failure-promotions PASS STRUCTURAL ONLY" in guidance
    assert "all eight required child pins" in guidance
    assert "await terminal make check-gate-failure-promotions exit0" in guidance
    assert "before a broader cohort" in guidance
    assert "not full-owner, gate or CI proof" in guidance
    assert not (root / "scripts" / "run_gate.sh").exists()


@pytest.mark.parametrize("broken, reasons", [
    ("schema", ("schema_version",)),
    ("node", ("pytest node does not exist",)),
    ("runtime", ("runtime phase is not wired with its exact budget",)),
    ("multiple", ("duplicate family id", "owner target is not defined",
                  "owner target is not wired into integration-admission")),
])
def test_promotion_native_fail_guidance_retains_all_actual_reasons(
    native_repository: NativeRepositoryFixture, broken: str, reasons: tuple[str, ...],
) -> None:
    root, environment = native_repository.root, native_repository.environment
    _root, payload = _guidance_promotion_fixture(root)
    families = cast(list[dict[str, object]], payload["families"])
    runtime = cast(dict[str, object], payload["runtime_contract"])
    phases = cast(list[dict[str, object]], runtime["phases"])
    if broken == "schema":
        payload = {}
    elif broken == "node":
        families[1]["node"] = "tests/test_promoted.py::test_missing"
    elif broken == "runtime":
        phases[0]["max_seconds"] = 91
    else:
        families[1]["id"] = "dead-code-baseline-drift"
        families[1]["owner_target"] = "_missing-owner"
    path = root / "config" / "gate_failure_promotions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = _native_result(root, environment, "check_gate_failure_promotions",
                            "--manifest", str(path), "--repository-root", str(root))
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""
    for reason in reasons:
        assert reason in result.stderr
        assert result.stderr.index(reason) < result.stderr.index("AGENT_NEXT ")
    guidance = _guidance_line(result.stderr)
    assert "gate-failure-promotions FAIL" in guidance
    assert "reported manifest/schema/node/Make-wiring inputs" in guidance
    assert "rerun make check-gate-failure-promotions with the same manifest" in guidance
    assert "keep all eight required child pins and bounds" in guidance
    assert "do not retry an unchanged broader cohort" in guidance
    assert "gate-failure-promotions: PASS" not in result.stderr


@pytest.mark.parametrize("broken, reason", [
    ("missing", "No such file"),
    ("json", "Expecting"),
    ("utf8", "utf-8"),
    ("oversized", "exceeds the 256000-byte limit"),
])
def test_promotion_native_input_error_guidance_preserves_status_and_bound(
    native_repository: NativeRepositoryFixture, broken: str, reason: str,
) -> None:
    root, environment = native_repository.root, native_repository.environment
    path = root / "config" / "gate_failure_promotions.json"
    if broken == "json":
        path.write_text("{", encoding="utf-8")
    elif broken == "utf8":
        path.write_bytes(b"\xff")
    elif broken == "oversized":
        path.write_text(" " * 256_001, encoding="utf-8")
    result = _native_result(root, environment, "check_gate_failure_promotions",
                            "--manifest", str(path), "--repository-root", str(root))
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith("gate-failure-promotions: ERROR cannot load manifest: ")
    assert reason in result.stderr
    guidance = _guidance_line(result.stderr)
    assert "gate-failure-promotions ERROR" in guidance
    assert "reported manifest/path/parse input" in guidance
    assert "rerun make check-gate-failure-promotions with the same manifest" in guidance
    assert "do not retry an unchanged broader cohort" in guidance


@pytest.mark.parametrize("arguments, reason", [
    (["--unknown-guidance-option"], "unrecognized arguments"),
    (["--manifest"], "expected one argument"),
])
def test_promotion_native_argument_error_retains_usage_and_exit_two(
    native_repository: NativeRepositoryFixture, arguments: list[str], reason: str,
) -> None:
    root, environment = native_repository.root, native_repository.environment
    result = _native_result(root, environment, "check_gate_failure_promotions", *arguments)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "usage:" in result.stderr
    assert reason in result.stderr
    guidance = _guidance_line(result.stderr)
    assert "gate-failure-promotions ERROR" in guidance
    assert "reported CLI arguments" in guidance
    assert "rerun make check-gate-failure-promotions with the same intended inputs" in guidance
    assert result.stderr.index(reason) < result.stderr.index("AGENT_NEXT ")


def test_promotion_native_help_stays_zero_without_claiming_a_result(
    native_repository: NativeRepositoryFixture,
) -> None:
    root, environment = native_repository.root, native_repository.environment
    result = _native_result(root, environment, "check_gate_failure_promotions", "--help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout
    assert result.stderr == ""
    assert "AGENT_NEXT " not in result.stdout
    assert "gate-failure-promotions: PASS" not in result.stdout


def test_guidance_fixture_repr_keeps_owned_environment_private(tmp_path: Path) -> None:
    environment = {
        "GLUDD_FAKE_PRIVATE_CARRIER": "owned-private-repr-marker",
        "GLUDD_FAKE_RETAINED_CARRIER": "owned-retained-repr-marker",
    }
    before = dict(environment)
    fixture = _native_repository_state(tmp_path, environment)

    rendered = repr(fixture)

    assert all(name not in rendered and value not in rendered for name, value in environment.items())
    assert str(tmp_path) in rendered
    assert environment == before
