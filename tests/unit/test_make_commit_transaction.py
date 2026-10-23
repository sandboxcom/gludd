"""Native Make transactions must hold the existing common-directory mutex."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from general_ludd.git_automation import locking

if TYPE_CHECKING:
    import active_work_status as inventory_owner
    import kill_owned_gate as cleanup_owner
    import stream_command as observer
    import task_watchdog as watchdog_owner
    from makefile_layout import compose_makefile
else:
    from scripts import active_work_status as inventory_owner
    from scripts import kill_owned_gate as cleanup_owner
    from scripts import stream_command as observer
    from scripts import task_watchdog as watchdog_owner
    from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "git-commit", "commit-no-verify", "git-commit-no-verify", "git-amend-msg",
    "repo-commit", "ship-commit", "ship-commit-files", "git-commit-file",
    "test-and-commit", "commit-bootstrap", "commit-and-ship", "commit-and-ship-push",
)
GUARDS = (
    "_gate-mutation-guard", "_gate-fresh-check", "_commit-lint-guard",
    "_commit-docstring-guard", "_pre-commit-stage-guard", "_stash-leak-guard",
    "_push-parameter-audit", "_pre-commit-stash-audit", "_edit-commit-atomicity-guard",
    "_pre-commit-spec-quality-guard", "_pre-commit-content-guard",
)
ADAPTER = r'''import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

state = Path(os.environ["TRANSACTION_STATE"])
case = os.environ.get("TRANSACTION_CASE", "first")
args = sys.argv[1:]
def event(phase):
    with (state / "events").open("a") as stream:
        stream.write(case + ":" + phase + "\n")
def barrier(phase):
    (state / (case + "." + phase)).write_text("entered")
    deadline = time.monotonic() + 15
    while (case == "first" and os.environ.get("TRANSACTION_BLOCK") == "1"
           and os.environ.get("TRANSACTION_BLOCK_PHASE", "collect") == phase):
        if (state / "release").exists():
            break
        if time.monotonic() >= deadline:
            raise SystemExit(74)
        time.sleep(0.01)
if Path(sys.argv[0]).name == "git":
    if args and args[0] in {"add", "commit"}:
        if case == "second" and os.environ.get("TRANSACTION_CHECK_FIRST_CHILD") == "1":
            import psutil
            pid = int((state / "first.collect-pid").read_text())
            try:
                if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                    raise SystemExit(76)
            except psutil.NoSuchProcess:
                pass
        event(args[0])
        if args[0] == "commit":
            barrier("commit")
        result = int(os.environ.get("TRANSACTION_" + args[0].upper() + "_RC", "0"))
        if result:
            raise SystemExit(result)
    os.execve(os.environ["TRANSACTION_REAL_GIT"], ["git", *args], dict(os.environ))
if args and args[0] == "run":
    args = args[1:]
    if args and args[0] == "pre-commit":
        event("pre-commit")
        raise SystemExit(0)
    if args and args[0] == "python":
        args = args[1:]
    event("uv")
    if args[:2] == ["-m", "pytest"]:
        event("test")
        raise SystemExit(0)
    os.execve(sys.executable, [sys.executable, *args], dict(os.environ))
if args and args[0] == "collect":
    if os.environ.get("TRANSACTION_IGNORE_TERM") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (state / (case + ".collect-pid")).write_text(str(os.getpid()))
    event("collect")
    barrier("collect")
    raise SystemExit(int(os.environ.get("TRANSACTION_COLLECT_RC", "0")))
if args and args[0] == "guard":
    event("guard-" + args[1])
    raise SystemExit(75 if args[1] == os.environ.get("TRANSACTION_FAIL_GUARD") else 0)
raise SystemExit("unknown native adapter input")
'''


def _block(text: str, name: str) -> str | None:
    found = re.search(rf"^{re.escape(name)}:[^\n]*\n(?:\t[^\n]*\n)*", text, re.MULTILINE)
    return found.group() if found else None


def _native_rules() -> str:
    sources = [
        (ROOT / "make" / name).read_text(encoding="utf-8")
        for name in (
            "20-recovery-and-git.mk", "40-cross-version-and-worktrees.mk",
            "50-development-and-guardrails.mk",
        )
    ]
    text = "\n".join(sources)
    names = ["_commit-lock-acquire", "git-add", "git-add-all", *TARGETS]
    names.extend("_commit-locked-" + name for name in TARGETS)
    blocks = [block for name in names if (block := _block(text, name)) is not None]
    macro = re.search(r"^define _commit-transaction\n.*?^endef\n", text, re.MULTILINE | re.DOTALL)
    frontend = re.search(
        r"^# Native Make inspection owns.*?(?=^define _commit-transaction\n)",
        text, re.MULTILINE | re.DOTALL,
    )
    scheduling = re.search(
        r"^ifneq \(\$\(filter _commit-locked-%,\$\(MAKECMDGOALS\)\),\)\n\.NOTPARALLEL:\nendif\n",
        text, re.MULTILINE,
    )
    phony = re.search(r"^\.PHONY: _commit-lock-acquire _gate-mutation-guard[^\n]*\n", text, re.MULTILINE)
    return (
        (frontend.group() if frontend else "") + (macro.group() if macro else "")
        + (scheduling.group() if scheduling else "")
        + (phony.group() if phony else "") + "\n".join(blocks)
    )


def _environment() -> dict[str, str]:
    excluded = {
        "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "PYTEST_ARGS",
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    }
    environment = {key: value for key, value in os.environ.items() if key not in excluded}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "src"), str(ROOT / "scripts"))),
    })
    return environment


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = shutil.which("git")
    assert command is not None
    return subprocess.run(
        [command, *args], cwd=root, env=_environment(), capture_output=True,
        text=True, check=True, timeout=15,
    )


@dataclass
class NativeTransaction:
    root: Path
    state: Path
    environment: dict[str, str]

    def start(
        self, *, case: str = "first", target: str = "ship-commit-files",
        filename: str = "first.txt", block: bool = False, state: dict[str, str] | None = None,
        make_flags: tuple[str, ...] = (), pass_fds: tuple[int, ...] = (), uv: str | None = None,
    ) -> subprocess.Popen[str]:
        environment = dict(self.environment)
        environment.update({
            "TRANSACTION_CASE": case, "TRANSACTION_BLOCK": "1" if block else "0",
            **(state or {}),
        })
        return subprocess.Popen(
            ["make", "--no-print-directory", *make_flags, target, f"FILES={filename}",
             f"FILE={self.state / 'message.txt'}", f"MSG=owned {case}", "PUSH=0",
             f"UV={uv or shlex.join((sys.executable, str(self.state / 'adapter.py')))}"],
            cwd=self.root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            pass_fds=pass_fds,
        )

    def events(self) -> list[str]:
        path = self.state / "events"
        return path.read_text().splitlines() if path.exists() else []

    def release(self) -> None:
        (self.state / "release").touch()


def _fixture(root: Path, state: Path) -> NativeTransaction:
    root.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Owned transaction fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "core.hooksPath", str(state / "no-hooks"))
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-q", "-m", "fixture seed")
    (root / ".git" / "info" / "exclude").write_text("Makefile\nsrc/\nscripts/\n.gate-logs/\n")
    (state / "message.txt").write_text("owned message file\n")
    _write_fixture_inputs(root, state)
    environment = _environment()
    environment.update({
        "PATH": str(state / "bin") + os.pathsep + environment["PATH"],
        "TRANSACTION_STATE": str(state), "TRANSACTION_REAL_GIT": str(shutil.which("git")),
    })
    return NativeTransaction(root, state, environment)


def _write_fixture_inputs(root: Path, state: Path) -> None:
    binary = state / "bin"
    binary.mkdir(exist_ok=True)
    adapter = state / "adapter.py"
    adapter.write_text(ADAPTER, encoding="utf-8")
    git = binary / "git"
    git.write_text(f"#!{sys.executable}\n{ADAPTER}", encoding="utf-8")
    git.chmod(0o700)
    helper = shlex.join((sys.executable, str(adapter)))
    guards = "\n".join(f"{name}:\n\t@{helper} guard {name}\n" for name in GUARDS)
    native = _native_rules().replace("/tmp/gludd-commit.lock", str(state / "legacy-commit.lock"))
    makefile = (
        f"UV := {helper}\nPYTHON := {shlex.quote(sys.executable)}\n"
        "OBSERVED_ROOT := .gate-logs/observed\nOBSERVED_HEARTBEAT_SECS := 2\n"
        "OBSERVED_QUIET_SECS := 0\nOBSERVED_MAX_SECS := 30\nOBSERVED_RETAIN_RUNS := 20\n"
        + guards + "\ntest-count collect-check:\n\t@" + helper + " collect\n"
        "check-gate-fresh preflight dist lint-fix development-push ci-verdict batch-push:\n\t@true\n" + native
    )
    (root / "Makefile").write_text(makefile, encoding="utf-8")
    source = root / "src" / "general_ludd" / "git_automation"
    source.mkdir(parents=True, exist_ok=True)
    (source / "locking.py").symlink_to(ROOT / "src" / "general_ludd" / "git_automation" / "locking.py")
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "stream_command.py").symlink_to(ROOT / "scripts" / "stream_command.py")
    for filename in ("first.txt", "second.txt"):
        (root / filename).write_text(filename + "\n")


def _wait_for(path: Path, process: subprocess.Popen[str], timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            pytest.fail(f"native owner ended before barrier: {process.returncode}\n{stdout}\n{stderr}")
        if time.monotonic() >= deadline:
            pytest.fail(f"native owner did not reach barrier {path}")
        time.sleep(0.01)


def _drain(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        return process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)


@pytest.mark.parametrize("linked", [False, True])
def test_native_two_make_callers_hold_common_lock_through_collection(
    tmp_path: Path, linked: bool,
) -> None:
    first = _fixture(tmp_path / "repo", tmp_path / "first-state")
    second_root = first.root
    if linked:
        second_root = tmp_path / "linked"
        _git(first.root, "worktree", "add", "-q", "-b", "owned-second", str(second_root))
        _write_fixture_inputs(second_root, first.state)
    second = NativeTransaction(second_root, first.state, first.environment)
    one = first.start(block=True)
    two: subprocess.Popen[str] | None = None
    held = False
    try:
        _wait_for(first.state / "first.collect", one)
        two = second.start(case="second", filename="second.txt")
        common = first.root / ".git" / "gludd-git.lock"
        with common.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held = True
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        assert held, "native common-directory lock was released before collection completed"
    finally:
        first.release()
        out_one = _drain(one)
        out_two = _drain(two) if two is not None else ("", "")
    assert one.returncode == 0, out_one
    assert two is not None and two.returncode == 0, out_two
    for output in (out_one, out_two):
        assert f"source={ROOT / 'src/general_ludd/git_automation/locking.py'}" in output[1]
        assert f"observer={ROOT / 'scripts/stream_command.py'}" in output[1]
    events = first.events()
    assert events.index("first:add") < events.index("first:collect") < events.index("first:commit")
    assert events.index("first:commit") < events.index("second:add")
    for root, filename in ((first.root, "first.txt"), (second_root, "second.txt")):
        assert _git(root, "show", f"HEAD:{filename}").stdout == filename + "\n"


def test_independent_repository_can_commit_while_other_transaction_is_held(tmp_path: Path) -> None:
    first = _fixture(tmp_path / "repo-one", tmp_path / "first-state")
    second = _fixture(tmp_path / "repo-two", tmp_path / "second-state")
    one = first.start(block=True)
    two: subprocess.Popen[str] | None = None
    try:
        _wait_for(first.state / "first.collect", one)
        two = second.start(case="second", filename="second.txt")
        output = _drain(two)
        assert two.returncode == 0, output
        assert not (first.state / "release").exists()
        assert one.poll() is None
    finally:
        first.release()
        _drain(one)
        if two is not None and two.poll() is None:
            _drain(two)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("make_flags", [(), ("-e",)])
def test_every_public_commit_target_holds_native_mutex_until_actual_commit(
    tmp_path: Path, target: str, make_flags: tuple[str, ...],
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    if target != "ship-commit-files":
        _git(fixture.root, "add", "first.txt")
    process = fixture.start(
        target=target, block=True, make_flags=make_flags,
        state={"TRANSACTION_BLOCK_PHASE": "commit"},
    )
    try:
        _wait_for(fixture.state / "first.commit", process)
        with (fixture.root / ".git" / "gludd-git.lock").open("r+") as handle, pytest.raises(BlockingIOError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        fixture.release()
        stdout, stderr = _drain(process)
    assert process.returncode == 0, (stdout, stderr)
    assert _git(fixture.root, "show", "HEAD:first.txt").stdout == "first.txt\n"
    assert "outcome=passed" in stderr and "release_ready=false" in stderr


@pytest.mark.parametrize("state,expected_status", [
    ({"TRANSACTION_ADD_RC": "73"}, 73),
    ({"TRANSACTION_COLLECT_RC": "2"}, 2),
    ({"TRANSACTION_COLLECT_RC": "124"}, 124),
    ({"TRANSACTION_COMMIT_RC": "73"}, 73),
    ({"TRANSACTION_FAIL_GUARD": "_commit-lint-guard"}, 75),
])
def test_failure_preserves_native_status_and_owned_state_then_releases_mutex(
    tmp_path: Path, state: dict[str, str], expected_status: int,
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    before = _git(fixture.root, "rev-parse", "HEAD").stdout
    process = fixture.start(state=state)
    stdout, stderr = _drain(process)
    assert process.returncode != 0
    assert f"Error {expected_status}" in stdout + stderr
    assert "outcome=passed" not in stderr
    assert _git(fixture.root, "rev-parse", "HEAD").stdout == before
    staged = _git(fixture.root, "diff", "--cached", "--name-only").stdout
    assert staged == ("" if "TRANSACTION_ADD_RC" in state else "first.txt\n")
    with (fixture.root / ".git" / "gludd-git.lock").open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    repaired = fixture.start()
    assert _drain(repaired) and repaired.returncode == 0


@pytest.mark.parametrize("descriptor", ["", "-1", "bad", "3.0", "1", "99999999999", "9999999999", "\u0663"])
def test_descriptor_text_cannot_grant_native_transaction_entry(
    tmp_path: Path, descriptor: str,
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    with pytest.raises(ValueError):
        locking._assert_make_transaction(str(fixture.root), descriptor)


def test_actual_flock_rejects_same_inode_contender_but_accepts_inherited_owner(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    common = fixture.root / ".git"
    with locking.git_repo_lock(str(fixture.root)):
        fd = locking._file_lock_fds[str(common.resolve())]
        assert locking._assert_make_transaction(str(fixture.root), str(fd)) == fd
        with (common / "gludd-git.lock").open("r+") as contender:
            assert os.fstat(fd).st_ino == os.fstat(contender.fileno()).st_ino
            with pytest.raises(BlockingIOError):
                locking._assert_make_transaction(str(fixture.root), str(contender.fileno()))
        with (
            (fixture.state / "foreign.lock").open("w+") as foreign,
            pytest.raises(ValueError, match="different repository"),
        ):
            locking._assert_make_transaction(str(fixture.root), str(foreign.fileno()))
    with pytest.raises(OSError):
        locking._assert_make_transaction(str(fixture.root), str(fd))


@pytest.mark.parametrize("mode", ["forged-environment", "foreign-descriptor", "same-inode-contender"])
def test_private_native_rule_refuses_unowned_descriptor_before_staging(
    tmp_path: Path, mode: str,
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    index = (fixture.root / ".git" / "index").read_bytes()
    with locking.git_repo_lock(str(fixture.root)), contextlib.ExitStack() as stack:
        file = fixture.root / ".git" / "gludd-git.lock"
        if mode == "foreign-descriptor":
            file = fixture.state / "foreign.lock"
        handle = stack.enter_context(file.open("a+"))
        process = fixture.start(
            target="_commit-locked-ship-commit-files",
            state={
                "GLUDD_COMMIT_TRANSACTION_FD": str(handle.fileno()),
                "GLUDD_COMMIT_TRANSACTION_PYTHON": sys.executable,
            },
            pass_fds=() if mode == "forged-environment" else (handle.fileno(),),
        )
        stdout, stderr = _drain(process)
    assert process.returncode != 0, (stdout, stderr)
    assert "COMMIT-LOCK: refused" in stderr
    assert "first:add" not in fixture.events() and "first:collect" not in fixture.events()
    assert (fixture.root / ".git" / "index").read_bytes() == index


def test_signal_drains_native_make_descendants_before_another_caller_can_stage(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    one = fixture.start(block=True, state={"TRANSACTION_IGNORE_TERM": "1"})
    two: subprocess.Popen[str] | None = None
    owner_pid = 0
    try:
        _wait_for(fixture.state / "first.collect", one)
        receipt_path = fixture.root / ".gate-logs" / "observed" / "commit-transaction" / "current.json"
        receipt = json.loads(receipt_path.read_text())
        owner_pid = int(receipt["owner_pid"])
        assert owner_pid != one.pid and owner_pid > 0
        two = fixture.start(
            case="second", filename="first.txt", state={"TRANSACTION_CHECK_FIRST_CHILD": "1"},
        )
        os.kill(owner_pid, signal.SIGTERM)
        out_one = _drain(one)
        out_two = _drain(two)
        assert one.returncode != 0, out_one
        assert two.returncode == 0, out_two
        terminal = json.loads(Path(receipt["log_path"]).with_suffix(".json").read_text())
        assert terminal["state"] == "interrupted" and terminal["exit_code"] == 143
        events = fixture.events()
        assert "first:commit" not in events
        assert events.count("second:commit") == 1
    finally:
        fixture.release()
        if one.poll() is None and owner_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(owner_pid, signal.SIGTERM)
        _drain(one)
        if two is not None:
            _drain(two)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("native_status", [0, 73, 143])
def test_existing_observer_receives_exact_transaction_and_open_native_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    target: str, native_status: int,
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    monkeypatch.chdir(fixture.root)
    monkeypatch.setitem(sys.modules, "stream_command", observer)
    carried = dict(os.environ)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def capture(command: list[str], _log: Path, **options: object) -> int:
        calls.append((command, options))
        assert options["max_seconds"] == 31 and options["quiet_seconds"] == 7
        assert options["heartbeat_seconds"] == 2 and options["retain_runs"] == 4
        descriptors = options["pass_fds"]
        assert isinstance(descriptors, tuple) and len(descriptors) == 1
        descriptor = descriptors[0]
        assert isinstance(descriptor, int)
        assert locking._assert_make_transaction(str(fixture.root), str(descriptor)) == descriptor
        with (fixture.root / ".git" / "gludd-git.lock").open("r+") as contender, pytest.raises(BlockingIOError):
            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return native_status

    monkeypatch.setattr(observer, "stream_command", capture)
    result = locking._run_make_transaction(
        target, "make", "Makefile", observed_root=".gate-logs/observed",
        heartbeat_seconds=2, quiet_seconds=7, max_seconds=31, retain_runs=4,
    )
    assert result == native_status and len(calls) == 1
    assert calls[0][0] == [
        "make", "--no-print-directory", "-f", str(fixture.root / "Makefile"), "_commit-locked-" + target,
    ]
    assert dict(os.environ) == carried
    guidance = capsys.readouterr().err
    assert f"outcome={'passed' if native_status == 0 else 'failed'}" in guidance
    assert f"native_status={native_status}" in guidance and "release_ready=false" in guidance


@pytest.mark.parametrize("target,make,makefile,observed_root,message", [
    ("unrelated", "make", "Makefile", ".gate-logs/observed", "unsupported native"),
    ("ship-commit", "sh", "Makefile", ".gate-logs/observed", "one Make executable"),
    ("ship-commit", "make -j8", "Makefile", ".gate-logs/observed", "one Make executable"),
    ("ship-commit", "make", "missing.mk", ".gate-logs/observed", "Makefile"),
    ("ship-commit", "make", "Makefile", "../foreign-observed", "private"),
])
def test_transaction_input_refusals_start_no_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    target: str, make: str, makefile: str, observed_root: str, message: str,
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    monkeypatch.chdir(fixture.root)
    with pytest.raises(ValueError, match=message):
        locking._run_make_transaction(
            target, make, makefile, observed_root=observed_root,
            heartbeat_seconds=2, quiet_seconds=0, max_seconds=30, retain_runs=4,
        )
    assert fixture.events() == []


def test_cli_assertion_and_usage_preserve_native_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    monkeypatch.chdir(fixture.root)
    monkeypatch.delenv("GLUDD_COMMIT_TRANSACTION_FD", raising=False)
    assert locking.main(["--assert-transaction"]) == 2
    assert "outcome=refused" in capsys.readouterr().err
    with locking.git_repo_lock(str(fixture.root)):
        fd = locking._file_lock_fds[str((fixture.root / ".git").resolve())]
        monkeypatch.setenv("GLUDD_COMMIT_TRANSACTION_FD", str(fd))
        assert locking.main(["--assert-transaction"]) == 0
    with pytest.raises(SystemExit) as error:
        locking.main(["--make-target", "unrelated"])
    assert error.value.code == 2


@pytest.mark.parametrize("target", TARGETS)
def test_native_dry_run_plans_without_lock_observation_staging_or_commit(tmp_path: Path, target: str) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    before = _git(fixture.root, "rev-parse", "HEAD").stdout
    index = (fixture.root / ".git" / "index").read_bytes()
    process = fixture.start(target=target, make_flags=("-n", "-j2"))
    stdout, stderr = _drain(process)
    assert process.returncode == 0, (stdout, stderr)
    assert "outcome=planned" in stdout + stderr and "source_checkpoint=false" in stdout + stderr
    assert "outcome=passed" not in stdout + stderr and "Committed locally" not in stdout
    assert _git(fixture.root, "rev-parse", "HEAD").stdout == before
    assert (fixture.root / ".git" / "index").read_bytes() == index
    assert not (fixture.root / ".git" / "gludd-git.lock").exists()
    assert not (fixture.root / ".gate-logs").exists()
    assert not {"first:add", "first:collect", "first:commit"}.intersection(fixture.events())


@pytest.mark.parametrize("target", ["ship-commit-files", "commit-and-ship"])
def test_native_jobserver_crosses_existing_observer_without_warnings(tmp_path: Path, target: str) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    process = fixture.start(target=target, make_flags=("-j2",))
    stdout, stderr = _drain(process)
    assert process.returncode == 0, (stdout, stderr)
    assert "jobserver unavailable" not in (stdout + stderr).lower()
    assert "warning:" not in (stdout + stderr).lower()
    assert _git(fixture.root, "show", "HEAD:first.txt").stdout == "first.txt\n"


def test_native_parent_siblings_remain_parallel_on_installed_make(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    sibling = fixture.state / "parallel_sibling.py"
    sibling.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "root=Path(sys.argv[1]); name=sys.argv[2]\n"
        "(root/name).touch()\ndeadline=time.monotonic()+4\n"
        "while not all((root/key).exists() for key in ('left','right')):\n"
        "    if time.monotonic()>deadline: raise SystemExit(78)\n"
        "    time.sleep(0.01)\n",
    )
    with (fixture.root / "Makefile").open("a") as stream:
        for name in ("left", "right"):
            command = shlex.join((sys.executable, str(sibling), str(fixture.state), name))
            stream.write(f"\ngludd-parallel-{name}:\n\t@{command}\n")
    result = subprocess.run(
        ["make", "--no-print-directory", "-j2", "gludd-parallel-left", "gludd-parallel-right"],
        cwd=fixture.root, env=fixture.environment, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning:" not in result.stderr.lower()


def test_invalid_private_entry_cannot_run_quality_children_in_parallel(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    process = fixture.start(
        target="_commit-locked-ship-commit", make_flags=("-j2",),
        state={"GLUDD_COMMIT_TRANSACTION_PYTHON": sys.executable, "GLUDD_COMMIT_TRANSACTION_FD": "9999999999"},
    )
    stdout, stderr = _drain(process)
    assert process.returncode != 0, (stdout, stderr)
    assert "Traceback" not in stderr and "outcome=refused" in stderr
    assert fixture.events() == ["first:guard-_gate-mutation-guard", "first:uv"]


def test_native_source_fronts_its_own_observer_ahead_of_outside_decoy(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    decoy = fixture.state / "outside"
    decoy.mkdir()
    (decoy / "stream_command.py").write_text("raise RuntimeError('outside observer was imported')\n")
    environment = os.pathsep.join((str(decoy), fixture.environment["PYTHONPATH"]))
    process = fixture.start(state={"PYTHONPATH": environment})
    stdout, stderr = _drain(process)
    assert process.returncode == 0, (stdout, stderr)
    assert f"observer={ROOT / 'scripts/stream_command.py'}" in stderr
    assert "outside observer was imported" not in stdout + stderr


@pytest.mark.parametrize("flags", [
    "n -- ID=ordinary", "nrR -- MSG=owned", "--dry-run -- MSG=owned",
    "--just-print", "--recon", "-n", "--jobserver-auth=fifo:/unused-native-input",
    "-- ID=contains-n",
])
def test_native_make_flag_modes_retain_make_input_meaning(
    monkeypatch: pytest.MonkeyPatch, flags: str,
) -> None:
    monkeypatch.setenv("MAKEFLAGS", flags)
    mode, descriptors = locking._native_make_options()
    assert mode == ("execute" if flags in {
        "--jobserver-auth=fifo:/unused-native-input", "-- ID=contains-n",
    } else "dry-run")
    assert descriptors == ()


def test_native_make_pipe_jobserver_preserves_exact_open_descriptors(monkeypatch: pytest.MonkeyPatch) -> None:
    read_fd, write_fd = os.pipe()
    try:
        old_flags = tuple(fcntl.fcntl(fd, fcntl.F_GETFD) for fd in (read_fd, write_fd))
        monkeypatch.setenv(
            "MAKEFLAGS",
            f"e --jobserver-fds={read_fd},{write_fd} --jobserver-auth={read_fd},{write_fd} -- MSG=ordinary",
        )
        assert locking._native_make_options() == ("execute", (read_fd, write_fd))
        assert tuple(fcntl.fcntl(fd, fcntl.F_GETFD) for fd in (read_fd, write_fd)) == old_flags
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("flags", [
    "--jobserver-auth=bad", "--jobserver-auth=3,3", "--jobserver-fds=9999999999,3",
])
def test_unusable_native_jobserver_refuses_instead_of_hiding_warning(
    monkeypatch: pytest.MonkeyPatch, flags: str,
) -> None:
    monkeypatch.setenv("MAKEFLAGS", flags)
    with pytest.raises(ValueError):
        locking._native_make_options()


def test_native_cli_rejects_missing_common_directory_and_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Makefile").write_text("help:\n\t@true\n")
    with pytest.raises(ValueError, match="POSIX Git common"):
        locking._run_make_transaction(
            "ship-commit", "make", "Makefile", observed_root=".gate-logs/observed",
            heartbeat_seconds=2, quiet_seconds=0, max_seconds=30, retain_runs=4,
        )
    with pytest.raises(ValueError, match="POSIX Git common"):
        locking._assert_make_transaction(str(tmp_path), "3")
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    monkeypatch.chdir(fixture.root)
    monkeypatch.setattr(locking, "_HAVE_FCNTL", False)
    with pytest.raises(ValueError, match="POSIX Git common"):
        locking._run_make_transaction(
            "ship-commit", "make", "Makefile", observed_root=".gate-logs/observed",
            heartbeat_seconds=2, quiet_seconds=0, max_seconds=30, retain_runs=4,
        )


def test_native_assertion_refuses_nonregular_and_replaced_mutex(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    mutex = fixture.root / ".git" / "gludd-git.lock"
    with locking.git_repo_lock(str(fixture.root)):
        fd = locking._file_lock_fds[str((fixture.root / ".git").resolve())]
        read_fd, write_fd = os.pipe()
        try:
            with pytest.raises(ValueError, match="regular mutex"):
                locking._assert_make_transaction(str(fixture.root), str(read_fd))
        finally:
            os.close(read_fd)
            os.close(write_fd)
        held_inode = fixture.state / "owned-mutex"
        mutex.rename(held_inode)
        mutex.symlink_to(held_inode)
        with pytest.raises(ValueError, match="regular mutex"):
            locking._assert_make_transaction(str(fixture.root), str(fd))


def test_native_cli_mode_returns_actual_owner_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def receive(target: str, _make: str, _makefile: str, **_options: object) -> int:
        calls.append(target)
        return 73

    monkeypatch.setattr(locking, "_run_make_transaction", receive)
    assert locking.main(
        ["--label", "commit-transaction", "--make-target", "ship-commit"], require_make_target=True,
    ) == 73
    assert calls == ["ship-commit"]


def test_actual_uv_retains_native_lock_descriptor_without_environment_creation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    uv = shutil.which("uv")
    assert uv is not None
    (fixture.root / "pyproject.toml").write_text(
        '[project]\nname = "gludd-owned-native-commit-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
    )
    process = fixture.start(uv=uv, make_flags=("-j2",))
    stdout, stderr = _drain(process)
    assert process.returncode == 0, (stdout, stderr)
    assert "warning:" not in (stdout + stderr).lower()
    assert not (fixture.root / ".venv").exists()
    assert _git(fixture.root, "show", "HEAD:first.txt").stdout == "first.txt\n"
    assert f"source={ROOT / 'src/general_ludd/git_automation/locking.py'}" in stderr
    assert f"observer={ROOT / 'scripts/stream_command.py'}" in stderr


def test_ambient_interpreter_marker_cannot_skip_native_private_assertion(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    process = fixture.start(
        target="_commit-locked-ship-commit-files",
        state={"GLUDD_COMMIT_TRANSACTION_PYTHON": "/usr/bin/true", "GLUDD_COMMIT_TRANSACTION_FD": "9999999999"},
    )
    stdout, stderr = _drain(process)
    assert process.returncode != 0, (stdout, stderr)
    assert "outcome=refused" in stderr and "first:add" not in fixture.events()
    assert _git(fixture.root, "diff", "--cached", "--name-only").stdout == ""


def test_live_transaction_is_visible_and_protected_by_actual_native_owners(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    process = fixture.start(block=True)
    try:
        _wait_for(fixture.state / "first.collect", process)
        receipt = json.loads(
            (fixture.root / ".gate-logs/observed/commit-transaction/current.json").read_text(),
        )
        observed = inventory_owner._observer_owned_processes_from_output(
            inventory_owner._process_output(), repository_roots=(fixture.root,),
        )
        owner = next(row for row in observed if row["observer_role"] == "owner")
        assert int(owner["pid"]) == receipt["owner_pid"]
        assert "scripts/stream_command.py" in owner["command"]
        assert "--label commit-transaction" in owner["command"]
        child = next(row for row in observed if row["observer_role"] == "child")
        assert int(child["pid"]) == receipt["child_pid"]
        records = cleanup_owner._records(fixture.root)
        protected = watchdog_owner._protected_pids(records, (fixture.root,))
        assert {int(row["pid"]) for row in observed}.issubset(protected)
    finally:
        fixture.release()
        stdout, stderr = _drain(process)
    assert process.returncode == 0, (stdout, stderr)


@pytest.mark.parametrize("arguments", [
    ["--status"], ["--tail", "5"], ["--assert-transaction"],
    ["--make-target", "ship-commit", "--", "true"],
    ["--make-target", "unrelated"],
    ["--make-target", "ship-commit", "--label", "foreign"],
])
def test_internal_stream_dispatch_refuses_mixed_or_unbounded_modes_before_execution(
    arguments: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as result:
        observer.main(["--commit-transaction", "--label", "commit-transaction", *arguments])
    assert result.value.code == 2
    assert not (tmp_path / ".gate-logs").exists()


@pytest.mark.parametrize("target", TARGETS)
def test_same_named_public_and_private_files_cannot_skip_commit_transaction(tmp_path: Path, target: str) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    names = (target, "_commit-locked-" + target)
    payload = b"owned same-name data, never a Make receipt\n"
    with (fixture.root / ".git/info/exclude").open("a") as excludes:
        for name in names:
            (fixture.root / name).write_bytes(payload)
            excludes.write(name + "\n")
    if target != "ship-commit-files":
        _git(fixture.root, "add", "first.txt")
    process = fixture.start(target=target)
    stdout, stderr = _drain(process)
    assert process.returncode == 0, (stdout, stderr)
    assert "first:commit" in fixture.events()
    assert _git(fixture.root, "show", "HEAD:first.txt").stdout == "first.txt\n"
    for name in names:
        assert (fixture.root / name).read_bytes() == payload


@pytest.mark.parametrize("flag,status", [("-q", 1), ("-t", 0), ("-n", 0)])
@pytest.mark.parametrize("prior_observation", [False, True])
def test_native_query_touch_and_dry_run_preserve_source_and_current_pointer(
    tmp_path: Path, flag: str, status: int, prior_observation: bool,
) -> None:
    fixture = _fixture(tmp_path / "repo", tmp_path / "state")
    pointer = fixture.root / ".gate-logs/observed/commit-transaction/current.json"
    if prior_observation:
        previous = fixture.start()
        assert _drain(previous) and previous.returncode == 0
    original_pointer = pointer.read_bytes() if pointer.exists() else None
    original_head = _git(fixture.root, "rev-parse", "HEAD").stdout
    original_index = (fixture.root / ".git/index").read_bytes()
    original_events = fixture.events()
    process = fixture.start(filename="second.txt", make_flags=(flag,))
    stdout, stderr = _drain(process)
    received = compose_makefile(fixture.root / "Makefile")
    macro = re.search(r"define _commit-transaction\n.*?endef", received, re.DOTALL)
    assert process.returncode == status, (
        stdout, stderr, macro.group() if macro else "missing native macro",
        _block(received, "ship-commit-files"),
    )
    guidance = stdout + stderr
    assert f"outcome=planned native_status={status}" in guidance
    assert "source_checkpoint=false" in guidance and "outcome=passed" not in guidance
    assert _git(fixture.root, "rev-parse", "HEAD").stdout == original_head
    assert (fixture.root / ".git/index").read_bytes() == original_index
    assert (pointer.read_bytes() if pointer.exists() else None) == original_pointer
    assert not {"first:add", "first:collect", "first:commit"}.intersection(
        fixture.events()[len(original_events):],
    )
    assert all(not (fixture.root / name).exists() for name in TARGETS)
