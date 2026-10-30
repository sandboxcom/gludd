"""Confined native Git acceptance for the project no-commit merge owner."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.unit.test_runtime_admission_scheduling import _native_test_files

ROOT = Path(__file__).resolve().parents[2]
FRAGMENT = ROOT / "make" / "30-ci-and-release.mk"
REAL_GIT = shutil.which("git") or pytest.fail("Native Git is required for merge acceptance")


def run_git(
    root: Path, environment: dict[str, str], *arguments: str, check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [REAL_GIT, "--no-optional-locks", *arguments], cwd=root, env=environment,
        capture_output=True, check=check, timeout=15,
    )


def git_text(root: Path, environment: dict[str, str], *arguments: str) -> str:
    return run_git(root, environment, *arguments).stdout.decode().strip()


def git_path(root: Path, environment: dict[str, str], name: str) -> Path:
    value = Path(git_text(root, environment, "rev-parse", "--git-path", name))
    return value if value.is_absolute() else root / value


@dataclass(frozen=True)
class RepositoryFixture:
    root: Path
    environment: dict[str, str] = field(repr=False)


def _repository_state(root: Path, environment: dict[str, str]) -> RepositoryFixture:
    return RepositoryFixture(root, environment)


@pytest.fixture
def repository(tmp_path: Path) -> RepositoryFixture:
    root = tmp_path / "repository"
    root.mkdir()
    private_home = tmp_path / "home"
    private_home.mkdir()
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in {
            "HOME", "XDG_CONFIG_HOME", "BR", "GLUDD_GIT_MERGE_NC_BR",
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MAKEFILES", "GNUMAKEFLAGS",
        }
    }
    environment.update(
        HOME=str(private_home), XDG_CONFIG_HOME=str(private_home / "xdg"),
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1",
        GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="0",
        GIT_CEILING_DIRECTORIES=str(tmp_path), GIT_MERGE_AUTOEDIT="no",
    )
    run_git(root, environment, "init", "-b", "development")
    for name, value in {
        "user.name": "Merge NC confined fixture", "user.email": "merge-nc@example.invalid",
        "commit.gpgsign": "false", "core.hooksPath": str(hooks),
        "core.fileMode": "true", "core.autocrlf": "false", "merge.autoStash": "true",
    }.items():
        run_git(root, environment, "config", name, value)
    for name, value in {
        "keep.txt": "retained work\n", "conflict.txt": "base\n",
        "mode.sh": "#!/bin/sh\nexit 0\n", ".gitignore": "ignored-proof.txt\n",
    }.items():
        (root / name).write_text(value, encoding="utf-8")
    (root / "mode.sh").chmod(0o755)
    run_git(root, environment, "add", ".")
    run_git(root, environment, "commit", "-m", "fixture base")
    base = git_text(root, environment, "rev-parse", "HEAD")
    run_git(root, environment, "checkout", "-b", "feature/clean")
    (root / "feature.txt").write_text("feature payload\n", encoding="utf-8")
    run_git(root, environment, "add", "feature.txt")
    run_git(root, environment, "commit", "-m", "clean feature")
    run_git(root, environment, "checkout", "-b", "feature/conflict", base)
    (root / "conflict.txt").write_text("feature conflict\n", encoding="utf-8")
    run_git(root, environment, "commit", "-am", "conflicting feature")
    run_git(root, environment, "checkout", "-b", "feature/ignored", base)
    (root / "ignored-proof.txt").write_text("incoming tracked replacement\n", encoding="utf-8")
    run_git(root, environment, "add", "-f", "ignored-proof.txt")
    run_git(root, environment, "commit", "-m", "tracked ignored-path feature")
    run_git(root, environment, "checkout", "development")
    (root / "conflict.txt").write_text("development conflict\n", encoding="utf-8")
    run_git(root, environment, "commit", "-am", "development work")
    # One real owned checkpoint plus a separate retained ref exercise preservation.
    (root / "keep.txt").write_text("checkpoint payload\n", encoding="utf-8")
    run_git(root, environment, "stash", "push", "-m", "owned fixture checkpoint")
    run_git(root, environment, "update-ref", "refs/gludd-checkpoints/retained", base)
    return _repository_state(root, environment)


def snapshot(root: Path, environment: dict[str, str]) -> dict[str, object]:
    # No status refresh may modify the index while collecting the invariant.
    files: dict[str, tuple[bytes, int]] = {}
    for path in root.rglob("*"):
        if ".git" not in path.relative_to(root).parts and path.is_file():
            files[str(path.relative_to(root))] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    index = git_path(root, environment, "index")
    return {
        "head": git_text(root, environment, "rev-parse", "HEAD"),
        "refs": run_git(root, environment, "show-ref").stdout,
        "stashes": run_git(root, environment, "stash", "list", "--format=%H:%gs").stdout,
        "index": index.read_bytes(),
        "entries": run_git(root, environment, "ls-files", "--stage", "-z").stdout,
        "status": run_git(root, environment, "status", "--porcelain=v1", "--untracked-files=all").stdout,
        "files": files,
        "merge": {
            name: git_path(root, environment, name).read_bytes()
            for name in ("MERGE_HEAD", "MERGE_AUTOSTASH", "MERGE_MSG", "MERGE_MODE", "AUTO_MERGE")
            if git_path(root, environment, name).is_file()
        },
    }


def invoke(
    root: Path, environment: dict[str, str], tmp_path: Path, branch: str = "feature/clean",
    *, origin: str = "command", fault: str = "", full_make: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    bin_directory = tmp_path / "fixture-bin"
    bin_directory.mkdir(exist_ok=True)
    record = git_path(root, environment, "merge-nc-argv.jsonl")
    shim = bin_directory / "git"
    shim.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        f"native = {REAL_GIT!r}\n"
        "args = sys.argv[1:]\n"
        f"with Path({str(record)!r}).open('a') as stream: stream.write(json.dumps(args) + '\\n')\n"
        f"fault = {fault!r}\n"
        "if 'status' in args and fault == 'status17': raise SystemExit(17)\n"
        "if 'symbolic-ref' in args and fault == 'symbolic17': raise SystemExit(17)\n"
        "if 'config' in args and fault == 'config17': raise SystemExit(17)\n"
        "if 'merge-tree' in args:\n"
        "    if fault in {'tree17', 'tree129'}: raise SystemExit(17 if fault == 'tree17' else 129)\n"
        "    if fault == 'tree-malformed0': print('missing native NUL field'); raise SystemExit(0)\n"
        "    if fault == 'tree-malformed1': sys.stdout.write('not-a-tree\\x00'); raise SystemExit(1)\n"
        "if 'merge' in args and '--abort' not in args:\n"
        "    if fault == 'merge17':\n"
        "        print('fixture native merge refusal17', file=sys.stderr); raise SystemExit(17)\n"
        "    if fault == 'foreign17':\n"
        "        subprocess.run([native, '-c', 'merge.autoStash=false', 'merge', "
        "'--no-autostash', '--no-ff', '--no-commit', 'feature/conflict'], check=False)\n"
        "        raise SystemExit(17)\n"
        "if 'merge' in args and '--abort' in args and fault == 'abort23': raise SystemExit(23)\n"
        "os.execv(native, [native, *args])\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    native_environment = {**environment, "PATH": str(bin_directory) + os.pathsep + environment["PATH"]}
    makefile = root / "Makefile" if full_make else FRAGMENT
    command = ["make", "--no-print-directory", "-f", str(makefile)]
    if origin == "inherited":
        forwarding = tmp_path / "forward.mk"
        forwarding.write_text(
            "include " + str(makefile) + "\n"
            "forward: BR = " + branch + "\n"
            "forward: git-merge-nc\n", encoding="utf-8",
        )
        command = ["make", "--no-print-directory", "-f", str(forwarding), "forward"]
    elif origin == "environment-e":
        native_environment["BR"] = branch
        command.extend(["-e", "git-merge-nc"])
    else:
        command.extend(["git-merge-nc", "BR=" + branch])
    command.append("PYTHON=" + sys.executable)
    result = subprocess.run(
        command, cwd=root, env=native_environment, capture_output=True, text=True, check=False, timeout=15,
    )
    calls = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
    return result, calls


def merge_calls(calls: list[list[str]]) -> list[list[str]]:
    return [arguments for arguments in calls if "merge" in arguments]


def test_external_fixture_configuration_is_confined(tmp_path: Path) -> None:
    """Retain real child configuration while confining temporary collection."""
    physical = tmp_path.resolve()
    probe = tmp_path / "test_native_scope.py"
    probe.write_text(
        "from pathlib import Path\n"
        "def test_actual_configuration(pytestconfig):\n"
        "    physical = Path(__file__).parent.resolve()\n"
        "    assert pytestconfig.rootpath == physical\n"
        "    assert Path(pytestconfig.getoption('confcutdir')).resolve() == physical\n"
        f"    assert Path(pytestconfig.inipath) == Path({str(ROOT / 'pyproject.toml')!r})\n",
        encoding="utf-8",
    )
    flags = shlex.join([
        "-c", str(ROOT / "pyproject.toml"), "--rootdir", str(physical),
        "--confcutdir", str(physical), "-q", "-W", "error", "-n", "0",
    ])
    result = _native_test_files([probe], 1, extra_pytest_args=flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert not result.stderr, result.stderr


@pytest.mark.parametrize("dirty", ["staged-new", "unstaged", "untracked", "mode"])
@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
def test_dirty_refusal_preserves_exact_index_worktree_and_checkpoints(
    repository: RepositoryFixture, tmp_path: Path, dirty: str, origin: str,
) -> None:
    root, environment = repository.root, repository.environment
    if dirty == "staged-new":
        (root / "new-test.py").write_text("# source-identical fixture test\n", encoding="utf-8")
        run_git(root, environment, "add", "new-test.py")
    elif dirty == "unstaged":
        (root / "keep.txt").write_text("uncommitted retained bytes\n", encoding="utf-8")
    elif dirty == "untracked":
        (root / "untracked-proof").write_bytes(b"retained untracked proof")
    else:
        (root / "mode.sh").chmod(0o644)
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, origin=origin)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "dirty" in result.stdout.lower() + result.stderr.lower()
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


def test_existing_merge_is_never_aborted(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    native = run_git(root, environment, "-c", "merge.autoStash=false", "merge", "--no-autostash",
                     "--no-ff", "--no-commit", "feature/conflict", check=False)
    assert native.returncode == 1
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path)
    assert result.returncode != 0
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
@pytest.mark.parametrize("payload", [
    "$(shell touch TRIPWIRE)", chr(96) + "touch TRIPWIRE" + chr(96),
    '"; touch TRIPWIRE; #', "--not-a-merge-option",
])
def test_branch_data_is_literal_before_make_and_shell_expansion(
    repository: RepositoryFixture, tmp_path: Path, origin: str, payload: str,
) -> None:
    root, environment = repository.root, repository.environment
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, payload, origin=origin)
    assert not (root / "TRIPWIRE").exists(), result.stdout + result.stderr
    assert result.returncode != 0
    assert not any("--abort" in arguments for arguments in calls), calls
    assert snapshot(root, environment) == before


@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
def test_clean_no_commit_merge_pins_head_and_retains_owned_checkpoints(
    repository: RepositoryFixture, tmp_path: Path, origin: str,
) -> None:
    root, environment = repository.root, repository.environment
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, origin=origin)
    assert result.returncode == 0, result.stdout + result.stderr
    after = snapshot(root, environment)
    assert after["head"] == before["head"]
    assert after["refs"] == before["refs"]
    assert after["stashes"] == before["stashes"]
    assert (root / "feature.txt").read_bytes() == b"feature payload\n"
    assert git_path(root, environment, "MERGE_HEAD").is_file()
    assert not git_path(root, environment, "MERGE_AUTOSTASH").exists()
    actual = [arguments for arguments in merge_calls(calls) if "--abort" not in arguments]
    assert len(actual) == 1
    assert {"--no-commit", "--no-ff", "--no-autostash", "--no-overwrite-ignore"} <= set(actual[0])
    assert not any("--abort" in arguments for arguments in calls)


def test_valid_branch_containing_dollar_is_not_expanded(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    branch = "feature/literal-$x"
    target = git_text(root, environment, "rev-parse", "feature/clean")
    run_git(root, environment, "update-ref", "refs/heads/" + branch, target)
    before = snapshot(root, environment)
    result, _calls = invoke(root, environment, tmp_path, branch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git_text(root, environment, "rev-parse", "HEAD") == before["head"]
    assert git_text(root, environment, "rev-parse", "MERGE_HEAD") == target


def test_native_conflict_aborts_only_its_new_merge_and_retains_native_status(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "feature/conflict")
    assert result.returncode != 0
    assert "Error 1" in result.stderr
    assert sum("--abort" in arguments for arguments in calls) == 1
    after = snapshot(root, environment)
    # Native abort may rewrite stat-cache bytes; preserve logical index and data.
    for key in ("head", "refs", "stashes", "entries", "status", "files", "merge"):
        assert after[key] == before[key], key


@pytest.mark.parametrize("fault", ["status17", "merge17", "index-lock", "bad-ref"])
def test_native_errors_do_not_abort_or_rewrite_prior_state(
    repository: RepositoryFixture, tmp_path: Path, fault: str,
) -> None:
    root, environment = repository.root, repository.environment
    lock = git_path(root, environment, "index.lock")
    if fault == "index-lock":
        lock.write_bytes(b"existing fixture-owned lock\n")
    before = snapshot(root, environment)
    branch = "missing-ref" if fault == "bad-ref" else "feature/clean"
    result, calls = invoke(root, environment, tmp_path, branch, fault=fault)
    expected = 17 if fault in {"status17", "merge17"} else 128
    assert result.returncode != 0
    assert f"Error {expected}" in result.stderr, result.stdout + result.stderr
    assert not any("--abort" in arguments for arguments in calls), calls
    assert snapshot(root, environment) == before
    if fault == "index-lock":
        assert lock.read_bytes() == b"existing fixture-owned lock\n"


def test_foreign_merge_state_after_native_error_is_retained(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, fault="foreign17")
    assert result.returncode != 0
    assert "Error 17" in result.stderr
    assert not any("--abort" in arguments for arguments in calls)
    assert git_path(root, environment, "MERGE_HEAD").is_file()
    assert git_text(root, environment, "rev-parse", "MERGE_HEAD") == git_text(
        root, environment, "rev-parse", "feature/conflict",
    )
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]


def test_abort_failure_retains_both_native_failures_and_conflict_state(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "feature/conflict", fault="abort23")
    assert result.returncode != 0
    assert "merge_exit=1" in result.stdout + result.stderr
    assert "abort_exit=23" in result.stdout + result.stderr
    assert "Error 23" in result.stderr
    assert sum("--abort" in arguments for arguments in calls) == 1
    assert git_path(root, environment, "MERGE_HEAD").is_file()
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]


def test_ignored_checkpoint_is_not_overwritten(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    (root / "ignored-proof.txt").write_bytes(b"retained ignored checkpoint")
    (root / "ignored-proof.txt").chmod(0o600)
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "feature/ignored")
    assert result.returncode != 0, result.stdout + result.stderr
    assert not any("--abort" in arguments for arguments in calls)
    assert snapshot(root, environment) == before


# Additive result-path controls. Every original38 assertion above is unchanged.
def prepare_result_rename(root: Path, environment: dict[str, str], mode: str) -> Path:
    run_git(root, environment, "config", "merge.directoryRenames", mode)
    (root / "old").mkdir()
    (root / "old" / "seed").write_bytes(b"rename seed\n")
    run_git(root, environment, "add", "old/seed")
    run_git(root, environment, "commit", "-m", "result rename base")
    run_git(root, environment, "checkout", "-b", "result/rename")
    run_git(root, environment, "mv", "old", "new")
    run_git(root, environment, "commit", "-m", "rename old directory")
    run_git(root, environment, "checkout", "development")
    (root / "old" / "private.dat").write_bytes(b"committed HEAD bytes\n")
    run_git(root, environment, "add", "old/private.dat")
    run_git(root, environment, "commit", "-m", "HEAD adds file inside renamed directory")
    git_path(root, environment, "info/exclude").write_text("new/private.dat\n", encoding="utf-8")
    (root / "new").mkdir()
    checkpoint = root / "new" / "private.dat"
    checkpoint.write_bytes(b"unique ignored checkpoint bytes\n")
    checkpoint.chmod(0o600)
    assert run_git(root, environment, "cat-file", "-e", "result/rename:new/private.dat", check=False).returncode != 0
    assert not run_git(root, environment, "status", "--porcelain=v1").stdout
    return checkpoint


@pytest.mark.parametrize("mode", ["true", "conflict"])
@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
def test_result_directory_rename_preserves_ignored_destination(
    repository: RepositoryFixture, tmp_path: Path, mode: str, origin: str,
) -> None:
    root, environment = repository.root, repository.environment
    checkpoint = prepare_result_rename(root, environment, mode)
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "result/rename", origin=origin)
    assert checkpoint.read_bytes() == b"unique ignored checkpoint bytes\n", result.stdout + result.stderr
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert result.returncode != 0, result.stdout + result.stderr
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


@pytest.mark.parametrize("direction", ["ignored-file", "ignored-directory"])
@pytest.mark.parametrize("name", ["blocked", "blocked space", "blocked\nnewline"])
def test_result_file_directory_prefix_collision_preserves_checkpoint(
    repository: RepositoryFixture, tmp_path: Path, direction: str, name: str,
) -> None:
    root, environment = repository.root, repository.environment
    run_git(root, environment, "checkout", "-b", "result/prefix")
    incoming = root / name
    if direction == "ignored-file":
        incoming.mkdir()
        incoming = incoming / "incoming"
    incoming.write_bytes(b"incoming tracked bytes\n")
    run_git(root, environment, "add", ".")
    run_git(root, environment, "commit", "-m", "incoming prefix shape")
    run_git(root, environment, "checkout", "development")
    # Ignore the top-level basename using a NUL-safe Git CLI path-independent rule.
    git_path(root, environment, "info/exclude").write_text("*blocked*\n", encoding="utf-8")
    checkpoint = root / name
    if direction == "ignored-directory":
        checkpoint.mkdir()
        checkpoint = checkpoint / "retained"
    checkpoint.write_bytes(b"prefix checkpoint remains\n")
    checkpoint.chmod(0o600)
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "result/prefix")
    assert result.returncode != 0, result.stdout + result.stderr
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


def test_result_unchanged_target_path_does_not_refuse_legitimate_merge(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    (root / "retired.dat").write_bytes(b"base tracked bytes\n")
    run_git(root, environment, "add", "retired.dat")
    run_git(root, environment, "commit", "-m", "shared retired path")
    run_git(root, environment, "checkout", "-b", "result/unchanged")
    (root / "result-added.txt").write_bytes(b"legitimate incoming bytes\n")
    run_git(root, environment, "add", "result-added.txt")
    run_git(root, environment, "commit", "-m", "incoming leaves retired path unchanged")
    run_git(root, environment, "checkout", "development")
    run_git(root, environment, "rm", "retired.dat")
    run_git(root, environment, "commit", "-m", "HEAD deliberately removes retired path")
    git_path(root, environment, "info/exclude").write_text("retired.dat\n", encoding="utf-8")
    (root / "retired.dat").write_bytes(b"unrelated retained ignored bytes\n")
    (root / "retired.dat").chmod(0o600)
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "result/unchanged")
    assert result.returncode == 0, result.stdout + result.stderr
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]
    assert (root / "retired.dat").read_bytes() == b"unrelated retained ignored bytes\n"
    assert stat.S_IMODE((root / "retired.dat").stat().st_mode) == 0o600
    assert (root / "result-added.txt").read_bytes() == b"legitimate incoming bytes\n"
    assert git_path(root, environment, "MERGE_HEAD").is_file()
    assert not any("--abort" in arguments for arguments in calls)


@pytest.mark.parametrize("mode", ["true", "conflict"])
def test_result_native_merge_tree_exposes_destination_without_index_or_ref_changes(
    repository: RepositoryFixture, mode: str,
) -> None:
    root, environment = repository.root, repository.environment
    prepare_result_rename(root, environment, mode)
    before = snapshot(root, environment)
    native = run_git(root, environment, "merge-tree", "--write-tree", "--no-messages", "--name-only",
                     "-z", "HEAD", "result/rename", check=False)
    assert native.returncode == (0 if mode == "true" else 1), native.stderr
    tree_oid, separator, _unparsed_conflict_fields = native.stdout.partition(b"\0")
    assert separator == b"\0"
    assert run_git(root, environment, "cat-file", "-t", os.fsdecode(tree_oid)).stdout == b"tree\n"
    changes = run_git(root, environment, "diff-tree", "--no-commit-id", "-r", "-z", "--name-only",
                      "--no-renames", "--diff-filter=AMT", "HEAD", os.fsdecode(tree_oid)).stdout
    assert b"new/private.dat" in changes.split(b"\0")
    assert snapshot(root, environment) == before
    print("RESULT_NATIVE_VERSION", git_text(root, environment, "--version"))
    print("RESULT_NATIVE_MERGE_TREE", mode, native.returncode, os.fsdecode(tree_oid))


@pytest.mark.parametrize("fault, expected", [
    ("tree17", 17), ("tree129", 129), ("tree-malformed0", 2), ("tree-malformed1", 128),
])
def test_result_preflight_errors_fail_closed_before_native_merge(
    repository: RepositoryFixture, tmp_path: Path, fault: str, expected: int,
) -> None:
    root, environment = repository.root, repository.environment
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, fault=fault)
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"Error {expected}" in result.stderr, result.stdout + result.stderr
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


# Additive configuration controls. Every existing57 assertion stays unchanged.
def prepare_config_subtree(root: Path, environment: dict[str, str]) -> Path:
    run_git(root, environment, "config", "merge.directoryRenames", "false")
    run_git(root, environment, "checkout", "-b", "config/subtree")
    (root / "entry.txt").write_bytes(b"incoming subtree payload\n")
    run_git(root, environment, "add", "entry.txt")
    run_git(root, environment, "commit", "-m", "incoming subtree entry")
    run_git(root, environment, "checkout", "development")
    (root / "prefix").mkdir()
    run_git(root, environment, "mv", ".gitignore", "conflict.txt", "keep.txt", "mode.sh", "prefix/")
    run_git(root, environment, "commit", "-m", "HEAD nests the common project")
    git_path(root, environment, "info/exclude").write_text("prefix/entry.txt\n", encoding="utf-8")
    checkpoint = root / "prefix" / "entry.txt"
    checkpoint.write_bytes(b"configuration checkpoint stays\n")
    checkpoint.chmod(0o600)
    assert not run_git(root, environment, "status", "--porcelain=v1").stdout
    return checkpoint


@pytest.mark.parametrize("options", ["-s subtree", "-Xsubtree=prefix"])
@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
def test_config_branch_merge_options_refuse_before_result_or_actual_merge(
    repository: RepositoryFixture, tmp_path: Path, options: str, origin: str,
) -> None:
    root, environment = repository.root, repository.environment
    checkpoint = prepare_config_subtree(root, environment)
    run_git(root, environment, "config", "branch.development.mergeOptions", options)
    config = git_path(root, environment, "config")
    before_config = config.read_bytes()
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "config/subtree", origin=origin)
    print("CONFIG_BRANCH_NATIVE", options, origin, result.returncode, repr(checkpoint.read_bytes()))
    assert checkpoint.read_bytes() == b"configuration checkpoint stays\n", result.stdout + result.stderr
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert result.returncode != 0, result.stdout + result.stderr
    assert "GIT_MERGE_NC_REFUSED branch mergeOptions" in result.stderr
    assert not any("merge-tree" in arguments for arguments in calls), calls
    assert not merge_calls(calls), calls
    assert config.read_bytes() == before_config
    assert snapshot(root, environment) == before


def test_config_native_subtree_changes_result_paths_without_mutating_checkout(
    repository: RepositoryFixture,
) -> None:
    root, environment = repository.root, repository.environment
    prepare_config_subtree(root, environment)
    before = snapshot(root, environment)
    paths = {}
    for label, options in (("default", []), ("subtree", ["-Xsubtree=prefix"])):
        native = run_git(root, environment, "merge-tree", "--write-tree", "--no-messages",
                         "--name-only", "-z", *options, "HEAD", "config/subtree", check=False)
        assert native.returncode in (0, 1), native.stderr
        tree_oid, separator, _ = native.stdout.partition(b"\0")
        assert separator == b"\0"
        changes = run_git(root, environment, "diff-tree", "--no-commit-id", "-r", "-z",
                          "--name-only", "--no-renames", "--diff-filter=AMT",
                          "HEAD", os.fsdecode(tree_oid), "--").stdout
        paths[label] = set(changes.split(b"\0")) - {b""}
        print("CONFIG_RESULT_PATHS", label, native.returncode, sorted(paths[label]))
    assert b"prefix/entry.txt" not in paths["default"]
    assert b"entry.txt" in paths["default"]
    assert b"prefix/entry.txt" in paths["subtree"]
    assert snapshot(root, environment) == before
    print("CONFIG_NATIVE_VERSION", git_text(root, environment, "--version"))


@pytest.mark.parametrize("selection", ["default", "attribute"])
def test_config_external_driver_cannot_run_during_preflight(
    repository: RepositoryFixture, tmp_path: Path, selection: str,
) -> None:
    root, environment = repository.root, repository.environment
    checkpoint = root / "ignored-proof.txt"
    checkpoint.write_bytes(b"external driver checkpoint stays\n")
    checkpoint.chmod(0o600)
    marker = git_path(root, environment, "fixture-driver-ran")
    driver = tmp_path / "fixture-driver.py"
    driver.write_text(
        "from pathlib import Path\n"
        f"Path({str(checkpoint)!r}).write_bytes(b'external driver overwrote checkpoint\\n')\n"
        f"Path({str(marker)!r}).write_text('native driver executed\\n')\n",
        encoding="utf-8",
    )
    run_git(root, environment, "config", "merge.packet-driver.driver",
            shlex.join([sys.executable, str(driver)]))
    if selection == "default":
        run_git(root, environment, "config", "merge.default", "packet-driver")
    else:
        (root / ".gitattributes").write_text("conflict.txt merge=packet-driver\n", encoding="utf-8")
        run_git(root, environment, "add", ".gitattributes")
        run_git(root, environment, "commit", "-m", "select fixture low-level driver")
    config = git_path(root, environment, "config")
    before_config = config.read_bytes()
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "feature/conflict")
    print("CONFIG_EXTERNAL_DRIVER_NATIVE", selection, result.returncode, marker.exists(),
          repr(checkpoint.read_bytes()))
    assert checkpoint.read_bytes() == b"external driver checkpoint stays\n", result.stdout + result.stderr
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert not marker.exists()
    assert result.returncode != 0, result.stdout + result.stderr
    assert "GIT_MERGE_NC_REFUSED configured merge driver" in result.stderr
    assert not any("merge-tree" in arguments for arguments in calls), calls
    assert not merge_calls(calls), calls
    assert config.read_bytes() == before_config
    assert snapshot(root, environment) == before


def test_config_builtin_union_remains_native_and_preserves_no_commit_intent(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    run_git(root, environment, "config", "merge.default", "union")
    config = git_path(root, environment, "config")
    before_config = config.read_bytes()
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, "feature/conflict")
    assert result.returncode == 0, result.stdout + result.stderr
    assert set((root / "conflict.txt").read_bytes().splitlines()) == {
        b"development conflict", b"feature conflict",
    }
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]
    assert config.read_bytes() == before_config
    assert git_path(root, environment, "MERGE_HEAD").is_file()
    assert not git_path(root, environment, "MERGE_AUTOSTASH").exists()
    actual = merge_calls(calls)
    assert len(actual) == 1
    assert actual[0].count("--strategy=ort") == 1
    assert actual[0].index("--strategy=ort") < actual[0].index("--")
    assert "--no-verify" not in actual[0]
    assert not any("hooksPath=" in value or "hookspath=" in value for value in actual[0])


@pytest.mark.parametrize("default_strategy", ["ours", "missing-fixture-strategy"])
def test_config_explicit_ort_replaces_default_strategy_without_changing_config(
    repository: RepositoryFixture, tmp_path: Path, default_strategy: str,
) -> None:
    root, environment = repository.root, repository.environment
    run_git(root, environment, "config", "pull.twohead", default_strategy)
    config = git_path(root, environment, "config")
    before_config = config.read_bytes()
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "feature.txt").read_bytes() == b"feature payload\n"
    assert config.read_bytes() == before_config
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]
    actual = merge_calls(calls)
    assert len(actual) == 1
    assert actual[0].count("--strategy=ort") == 1
    assert "--no-verify" not in actual[0]
    assert not git_path(root, environment, "MERGE_AUTOSTASH").exists()


def test_config_effective_empty_branch_options_allow_normal_merge(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    run_git(root, environment, "config", "--add", "branch.development.mergeOptions", "-s subtree")
    run_git(root, environment, "config", "--add", "branch.development.mergeOptions", "")
    config = git_path(root, environment, "config")
    before_config = config.read_bytes()
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "feature.txt").read_bytes() == b"feature payload\n"
    assert config.read_bytes() == before_config
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]
    assert merge_calls(calls)[0].count("--strategy=ort") == 1


def test_config_detached_head_keeps_clean_native_merge_behavior(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    run_git(root, environment, "checkout", "--detach")
    before = snapshot(root, environment)
    result, _calls = invoke(root, environment, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]
    assert (root / "feature.txt").read_bytes() == b"feature payload\n"
    assert git_path(root, environment, "MERGE_HEAD").is_file()


@pytest.mark.parametrize("fault", ["symbolic17", "config17"])
def test_config_native_query_error_preserves_status_and_prevents_preflight(
    repository: RepositoryFixture, tmp_path: Path, fault: str,
) -> None:
    root, environment = repository.root, repository.environment
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, fault=fault)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Error 17" in result.stderr, result.stdout + result.stderr
    assert not any("merge-tree" in arguments for arguments in calls), calls
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


def test_config_signature_verification_is_not_silently_disabled(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    root, environment = repository.root, repository.environment
    run_git(root, environment, "config", "merge.verifySignatures", "true")
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "signature" in result.stderr.lower()
    assert not any("--abort" in arguments for arguments in calls), calls
    assert not any("--no-verify-signatures" in arguments for arguments in calls), calls
    assert snapshot(root, environment) == before


def install_full_make(root: Path, environment: dict[str, str]) -> None:
    """Track the real entrypoint before exercising a clean confined checkout."""
    shutil.copyfile(ROOT / "Makefile", root / "Makefile")
    shutil.copytree(ROOT / "make", root / "make")
    shutil.copyfile(ROOT / "pyproject.toml", root / "pyproject.toml")
    (root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    run_git(root, environment, "add", "Makefile", "make", "pyproject.toml", "scripts")
    run_git(root, environment, "commit", "-m", "install actual Make entrypoint")


@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
@pytest.mark.parametrize("payload", [
    "$(shell touch FULL_MAKE_TRIPWIRE)", chr(96) + "touch FULL_MAKE_TRIPWIRE" + chr(96),
], ids=["make-expression", "shell-expression"])
def test_full_make_branch_data_stays_literal_before_startup(
    repository: RepositoryFixture, tmp_path: Path, origin: str, payload: str,
) -> None:
    """Real startup exports must never evaluate public branch data."""
    root, environment = repository.root, repository.environment
    install_full_make(root, environment)
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, payload, origin=origin, full_make=True)
    assert not (root / "FULL_MAKE_TRIPWIRE").exists(), result.stdout + result.stderr
    assert result.returncode != 0
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
def test_full_make_clean_merge_retains_native_no_commit_intent(
    repository: RepositoryFixture, tmp_path: Path, origin: str,
) -> None:
    """The actual entrypoint supports all three literal branch origins."""
    root, environment = repository.root, repository.environment
    install_full_make(root, environment)
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, origin=origin, full_make=True)
    assert result.returncode == 0, result.stdout + result.stderr
    after = snapshot(root, environment)
    for key in ("head", "refs", "stashes"):
        assert after[key] == before[key]
    assert (root / "feature.txt").read_bytes() == b"feature payload\n"
    assert git_path(root, environment, "MERGE_HEAD").is_file()
    assert not git_path(root, environment, "MERGE_AUTOSTASH").exists()
    actual = merge_calls(calls)
    assert len(actual) == 1 and actual[0].count("--strategy=ort") == 1
    assert {"--no-commit", "--no-ff", "--no-autostash", "--no-overwrite-ignore"} <= set(actual[0])


@pytest.mark.parametrize("origin", ["command", "inherited", "environment-e"])
def test_full_make_dirty_refusal_keeps_the_staged_source_test(
    repository: RepositoryFixture, tmp_path: Path, origin: str,
) -> None:
    """A staged source-identical test is refused without opening or aborting a merge."""
    root, environment = repository.root, repository.environment
    install_full_make(root, environment)
    (root / "new-test.py").write_bytes(b"# staged source-identical test\n")
    run_git(root, environment, "add", "new-test.py")
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, origin=origin, full_make=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "GIT_MERGE_NC_REFUSED dirty" in result.stderr
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


def test_namesake_file_cannot_skip_dirty_checkout_refusal(
    repository: RepositoryFixture, tmp_path: Path,
) -> None:
    """A file named after the command must not turn admission into a no-op."""
    root, environment = repository.root, repository.environment
    (root / "git-merge-nc").write_bytes(b"retained namesake checkpoint\n")
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "GIT_MERGE_NC_REFUSED dirty" in result.stderr
    assert not merge_calls(calls), calls
    assert snapshot(root, environment) == before


def test_merge_fixture_repr_keeps_owned_environment_private(tmp_path: Path) -> None:
    environment = {
        "GLUDD_FAKE_PRIVATE_CARRIER": "owned-private-repr-marker",
        "GLUDD_FAKE_RETAINED_CARRIER": "owned-retained-repr-marker",
    }
    before = dict(environment)
    fixture = _repository_state(tmp_path, environment)

    rendered = repr(fixture)

    assert all(name not in rendered and value not in rendered for name, value in environment.items())
    assert str(tmp_path) in rendered
    assert environment == before
