"""Confined native Make/Git proof for agent worktree creation outcomes."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from scripts.makefile_layout import compose_makefile

from tests.unit.test_single_native_commit_collection import _block

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("agent-worktree", "agent-worktree-base", "agent-worktree-dev")


def _creation_source(worktree_root: Path) -> str:
    """Retain native rules; confine only their fixed temporary root."""
    source = compose_makefile(ROOT / "Makefile")
    macro = re.search(r"^define _agent-worktree-create\n.*?^endef\n", source, re.MULTILINE | re.DOTALL)
    literal_guard = re.search(
        r"^# Reject evaluated creation data.*?^endif\nendif\n", source, re.MULTILINE | re.DOTALL,
    )
    goal_guard = re.search(
        r"^_MULTIWORD_VALUE_GOALS :=.*?^endif\nendif\n", source, re.MULTILINE | re.DOTALL,
    )
    blocks = [literal_guard.group() if literal_guard else "",
              goal_guard.group() if goal_guard else "", macro.group() if macro else ""]
    blocks.extend(_block(source, target) for target in (*TARGETS, "workstream-register"))
    return "\n".join(blocks).replace("/tmp/gludd-worktrees", str(worktree_root))


@dataclass
class WorktreeCreation:
    """Keep actual repository, tools and ownership state inside one fixture."""

    root: Path
    worktree_root: Path
    worktree: Path
    branch: str
    base: str
    head: str
    makefile: Path
    registry: Path
    calls: Path
    real_git: str
    real_make: str
    uv: Path
    environment: dict[str, str] = field(repr=False)

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.real_git, *arguments], cwd=self.root, env=self.environment,
            capture_output=True, text=True, check=True, timeout=10,
        )

    def rows(self) -> list[dict[str, object]]:
        if not self.calls.exists():
            return []
        return [
            cast(dict[str, object], json.loads(line))
            for line in self.calls.read_text(encoding="utf-8").splitlines()
        ]

    def invoke(
        self, target: str, *, fault: str = "", environment_override: bool = False,
        values: dict[str, str] | None = None, input_origin: str = "command-line",
        extra_goals: tuple[str, ...] = (), allow_development_bootstrap: bool = False,
        make_options: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        environment = self.environment.copy()
        environment["GLUDD_CREATION_FAULT"] = fault
        inputs = {"BRANCH": self.branch, "BASE": "selected-base",
                  "ACTIVE_WORKSTREAM_REGISTRY": str(self.registry)}
        inputs.update(values or {})
        if input_origin == "environment":
            environment.update(inputs)
        before_index = (self.root / ".git/index").read_bytes()
        before_note = (self.root / "note.txt").read_bytes()
        before_staged = (self.root / "staged.txt").read_bytes()
        before_untracked = (self.root / "untracked.txt").read_bytes()
        peer = self.root.parent / "frozen peer"
        peer_index = Path(self.git("-C", str(peer), "rev-parse", "--git-path", "index").stdout.strip())
        before_peer_index = peer_index.read_bytes()
        before_peer_note = (peer / "note.txt").read_bytes()
        before_checkpoint = (peer / "checkpoint.json").read_bytes()
        peer_prefix = f"worktree {peer}\0"
        before_peer_record = next(
            row for row in self.git("worktree", "list", "--porcelain", "-z").stdout.split("\0\0")
            if row.startswith(peer_prefix)
        )
        peer_refs = ["refs/heads/master", "refs/heads/selected-base", "refs/heads/frozen-peer"]
        if not allow_development_bootstrap:
            peer_refs.append("refs/heads/development")
        peers = self.git("for-each-ref", "--format=%(refname) %(objectname)", *peer_refs).stdout
        result = subprocess.run(
            [
                self.real_make, "--no-print-directory", *make_options,
                *(["-e"] if environment_override else []),
                "-f", str(self.makefile), target, *extra_goals,
                *([f"{name}={value}" for name, value in inputs.items()]
                  if input_origin == "command-line" else []),
                f"UV={shlex.quote(str(self.uv))}",
                f"PYTHON={sys.executable}",
            ],
            cwd=self.root, env=environment, capture_output=True, text=True,
            check=False, timeout=30,
        )
        assert (self.root / ".git/index").read_bytes() == before_index
        assert (self.root / "note.txt").read_bytes() == before_note
        assert (self.root / "staged.txt").read_bytes() == before_staged
        assert (self.root / "untracked.txt").read_bytes() == before_untracked
        assert peer_index.read_bytes() == before_peer_index
        assert (peer / "note.txt").read_bytes() == before_peer_note
        assert (peer / "checkpoint.json").read_bytes() == before_checkpoint
        assert next(
            row for row in self.git("worktree", "list", "--porcelain", "-z").stdout.split("\0\0")
            if row.startswith(peer_prefix)
        ) == before_peer_record
        assert self.git("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() == "master"
        assert self.git("rev-parse", "HEAD").stdout.strip() == self.head
        assert self.git(
            "for-each-ref", "--format=%(refname) %(objectname)", *peer_refs,
        ).stdout == peers
        if allow_development_bootstrap and result.returncode == 0:
            assert self.git("rev-parse", "refs/heads/development").stdout.strip() == self.head
        assert not (self.root / ".venv").exists()
        assert not (self.root / "forbidden-command").exists()
        return result


def _creation_fixture(
    tmp_path: Path, *, existing_branch: bool = False, older_branch: bool = False,
) -> WorktreeCreation:
    """Use the real registry with explicit owned inputs and native Git faults."""
    real_git, real_make = shutil.which("git"), shutil.which("make")
    assert real_git is not None and real_make is not None
    root = (tmp_path / "owned repository").resolve()
    root.mkdir()
    worktree_root = (tmp_path / "owned worktrees").resolve()
    tools = root / "tools"
    tools.mkdir()
    home = root / "private-home"
    home.mkdir()
    branch = "agent-create-owned"
    registry = root / "active-workstreams.json"
    calls = root / "calls.jsonl"
    uv = tools / "uv"
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("GIT_", "GH_", "GITHUB_"))
        and name not in {
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "GNUMAKEFLAGS", "PYTEST_ARGS",
            "BRANCH", "BASE", "WORKTREE", "ACTIVE_WORKSTREAM_REGISTRY",
            "GLUDD_ACTIVE_WORKSTREAM_REGISTRY",
        }
    }
    environment.update({
        "HOME": str(home), "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_ALLOW_PROTOCOL": "file",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root),
        "GLUDD_CREATION_CALLS": str(calls), "GLUDD_CREATION_FAULT": "",
        "GLUDD_CREATION_BRANCH": branch,
        "GLUDD_CREATION_WORKTREE": str(worktree_root / branch),
        "GLUDD_CREATION_CARRIER": "retained",
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
    })
    fixture = WorktreeCreation(
        root, worktree_root, worktree_root / branch, branch, "", "",
        root / "Makefile", registry, calls, real_git, real_make, uv, environment,
    )
    fixture.git("-c", "init.templateDir=", "init", "--quiet", "--initial-branch=master")
    fixture.git("config", "user.name", "Worktree creation fixture")
    fixture.git("config", "user.email", "creation@fixture.invalid")
    (root / "note.txt").write_text("first owned input\n", encoding="utf-8")
    fixture.git("add", "note.txt")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "first")
    first = fixture.git("rev-parse", "HEAD").stdout.strip()
    (root / "note.txt").write_text("second owned input\n", encoding="utf-8")
    fixture.git("add", "note.txt")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "second")
    fixture.head = fixture.git("rev-parse", "HEAD").stdout.strip()
    fixture.base = fixture.head
    fixture.git("branch", "development", fixture.head)
    fixture.git("branch", "selected-base", fixture.head)
    peer = root.parent / "frozen peer"
    fixture.git("worktree", "add", "--quiet", "-b", "frozen-peer", str(peer), fixture.head)
    fixture.git("worktree", "lock", "--reason", "owned frozen candidate", str(peer))
    (peer / "note.txt").write_bytes(b"frozen unrelated source\n")
    (peer / "checkpoint.json").write_bytes(b'{"candidate":"frozen","keep":true}\n')
    if existing_branch:
        fixture.git("branch", branch, first if older_branch else fixture.head)
    (root / ".git/info").mkdir(exist_ok=True)
    (root / ".git/info/exclude").write_text("*\n", encoding="utf-8")
    (root / "staged.txt").write_bytes(b"owned staged bytes\n")
    fixture.git("add", "-f", "staged.txt")
    (root / "untracked.txt").write_bytes(b"owned untracked bytes\n")
    (root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    adapter = tools / "git"
    adapter.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "fault = os.environ['GLUDD_CREATION_FAULT']\n"
        "path = pathlib.Path(os.environ['GLUDD_CREATION_CALLS'])\n"
        "with path.open('a') as output:\n"
        "    output.write(json.dumps({'kind':'git', 'args':args}) + '\\n')\n"
        "if fault == 'add-failure' and args[:2] == ['worktree', 'add']:\n"
        "    print('fixture native add failure', file=sys.stderr); raise SystemExit(53)\n"
        "if fault == 'development-bootstrap' and args[:2] == ['branch', 'development']:\n"
        "    print('fixture native development failure', file=sys.stderr); raise SystemExit(64)\n"
        "if fault == 'branch-query' and args[:3] == ['show-ref', '--verify', '--quiet']:\n"
        "    print('fixture native branch query failure', file=sys.stderr); raise SystemExit(61)\n"
        "if fault == 'wrong-path' and args[:2] == ['-C', os.environ['GLUDD_CREATION_WORKTREE']] "
        "and args[2:] == ['rev-parse', '--show-toplevel']:\n"
        "    print('/outside/owned-worktree'); raise SystemExit(0)\n"
        f"result = subprocess.run([{real_git!r}, *args], check=False)\n"
        "if result.returncode == 0 and args[:2] == ['worktree', 'add']:\n"
        "    worktree = os.environ['GLUDD_CREATION_WORKTREE']\n"
        "    branch = os.environ['GLUDD_CREATION_BRANCH']\n"
        "    if fault == 'wrong-branch':\n"
        f"        subprocess.run([{real_git!r}, '-C', worktree, 'checkout', '--quiet', '-b', "
        "'fixture-wrong-branch'], check=True)\n"
        "    if fault == 'wrong-head':\n"
        f"        subprocess.run([{real_git!r}, 'update-ref', 'refs/heads/' + branch, {first!r}], check=True)\n"
        "raise SystemExit(result.returncode)\n",
        encoding="utf-8",
    )
    adapter.chmod(0o755)
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with pathlib.Path(os.environ['GLUDD_CREATION_CALLS']).open('a') as output:\n"
        "    output.write(json.dumps({'kind':'registry', 'args':args, "
        "'carrier':os.environ.get('GLUDD_CREATION_CARRIER'), "
        "'makeflags':os.environ.get('MAKEFLAGS', '')}) + '\\n')\n"
        "if os.environ['GLUDD_CREATION_FAULT'] == 'registration-failure':\n"
        "    print('fixture native registration failure', file=sys.stderr); raise SystemExit(47)\n"
        "assert args[:5] == ['run', '--no-project', 'python', '-m', 'scripts.workstream_registry']\n"
        "from scripts import workstream_registry\n"
        "raise SystemExit(workstream_registry.main(args[5:]))\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    for command in ("gh", "ssh", "curl"):
        tool = tools / command
        tool.write_text(
            f"#!{sys.executable}\nimport pathlib\n"
            f"pathlib.Path({str(root / 'forbidden-command')!r}).write_text({command!r})\n"
            "raise SystemExit(91)\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
    fixture.makefile.write_text(
        "MAKE := " + shlex.quote(real_make) + " --no-print-directory -f "
        + shlex.quote(str(fixture.makefile)) + "\n"
        + "PYTHON := " + shlex.quote(sys.executable) + "\n"
        + ".PHONY: " + " ".join((*TARGETS, "workstream-register")) + "\n"
        + _creation_source(worktree_root),
        encoding="utf-8",
    )
    return fixture


def _assert_not_ready(result: subprocess.CompletedProcess[str]) -> None:
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "WORKTREE_PATH=" not in result.stdout
    assert "Worktree ready" not in output
    assert "outcome=passed" not in output


@pytest.mark.parametrize("creation_target", TARGETS)
@pytest.mark.parametrize("creation_environment_override", [False, True])
def test_native_creation_add_failure_never_registers_or_reports_ready(
    tmp_path: Path, creation_target: str, creation_environment_override: bool,
) -> None:
    fixture = _creation_fixture(tmp_path)
    result = fixture.invoke(
        creation_target, fault="add-failure",
        environment_override=creation_environment_override,
    )
    _assert_not_ready(result)
    rows = fixture.rows()
    assert not [row for row in rows if row["kind"] == "registry"]
    adds = [row for row in rows if row["kind"] == "git"
            and cast(list[str], row["args"])[:2] == ["worktree", "add"]]
    assert len(adds) == 1
    assert "fixture native add failure" in result.stderr and "Error 53" in result.stderr
    assert not fixture.registry.exists()
    assert not fixture.worktree.exists()


@pytest.mark.parametrize("creation_target", TARGETS)
@pytest.mark.parametrize("creation_environment_override", [False, True])
def test_native_creation_registration_failure_preserves_created_checkout_without_ready(
    tmp_path: Path, creation_target: str, creation_environment_override: bool,
) -> None:
    fixture = _creation_fixture(tmp_path)
    result = fixture.invoke(
        creation_target, fault="registration-failure",
        environment_override=creation_environment_override,
    )
    _assert_not_ready(result)
    assert fixture.worktree.is_dir()
    assert fixture.git("-C", str(fixture.worktree), "rev-parse", "HEAD").stdout.strip() == fixture.head
    assert not fixture.registry.exists()
    assert "fixture native registration failure" in result.stderr
    assert "Error 47" in result.stderr


@pytest.mark.parametrize("creation_target", TARGETS)
@pytest.mark.parametrize("creation_identity_fault", ["wrong-branch", "wrong-head", "wrong-path"])
def test_native_creation_rejects_wrong_native_checkout_identity_before_registration(
    tmp_path: Path, creation_target: str, creation_identity_fault: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    result = fixture.invoke(creation_target, fault=creation_identity_fault)
    _assert_not_ready(result)
    assert fixture.worktree.exists()
    assert not [row for row in fixture.rows() if row["kind"] == "registry"]
    assert not fixture.registry.exists()


@pytest.mark.parametrize("creation_target", TARGETS)
@pytest.mark.parametrize("creation_existing_branch", [False, True])
def test_native_creation_success_verifies_real_git_and_registered_owner(
    tmp_path: Path, creation_target: str, creation_existing_branch: bool,
) -> None:
    fixture = _creation_fixture(tmp_path, existing_branch=creation_existing_branch)
    result = fixture.invoke(creation_target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.worktree.is_dir()
    assert fixture.git("-C", str(fixture.worktree), "rev-parse", "HEAD").stdout.strip() == fixture.head
    assert fixture.git("-C", str(fixture.worktree), "symbolic-ref", "--short", "HEAD").stdout.strip() == fixture.branch
    actual = cast(dict[str, object], json.loads(fixture.registry.read_text(encoding="utf-8")))
    entry = cast(dict[str, object], cast(dict[str, object], actual["workstreams"])[fixture.branch])
    assert entry["status"] == "active" and entry["branch"] == fixture.branch
    assert entry["worktree"] == str(fixture.worktree)
    assert f"WORKTREE_PATH={fixture.worktree}" in result.stdout
    assert "outcome=passed" in result.stderr
    assert "gate_evidence=false" in result.stderr
    assert "release_ready=false" in result.stderr
    assert [row["kind"] for row in fixture.rows()].index("registry") > 0


@pytest.mark.parametrize("creation_target", ["agent-worktree-base", "agent-worktree-dev"])
def test_native_creation_existing_branch_must_descend_from_requested_base(
    tmp_path: Path, creation_target: str,
) -> None:
    fixture = _creation_fixture(tmp_path, existing_branch=True, older_branch=True)
    result = fixture.invoke(creation_target)
    _assert_not_ready(result)
    assert not fixture.worktree.exists() and not fixture.registry.exists()
    assert fixture.git("rev-parse", fixture.branch).stdout.strip() != fixture.head


def test_native_creation_plain_resume_preserves_existing_older_branch(tmp_path: Path) -> None:
    fixture = _creation_fixture(tmp_path, existing_branch=True, older_branch=True)
    expected = fixture.git("rev-parse", fixture.branch).stdout.strip()
    result = fixture.invoke("agent-worktree")
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.git("-C", str(fixture.worktree), "rev-parse", "HEAD").stdout.strip() == expected
    assert "WORKTREE_PATH=" in result.stdout


@pytest.mark.parametrize("creation_target", TARGETS)
def test_native_creation_branch_query_failure_is_not_branch_absence(
    tmp_path: Path, creation_target: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    result = fixture.invoke(creation_target, fault="branch-query")
    _assert_not_ready(result)
    assert "fixture native branch query failure" in result.stderr
    assert "Error 61" in result.stderr
    assert not fixture.worktree_root.exists()
    assert not [row for row in fixture.rows() if row["kind"] == "registry"]


@pytest.mark.parametrize("creation_path_kind", ["directory", "symlink", "parent-symlink"])
def test_native_creation_preserves_occupied_or_redirected_path(
    tmp_path: Path, creation_path_kind: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    outside = tmp_path / "outside sentinel"
    outside.mkdir()
    sentinel = outside / "retained.txt"
    sentinel.write_text("foreign bytes\n", encoding="utf-8")
    if creation_path_kind == "parent-symlink":
        fixture.worktree_root.symlink_to(outside, target_is_directory=True)
    else:
        fixture.worktree_root.mkdir()
        if creation_path_kind == "directory":
            fixture.worktree.mkdir()
            (fixture.worktree / "retained.txt").write_text("owned occupied bytes\n", encoding="utf-8")
        else:
            fixture.worktree.symlink_to(outside, target_is_directory=True)
    before = sentinel.read_bytes()
    result = fixture.invoke("agent-worktree")
    _assert_not_ready(result)
    assert sentinel.read_bytes() == before
    assert not fixture.registry.exists()
    assert not [row for row in fixture.rows() if row["kind"] == "registry"]
    assert not [row for row in fixture.rows()
                if cast(list[str], row["args"])[:2] == ["worktree", "add"]]


@pytest.mark.parametrize("creation_target,creation_input", [
    ("agent-worktree", "BRANCH"), ("agent-worktree-base", "BRANCH"),
    ("agent-worktree-dev", "BRANCH"), ("agent-worktree-base", "BASE"),
    ("agent-worktree", "ACTIVE_WORKSTREAM_REGISTRY"),
])
@pytest.mark.parametrize("creation_origin", ["command-line", "environment", "environment-override"])
def test_native_creation_literal_guard_has_same_origin_unguarded_witness(
    tmp_path: Path, creation_target: str, creation_input: str, creation_origin: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    marker = tmp_path / "owned evaluation witness"
    original = fixture.makefile.read_text(encoding="utf-8")
    guard = re.search(r"^# Reject evaluated creation data.*?^endif\nendif\n",
                      original, re.MULTILINE | re.DOTALL)
    assert guard is not None
    literal = {"BRANCH": fixture.branch, "BASE": "selected-base",
               "ACTIVE_WORKSTREAM_REGISTRY": str(fixture.registry)}[creation_input]
    values = {creation_input: literal + "$(shell touch " + shlex.quote(str(marker)) + ")"}
    origin = "command-line" if creation_origin == "command-line" else "environment"
    override = creation_origin == "environment-override"
    guarded = fixture.invoke(creation_target, values=values, input_origin=origin,
                             environment_override=override)
    _assert_not_ready(guarded)
    assert "inputs must be literal data" in guarded.stderr
    assert not marker.exists() and not fixture.rows()
    fixture.makefile.write_text(original[:guard.start()] + original[guard.end():], encoding="utf-8")
    fixture.invoke(creation_target, values=values, input_origin=origin, environment_override=override)
    assert marker.is_file(), "The identical unguarded Make value/origin must execute the payload"
    marker.unlink()
    fixture.makefile.write_text(original, encoding="utf-8")
    guarded_again = fixture.invoke(creation_target, values=values, input_origin=origin,
                                   environment_override=override)
    _assert_not_ready(guarded_again)
    assert not marker.exists()


@pytest.mark.parametrize("creation_origin", ["environment", "environment-override", "command-line"])
def test_native_creation_preserves_make_precedence_and_unrelated_carriers(
    tmp_path: Path, creation_origin: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    fixture.makefile.write_text(
        "BRANCH := file-selected-branch\n" + fixture.makefile.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fixture.environment["BRANCH"] = "ambient-branch"
    result = fixture.invoke(
        "agent-worktree", input_origin=("command-line" if creation_origin == "command-line"
                                        else "environment"),
        environment_override=creation_origin == "environment-override",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "file-selected-branch" if creation_origin == "environment" else fixture.branch
    payload = cast(dict[str, object], json.loads(fixture.registry.read_text(encoding="utf-8")))
    entries = cast(dict[str, object], payload["workstreams"])
    assert list(entries) == [expected]
    assert f"WORKTREE_PATH={fixture.worktree_root / expected}" in result.stdout
    registrations = [row for row in fixture.rows() if row["kind"] == "registry"]
    assert len(registrations) == 1 and registrations[0]["carrier"] == "retained"


@pytest.mark.parametrize("creation_target", TARGETS)
def test_native_creation_refuses_extra_goals_before_any_body(
    tmp_path: Path, creation_target: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    fixture.makefile.write_text(
        fixture.makefile.read_text(encoding="utf-8")
        + "\ncreation-decoy:\n\t@touch creation-decoy-ran\n", encoding="utf-8",
    )
    result = fixture.invoke(creation_target, extra_goals=("creation-decoy",))
    _assert_not_ready(result)
    assert "quote-values-or-split-goals" in result.stderr
    assert not (fixture.root / "creation-decoy-ran").exists()
    assert not fixture.rows() and not fixture.worktree.exists()


@pytest.mark.parametrize("creation_bootstrap_fault", ["", "development-bootstrap"])
def test_native_development_bootstrap_preserves_failure_and_pinned_master(
    tmp_path: Path, creation_bootstrap_fault: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    fixture.git("branch", "-d", "development")
    result = fixture.invoke("agent-worktree-dev", fault=creation_bootstrap_fault,
                            allow_development_bootstrap=True)
    if creation_bootstrap_fault:
        _assert_not_ready(result)
        assert "fixture native development failure" in result.stderr
        assert "Error 64" in result.stderr
        assert not fixture.worktree.exists() and not fixture.registry.exists()
        assert not fixture.git("for-each-ref", "refs/heads/development").stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert fixture.git("rev-parse", "development").stdout.strip() == fixture.head
        assert fixture.registry.exists() and fixture.worktree.exists()


def test_native_creation_invalid_base_never_adds_or_registers(tmp_path: Path) -> None:
    fixture = _creation_fixture(tmp_path)
    result = fixture.invoke("agent-worktree-base", values={"BASE": "missing-base"})
    _assert_not_ready(result)
    assert "phase=base-ref" in result.stderr
    assert not fixture.worktree_root.exists() and not fixture.registry.exists()


@pytest.mark.parametrize("creation_target", TARGETS)
@pytest.mark.parametrize("creation_quoted_origin", [
    "environment", "environment-override", "command-line",
])
def test_native_creation_roundtrips_literal_quotes_through_registration(
    tmp_path: Path, creation_target: str, creation_quoted_origin: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    branch = "agent-'quoted-"
    registry = fixture.root / ("registry 'quoted' " + chr(96) + "literal" + chr(96) + ".json")
    result = fixture.invoke(
        creation_target, values={"BRANCH": branch, "ACTIVE_WORKSTREAM_REGISTRY": str(registry)},
        input_origin=("command-line" if creation_quoted_origin == "command-line"
                      else "environment"),
        environment_override=creation_quoted_origin == "environment-override",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = cast(dict[str, object], json.loads(registry.read_text(encoding="utf-8")))
    entry = cast(dict[str, object], cast(dict[str, object], payload["workstreams"])[branch])
    assert entry["branch"] == branch
    assert entry["worktree"] == str(fixture.worktree_root / branch)
    assert f"WORKTREE_PATH={fixture.worktree_root / branch}" in result.stdout


@pytest.mark.parametrize("creation_target", TARGETS)
def test_native_creation_never_reassigns_another_active_registered_path(
    tmp_path: Path, creation_target: str,
) -> None:
    fixture = _creation_fixture(tmp_path)
    retained_path = tmp_path / "retained logical owner"
    retained_path.mkdir()
    sentinel = retained_path / "checkpoint.json"
    sentinel.write_bytes(b'{"owner":"retained","checkpoint":true}\n')
    entry = {"branch": fixture.branch, "status": "active", "updated_epoch": 19,
             "worktree": str(retained_path)}
    payload = {"version": 1, "workstreams": {fixture.branch: entry}}
    fixture.registry.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    before = fixture.registry.read_bytes()
    result = fixture.invoke(creation_target)
    _assert_not_ready(result)
    assert "active workstream path mismatch" in result.stderr
    assert "reconcile" in result.stderr
    assert fixture.registry.read_bytes() == before
    assert sentinel.read_bytes() == b'{"owner":"retained","checkpoint":true}\n'
    assert fixture.worktree.is_dir()
    assert fixture.git("rev-parse", fixture.branch).stdout.strip() == fixture.head
    assert fixture.git("-C", str(fixture.worktree), "rev-parse", "HEAD").stdout.strip() == fixture.head
