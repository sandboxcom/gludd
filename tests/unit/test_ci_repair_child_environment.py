"""Repair evidence owns its child environment without inheriting owner authority."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import ci_failure_ledger as owner

ROOT = Path(__file__).resolve().parents[2]
FAILED_SHA = "a" * 40
REPAIR_SHA = "b" * 40
RECURSION_KEYS = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS")
OWNER_KEYS = (
    "CI_FAILURE_LEDGER", "CI_FAILURE_FAMILIES", "CI_FAILURE_VALIDATE_ONLY",
    "CI_REPAIR_ALL_OPEN", "CI_REPAIR_SHA", "CI_REPAIR_EVIDENCE_TARGET", "CI_REPAIR_EVIDENCE_VARS",
)
RUNTIME_KEYS = (
    "PATH", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_NO_SYNC", "UV_PYTHON_DOWNLOADS",
    "NPROC", "GLUDD_XDIST_WORKERS", "PYTEST_ADDOPTS", "PYTHONWARNINGS",
)


def _seed(path: Path) -> dict[str, str]:
    ledger = owner.new_ledger()
    owner.observe_payload(ledger, {
        "databaseId": 101, "headSha": FAILED_SHA, "headBranch": "development",
        "status": "completed", "conclusion": "failure", "workflowName": "Build and Release",
        "url": "https://github.invalid/actions/runs/101",
        "jobs": [
            {"name": name, "status": "completed", "conclusion": "failure", "steps": []}
            for name in ("selected", "sibling")
        ],
    }, observed_at="first")
    owner.write_ledger(path, ledger)
    return {str(family["job"]): str(key) for key, family in ledger["families"].items()}


def _repair(path: Path, family_id: str) -> list[str]:
    return [
        "repair", "--ledger", str(path), "--family", family_id, "--sha", REPAIR_SHA,
        "--evidence-target", "evidence-probe", "--evidence-vars-from-env",
    ]


def _mapping(value: object) -> dict[str, str]:
    assert isinstance(value, dict), "the evidence child must receive an explicit environment copy"
    assert all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    return {str(key): str(item) for key, item in value.items()}


def _probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0,
) -> tuple[Path, dict[str, str], list[tuple[list[str], dict[str, object]]], list[str]]:
    path = tmp_path / "owner-ledger.json"
    families = _seed(path)
    calls: list[tuple[list[str], dict[str, object]]] = []
    checkouts: list[str] = []

    def evidence(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode, "", "")

    monkeypatch.setattr(owner, "ROOT", tmp_path)
    monkeypatch.setattr(owner, "_require_repair_checkout", checkouts.append)
    monkeypatch.setattr(owner, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(subprocess, "run", evidence)
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", "")
    return path, families, calls, checkouts


@pytest.mark.parametrize("name", (*RECURSION_KEYS, *OWNER_KEYS))
def test_repair_child_strips_each_ambient_owner_and_make_transport_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    path, families, calls, checkouts = _probe(tmp_path, monkeypatch)
    canonical_reference = "/Users/shawnwilson/gludd/.gludd/ci-failure-ledger.json"
    for key in (*RECURSION_KEYS, *OWNER_KEYS):
        monkeypatch.setenv(key, f"owner={canonical_reference}")
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", "TESTFILES=selected.py")
    monkeypatch.setenv("MAKEFLAGS", f" -- CI_FAILURE_LEDGER={canonical_reference} PYTEST_ARGS=-q\\ -W\\ error")
    before = dict(os.environ)

    assert owner.main(_repair(path, families["selected"])) == 0

    assert len(calls) == 1
    child = _mapping(calls[0][1].get("env"))
    assert name not in child
    assert canonical_reference not in "\n".join(child.values())
    assert dict(os.environ) == before
    assert checkouts == [REPAIR_SHA, REPAIR_SHA]


def test_repair_child_preserves_every_other_entry_and_exact_validated_evidence_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, families, calls, _checkouts = _probe(tmp_path, monkeypatch)
    evidence_vars = [
        "TESTFILES=tests/unit/a.py tests/unit/b.py",
        'PYTEST_ARGS=-q -n 0 -W error -k "repair or quoted"',
        f"CI_FAILURE_LEDGER={tmp_path / 'explicit-child-ledger.json'}",
    ]
    for key in RUNTIME_KEYS:
        monkeypatch.setenv(key, f"preserved-{key}")
    monkeypatch.setenv("CI_FAILURE_REPOSITORY", "sandboxcom/unchanged")
    monkeypatch.setenv("CI_REPAIR_UNRELATED", "keep this exact entry")
    monkeypatch.setenv("CI_FAILURE_LEDGER", str(path))
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", shlex.join(evidence_vars[1:]))
    before = dict(os.environ)
    command = _repair(path, families["selected"])
    command.extend(["--evidence-var", evidence_vars[0]])

    assert owner.main(command) == 0

    argv, options = calls[0]
    assert argv == ["make", "evidence-probe", *evidence_vars]
    assert options["cwd"] == tmp_path
    assert options["check"] is False
    child = _mapping(options.get("env"))
    assert child == {key: value for key, value in before.items() if key not in (*RECURSION_KEYS, *OWNER_KEYS)}
    assert dict(os.environ) == before
    repaired = owner.read_ledger(path)
    assert repaired["families"][families["selected"]]["repair"]["evidence"] == shlex.join(argv)
    assert repaired["families"][families["sibling"]]["status"] == "open"


@pytest.mark.parametrize("invalid", ["unknown", "resolved", "empty", "same-sha", "non-descendant"])
def test_environment_isolation_keeps_selection_admission_before_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    path, families, calls, _checkouts = _probe(tmp_path, monkeypatch)
    command = _repair(path, families["selected"])
    if invalid == "unknown":
        command[command.index("--family") + 1] = "f" * 64
    elif invalid == "resolved":
        ledger = owner.read_ledger(path)
        ledger["families"][families["selected"]]["status"] = "resolved"
        owner.write_ledger(path, ledger)
    elif invalid == "empty":
        index = command.index("--family")
        del command[index:index + 2]
    elif invalid == "same-sha":
        command[command.index("--sha") + 1] = FAILED_SHA
    else:
        monkeypatch.setattr(owner, "_git_is_ancestor", lambda _old, _new: False)
    before = path.read_bytes()

    assert owner.main(command) == 2
    assert calls == []
    assert path.read_bytes() == before


def _native_fixture(tmp_path: Path) -> None:
    """Bind the maintained Make recipe and ledger to an isolated test checkout."""
    source = ROOT / "scripts" / "ci_failure_ledger.py"
    (tmp_path / "owner.mk").write_text(
        f"include {ROOT / 'make' / '30-ci-and-release.mk'}\n", encoding="utf-8",
    )
    (tmp_path / "owner.py").write_text(
        "import importlib.util, json, os, sys\nfrom pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('repair_owner', {str(source)!r})\n"
        "assert spec is not None and spec.loader is not None\n"
        "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
        "module.ROOT = Path(__file__).resolve().parent\n"
        f"module._git_is_ancestor = lambda old, new: old == {FAILED_SHA!r} and new == {REPAIR_SHA!r}\n"
        "before = dict(os.environ)\nresult = module.main(sys.argv[2:])\n"
        "Path('owner-environment.json').write_text(json.dumps({'before': before, 'after': dict(os.environ)}))\n"
        "raise SystemExit(result)\n",
        encoding="utf-8",
    )
    (tmp_path / "probe.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "phase = sys.argv[1]\n"
        "if phase == 'checkout':\n"
        "    with Path('checkout-queries.txt').open('a') as stream:\n"
        "        stream.write('checkout\\n')\n"
        f"    print(json.dumps({{'path': {str(tmp_path)!r}, 'head': {REPAIR_SHA!r}, "
        "'is_clean': True, 'dirty_count': 0}))\n"
        "else:\n"
        "    Path(f'{phase}-environment.json').write_text(json.dumps(dict(os.environ)))\n"
        "    if phase == 'nested':\n"
        "        raise SystemExit(int(os.environ['GLUDD_REPAIR_CHILD_RC']))\n",
        encoding="utf-8",
    )
    probe = shlex.join([sys.executable, str(tmp_path / "probe.py")])
    (tmp_path / "Makefile").write_text(
        "export TESTFILES PYTEST_ARGS\n"
        f"worktree-state:\n\t@{probe} checkout\n"
        f"evidence-probe:\n\t@{probe} evidence\n\t@$(MAKE) --no-print-directory nested-probe\n"
        f"nested-probe:\n\t@{probe} nested\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("returncode", [0, 9])
@pytest.mark.parametrize("explicit_child_ledger", [False, True])
def test_native_make_repair_detaches_owner_inputs_but_preserves_explicit_child_policy(
    tmp_path: Path, returncode: int, explicit_child_ledger: bool,
) -> None:
    path = tmp_path / "owner-ledger.json"
    families = _seed(path)
    before = path.read_bytes()
    _native_fixture(tmp_path)
    child_ledger = tmp_path / "explicit-child-ledger.json"
    evidence_vars = [
        "TESTFILES=tests/unit/a.py tests/unit/b.py",
        'PYTEST_ARGS=-q -n 0 -W error -k "repair or quoted"',
    ]
    if explicit_child_ledger:
        evidence_vars.append(f"CI_FAILURE_LEDGER={child_ledger}")
    environment = {key: value for key, value in os.environ.items() if key not in (*RECURSION_KEYS, *OWNER_KEYS)}
    environment.update(
        GLUDD_REPAIR_CHILD_RC=str(returncode), PYTEST_ADDOPTS="-W error", PYTHONWARNINGS="error",
        CI_FAILURE_REPOSITORY="sandboxcom/preserved", CI_REPAIR_UNRELATED="not an owner input",
    )
    result = subprocess.run([
        "make", "--no-print-directory", "-f", "owner.mk", "ci-failure-repair",
        f"PYTHON={shlex.join([sys.executable, str(tmp_path / 'owner.py')])}",
        f"CI_FAILURE_LEDGER={path}", f"CI_FAILURE_FAMILIES={families['selected']}",
        "CI_REPAIR_ALL_OPEN=0", f"CI_REPAIR_SHA={REPAIR_SHA}", "CI_FAILURE_VALIDATE_ONLY=0",
        "CI_REPAIR_EVIDENCE_TARGET=evidence-probe", f"CI_REPAIR_EVIDENCE_VARS={shlex.join(evidence_vars)}",
        "TESTFILES=wrong-parent-selector.py", "PYTEST_ARGS=--wrong-parent-policy",
    ], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15, check=False)

    assert result.returncode == (0 if returncode == 0 else 2), result.stdout + result.stderr
    parent: object = json.loads((tmp_path / "owner-environment.json").read_text(encoding="utf-8"))
    assert isinstance(parent, dict)
    inherited = _mapping(parent.get("before"))
    assert inherited == _mapping(parent.get("after"))
    assert inherited["CI_FAILURE_LEDGER"] == str(path)
    assert "CI_FAILURE_LEDGER=" in inherited["MAKEFLAGS"]
    for phase in ("evidence", "nested"):
        child = _mapping(json.loads((tmp_path / f"{phase}-environment.json").read_text(encoding="utf-8")))
        expected_owner = {"CI_FAILURE_LEDGER": str(child_ledger)} if explicit_child_ledger else {}
        assert {key: child[key] for key in OWNER_KEYS if key in child} == expected_owner
        assert str(path) not in "\n".join(child.values())
        assert child["TESTFILES"] == evidence_vars[0].split("=", 1)[1]
        assert child["PYTEST_ARGS"] == evidence_vars[1].split("=", 1)[1]
        assert {key: child[key] for key in RUNTIME_KEYS if key in environment} == {
            key: environment[key] for key in RUNTIME_KEYS if key in environment
        }
        assert child["CI_FAILURE_REPOSITORY"] == "sandboxcom/preserved"
        assert child["CI_REPAIR_UNRELATED"] == "not an owner input"
    persisted = owner.read_ledger(path)
    if returncode:
        assert path.read_bytes() == before
        assert {family["status"] for family in persisted["families"].values()} == {"open"}
    else:
        assert persisted["families"][families["selected"]]["repair"]["sha"] == REPAIR_SHA
        assert persisted["families"][families["sibling"]]["status"] == "open"
    assert (tmp_path / "checkout-queries.txt").read_text(encoding="utf-8").splitlines() == (
        ["checkout"] if returncode else ["checkout", "checkout"]
    )
    assert not child_ledger.exists()
