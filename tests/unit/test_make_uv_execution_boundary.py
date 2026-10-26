"""Native Make must execute owners and guards while keeping reads lightweight."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.unit.test_make_readonly_version import _search_fixture
from tests.unit.test_observability_guardrails import _recipe

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ReaderMake:
    """Confine real reader recipes, producer evidence and launcher sentinels."""

    root: Path
    environment: dict[str, str] = field(repr=False)
    uv_marker: Path

    def run(
        self, *arguments: str, interpreter: str | None = sys.executable,
        run_id: str = "", tail_lines: int = 2,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "make", "--no-print-directory", *arguments,
            "OBSERVED_LABEL=reader", "OBSERVED_ROOT=state",
            "OBSERVED_STALE_SECS=90", f"OBSERVED_TAIL_LINES={tail_lines}",
            f"RUN_ID={run_id}", f"UV={self.root / 'bin/uv'}",
        ]
        if interpreter is not None:
            command.append(f"PYTHON={interpreter}")
        return subprocess.run(
            command, cwd=self.root, env=dict(self.environment),
            capture_output=True, text=True, check=False, timeout=10,
        )

    def authority(self) -> dict[str, tuple[bytes, int, int]]:
        paths = list((self.root / "state").rglob("*"))
        paths += [self.root / name for name in (".venv/leased-marker", "uv.lock", "unrelated-marker")]
        return {
            str(path.relative_to(self.root)): (
                path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns,
            )
            for path in paths if path.is_file()
        }


def _reader_fixture(tmp_path: Path, *, canonical: bool = False) -> ReaderMake:
    """Use only actual Make inputs and the native producer in a private CWD."""
    environment = dict(os.environ)
    for key in (
        "MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL", "GNUMAKEFLAGS",
        "PYTEST_ARGS", "PYTEST_ADDOPTS", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHON",
    ):
        environment.pop(key, None)
    environment.update(
        UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never", PYTHONDONTWRITEBYTECODE="1",
        GLUDD_READER_CARRIER="unrelated-carrier",
    )
    if canonical:
        _search_fixture(tmp_path)
    else:
        foundation = (ROOT / "make/00-foundation.mk").read_text(encoding="utf-8").splitlines()
        defaults = [line for line in foundation if line.startswith(("PYTHON :=", "UV :="))]
        assert len(defaults) == 2
        rules = "\n".join(f"{target}:\n{_recipe(target)}" for target in ("observed-status", "observed-tail"))
        (tmp_path / "Makefile").write_text(
            "\n".join(defaults) + "\n.PHONY: observed-status observed-tail reader-wrapper\n"
            + rules + "\nreader-wrapper: observed-status\n",
            encoding="utf-8",
        )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts/stream_command.py", scripts / "stream_command.py")
    binary = tmp_path / "bin"
    binary.mkdir()
    marker = tmp_path / "uv-called"
    (binary / "python3").symlink_to(sys.executable)
    uv = binary / "uv"
    uv.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('UV must never read evidence')\nraise SystemExit(79)\n",
        encoding="utf-8",
    )
    uv.chmod(0o700)
    environment["PATH"] = f"{binary}{os.pathsep}{environment['PATH']}"
    (tmp_path / "unrelated-marker").write_bytes(b"unchanged unrelated bytes\n")
    for run_id, code in (("run-one", 0), ("run-two", 7)):
        produced = subprocess.run(
            [
                sys.executable, str(scripts / "stream_command.py"),
                "--root", str(tmp_path / "state"), "--label", "reader",
                "--run-id", run_id, "--quiet", "--retain-runs", "2", "--",
                sys.executable, "-c",
                f"import os; print('first'); print('carrier=' + os.environ['GLUDD_READER_CARRIER']); "
                f"print('{run_id}'); raise SystemExit({code})",
            ],
            cwd=tmp_path, env=environment, text=True, capture_output=True, check=False, timeout=10,
        )
        assert produced.returncode == code, produced.stdout + produced.stderr
    return ReaderMake(tmp_path, environment, marker)


@pytest.fixture
def reader_make(tmp_path: Path) -> ReaderMake:
    return _reader_fixture(tmp_path)


def _assert_reader_result(result: subprocess.CompletedProcess[str], target: str, run_id: str = "run-two") -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    if target == "observed-tail":
        assert result.stdout == f"carrier=unrelated-carrier\n{run_id}\n"
    else:
        payload = json.loads(result.stdout)
        assert payload["run_id"] == run_id
        assert payload["state"] == ("failed" if run_id == "run-two" else "passed")
        assert payload["exit_code"] == (7 if run_id == "run-two" else 0)
        assert payload["effective_state"] == payload["state"]
        assert payload["active"] is False


@pytest.mark.parametrize("reader_target", ("observed-status", "observed-tail"))
@pytest.mark.parametrize("reader_options", ((), ("-e",)))
def test_observed_reader_needs_no_uv_or_project_environment(
    reader_make: ReaderMake, reader_target: str, reader_options: tuple[str, ...],
) -> None:
    """A real read must bypass UV even when no project environment exists."""
    before = reader_make.authority()
    parent = dict(os.environ)
    result = reader_make.run(*reader_options, reader_target, interpreter=f"{sys.executable} -S")
    assert "GLUDD_READER_CARRIER" not in repr(reader_make) and "unrelated-carrier" not in repr(reader_make)
    assert not reader_make.uv_marker.exists(), result.stdout + result.stderr
    _assert_reader_result(result, reader_target)
    assert reader_make.authority() == before and os.environ == parent
    assert reader_make.environment["GLUDD_READER_CARRIER"] == "unrelated-carrier"
    assert not (reader_make.root / ".venv").exists() and not (reader_make.root / "uv.lock").exists()


@pytest.mark.parametrize("reader_target", ("observed-status", "observed-tail"))
@pytest.mark.parametrize("reader_run_id", ("", "current", "run-one"))
def test_observed_reader_preserves_retained_and_leased_state(
    reader_make: ReaderMake, reader_target: str, reader_run_id: str,
) -> None:
    """Existing environment, lock and immutable producer bytes survive reads."""
    leased = reader_make.root / ".venv/leased-marker"
    leased.parent.mkdir()
    leased.write_bytes(b"retain this composed runtime layer\n")
    (reader_make.root / "uv.lock").write_bytes(b"retain existing lock\n")
    before = reader_make.authority()
    result = reader_make.run(reader_target, run_id=reader_run_id)
    _assert_reader_result(result, reader_target, "run-one" if reader_run_id == "run-one" else "run-two")
    assert reader_make.authority() == before and not reader_make.uv_marker.exists()


@pytest.mark.parametrize(
    ("reader_target", "reader_failure"),
    (
        ("observed-status", "missing"), ("observed-status", "json"),
        ("observed-status", "identity"), ("observed-tail", "log"),
        ("observed-tail", "zero"), ("observed-tail", "large"),
        ("observed-status", "stale"), ("observed-status", "orphaned"),
    ),
)
def test_observed_reader_preserves_native_failures_without_mutation(
    reader_make: ReaderMake, reader_target: str, reader_failure: str,
) -> None:
    """Native JSON and reader errors keep their own codes through Make."""
    current = reader_make.root / "state/reader/current.json"
    run_id = ""
    tail_lines = 2
    if reader_failure == "missing":
        current.unlink()
    elif reader_failure == "json":
        current.write_text("{", encoding="utf-8")
    elif reader_failure == "identity":
        run_id = "run-one"
        selected = current.with_name("run-one.json")
        payload = json.loads(selected.read_text(encoding="utf-8"))
        payload["label"] = "foreign"
        selected.write_text(json.dumps(payload), encoding="utf-8")
    elif reader_failure == "log":
        (reader_make.root / "state/reader/run-two.log").unlink()
    elif reader_failure in {"zero", "large"}:
        tail_lines = 0 if reader_failure == "zero" else 1001
    else:
        payload = json.loads(current.read_text(encoding="utf-8"))
        payload.update(
            state="running", owner_pid=os.getpid() if reader_failure == "stale" else 999_999_999,
            updated_at="2000-01-01T00:00:00+00:00",
        )
        current.write_text(json.dumps(payload), encoding="utf-8")
    before = reader_make.authority()
    result = reader_make.run(reader_target, run_id=run_id, tail_lines=tail_lines)
    assert result.returncode == 2
    assert not reader_make.uv_marker.exists() and reader_make.authority() == before
    if reader_failure in {"stale", "orphaned"}:
        assert "Error 3" in result.stderr
        assert json.loads(result.stdout)["effective_state"] == reader_failure
    else:
        assert "Error 2" in result.stderr and "observed-command:" in result.stderr


@pytest.mark.parametrize("reader_options", ((), ("-e",)))
def test_observed_reader_keeps_global_interpreter_precedence(
    reader_make: ReaderMake, reader_options: tuple[str, ...],
) -> None:
    """Only native Make precedence selects an ambient or explicit controller."""
    refusing = reader_make.root / "refusing-python"
    refusing.write_text("#!/bin/sh\nprintf 'selected interpreter unavailable\\n' >&2\nexit 73\n", encoding="utf-8")
    refusing.chmod(0o700)
    reader_make.environment["PYTHON"] = str(refusing)
    before = reader_make.authority()
    ambient = reader_make.run(*reader_options, "observed-status", interpreter=None)
    if reader_options:
        assert ambient.returncode == 2 and "Error 73" in ambient.stderr
    else:
        _assert_reader_result(ambient, "observed-status")
    explicit = reader_make.run(*reader_options, "observed-status")
    _assert_reader_result(explicit, "observed-status")
    assert reader_make.authority() == before and not reader_make.uv_marker.exists()


def test_observed_reader_missing_interpreter_never_bootstraps(reader_make: ReaderMake) -> None:
    before = reader_make.authority()
    result = reader_make.run("observed-status", interpreter=str(reader_make.root / "absent-python"))
    assert result.returncode == 2 and "Error 127" in result.stderr
    assert reader_make.authority() == before and not reader_make.uv_marker.exists()
    assert not (reader_make.root / ".venv").exists()


def test_observed_reader_dependency_and_dry_run_keep_native_scope(reader_make: ReaderMake) -> None:
    before = reader_make.authority()
    result = reader_make.run("reader-wrapper")
    _assert_reader_result(result, "observed-status")
    dry = reader_make.run("-n", "observed-tail", interpreter=str(reader_make.root / "absent-python"))
    assert dry.returncode == 0 and "--tail" in dry.stdout
    assert reader_make.authority() == before and not reader_make.uv_marker.exists()
    assert not (reader_make.root / ".venv").exists()


@pytest.mark.parametrize("reader_options", ((), ("-e",)))
def test_observed_readers_use_canonical_make_startup_without_uv(
    tmp_path: Path, reader_options: tuple[str, ...],
) -> None:
    """Retain one complete entrypoint/include-graph witness per precedence mode."""
    reader = _reader_fixture(tmp_path, canonical=True)
    before = reader.authority()
    for target in ("observed-status", "observed-tail"):
        result = reader.run(*reader_options, target)
        _assert_reader_result(result, target)
    assert not reader.uv_marker.exists() and reader.authority() == before
    assert not (reader.root / ".venv").exists() and not (reader.root / "uv.lock").exists()


def test_observed_reader_passes_carriers_to_the_selected_existing_interpreter(reader_make: ReaderMake) -> None:
    """A trusted recorder executes the native reader without changing its output."""
    capture = reader_make.root / "selected-python.json"
    selected = reader_make.root / "selected-python"
    selected.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
        f"Path({str(capture)!r}).write_text(json.dumps({{"
        "'carrier': os.environ.get('GLUDD_READER_CARRIER'), "
        "'downloads': os.environ.get('UV_PYTHON_DOWNLOADS'), "
        "'executable': sys.executable, 'argv': sys.argv[1:]}))\n"
        f"os.execve({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]], os.environ.copy())\n",
        encoding="utf-8",
    )
    selected.chmod(0o700)
    before = reader_make.authority()
    parent = dict(os.environ)
    result = reader_make.run("observed-tail", interpreter=str(selected))
    _assert_reader_result(result, "observed-tail")
    actual = json.loads(capture.read_text(encoding="utf-8"))
    assert actual["carrier"] == "unrelated-carrier" and actual["downloads"] == "never"
    assert actual["executable"] == sys.executable
    assert actual["argv"] == [
        "scripts/stream_command.py", "--tail", "2", "--root", "state", "--label", "reader",
    ]
    assert reader_make.authority() == before and os.environ == parent
    assert not reader_make.uv_marker.exists()


def test_observed_reader_keeps_native_stdlib_capability_failure(reader_make: ReaderMake) -> None:
    """A confined missing-UTC witness is not an actual legacy CPython claim."""
    incapable = reader_make.root / "missing-utc-python"
    incapable.write_text(
        f"#!{sys.executable}\nimport datetime, runpy, sys\ndel datetime.UTC\n"
        "sys.argv = sys.argv[1:]\nrunpy.run_path(sys.argv[0], run_name='__main__')\n",
        encoding="utf-8",
    )
    incapable.chmod(0o700)
    before = reader_make.authority()
    result = reader_make.run("observed-status", interpreter=str(incapable))
    assert result.returncode == 2 and "Error 1" in result.stderr
    assert "ImportError" in result.stderr and "UTC" in result.stderr
    assert reader_make.authority() == before and not reader_make.uv_marker.exists()
    assert not (reader_make.root / ".venv").exists()


@dataclass(frozen=True)
class NativeMake:
    root: Path
    environment: dict[str, str]
    native_git: str

    def run(self, *arguments: str, without_runtime: bool = False) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        if without_runtime:
            environment.pop("VIRTUAL_ENV", None)
            environment.pop("UV_PROJECT_ENVIRONMENT", None)
        return subprocess.run(
            ["make", "--no-print-directory", *arguments],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def authority(self) -> dict[str, bytes]:
        metadata = self.root / ".git"
        result = {name: (metadata / name).read_bytes() for name in ("HEAD", "index", "config")}
        result.update(
            {
                str(path.relative_to(metadata)): path.read_bytes()
                for path in (metadata / "refs").rglob("*")
                if path.is_file()
            }
        )
        result["pyproject.toml"] = (self.root / "pyproject.toml").read_bytes()
        result["input.txt"] = (self.root / "input.txt").read_bytes()
        return result


@pytest.fixture
def native_make(tmp_path: Path) -> NativeMake:
    git = shutil.which("git")
    assert git is not None and shutil.which("uv") is not None
    names = subprocess.run(
        [git, "rev-parse", "--local-env-vars"],
        cwd=tmp_path,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.split()
    assert names and all(name.startswith("GIT_") and name.isascii() and name.isidentifier() for name in names)
    environment = dict(os.environ)
    for key in (*names, "MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL", "GNUMAKEFLAGS", "PYTEST_ADDOPTS"):
        environment.pop(key, None)
    environment.update(
        {
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GLUDD_PROJECT_ROOT": str(tmp_path),
            "PYTHONPATH": str(tmp_path / "src"),
        }
    )
    if "VIRTUAL_ENV" in environment and "UV_PROJECT_ENVIRONMENT" not in environment:
        environment["UV_PROJECT_ENVIRONMENT"] = environment["VIRTUAL_ENV"]
    fixture = NativeMake(tmp_path, environment, git)
    _search_fixture(tmp_path)
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "native-make-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (tmp_path / "input.txt").write_text("native owned fixture\n", encoding="utf-8")
    application = tmp_path / "src" / "general_ludd"
    application.mkdir(parents=True)
    (application / "__init__.py").write_text("raise AssertionError('application import forbidden')\n", encoding="utf-8")
    for arguments in (
        ("init", "-q", "--initial-branch=fixture-main"),
        ("config", "user.name", "Native Make fixture"),
        ("config", "user.email", "native-make@example.invalid"),
        ("add", "input.txt", "pyproject.toml"),
        ("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "native fixture"),
    ):
        result = subprocess.run(
            [git, *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    return fixture


@pytest.mark.parametrize("state_first", [True, False])
def test_mixed_read_only_and_register_runs_real_owner_exactly_once(native_make: NativeMake, state_first: bool) -> None:
    registry = native_make.root / "active.json"
    goals = ("worktree-state", "workstream-register") if state_first else ("workstream-register", "worktree-state")
    before = native_make.authority()
    parent = dict(os.environ)
    result = native_make.run(
        *goals,
        "BRANCH=agent-owned",
        f"WORKTREE={native_make.root}",
        f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert registry.is_file(), "successful owner execution must persist its actual owned registration"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert list(payload["workstreams"]) == ["agent-owned"]
    assert payload["workstreams"]["agent-owned"]["worktree"] == str(native_make.root)
    assert payload["workstreams"]["agent-owned"]["status"] == "active"
    assert result.stdout.count("registered active workstream: agent-owned") == 1
    assert native_make.authority() == before and os.environ == parent
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()


@pytest.mark.parametrize("state_first", [True, False])
def test_mixed_read_only_preserves_real_owner_failure(native_make: NativeMake, state_first: bool) -> None:
    registry = native_make.root / "refused.json"
    goals = ("worktree-state", "workstream-register") if state_first else ("workstream-register", "worktree-state")
    before = native_make.authority()
    result = native_make.run(
        *goals,
        "BRANCH=invalid..branch",
        f"WORKTREE={native_make.root}",
        f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    assert result.returncode != 0, "a printed owner command is not successful owner execution"
    assert "invalid workstream branch" in result.stdout + result.stderr
    assert not registry.exists() and native_make.authority() == before


def _capture_checkout(fixture: NativeMake) -> Path:
    binary = fixture.root / "bin"
    binary.mkdir()
    event = fixture.root / "checkout-called"
    git = binary / "git"
    git.write_text(
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['checkout', 'fixture-target']:\n"
        f"    Path({str(event)!r}).write_text('called')\n"
        "    print('NATIVE CHECKOUT BODY')\n    raise SystemExit(0)\n"
        f"os.execv({fixture.native_git!r}, [{fixture.native_git!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    git.chmod(0o700)
    fixture.environment["PATH"] = f"{binary}{os.pathsep}{fixture.environment['PATH']}"
    return event


@pytest.mark.parametrize("state_first", [False, True])
@pytest.mark.parametrize("active", [True, False])
def test_native_checkout_executes_gate_guard_before_harmless_body(
    native_make: NativeMake,
    state_first: bool,
    active: bool,
) -> None:
    event = _capture_checkout(native_make)
    lock = native_make.root / "gate.lock"
    if active:
        acquired = subprocess.run(
            [sys.executable, str(ROOT / "scripts/gate_run_lock.py"), "acquire", str(lock), str(os.getpid())],
            cwd=native_make.root,
            env=native_make.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert acquired.returncode == 0, acquired.stderr
    before = native_make.authority()
    lock_before = lock.read_bytes() if lock.exists() else None
    goals = ("worktree-state", "git-checkout") if state_first else ("git-checkout",)
    result = native_make.run(*goals, "MSG=fixture-target", f"GATE_RUN_LOCK={lock}")
    output = result.stdout + result.stderr
    if active:
        assert result.returncode != 0 and "active gate" in output
        assert not event.exists(), "an active gate must block the mutation body, not print its guard"
        assert lock.read_bytes() == lock_before
    else:
        assert result.returncode == 0, output
        assert event.read_text() == "called"
        assert result.stdout.index("gate-run-lock: inactive") < result.stdout.index("NATIVE CHECKOUT BODY")
    assert native_make.authority() == before


@pytest.mark.parametrize("target", ["worktree-state", "search", "cat-file", "uv-cache-path"])
def test_native_lightweight_reads_need_no_uv_or_environment(native_make: NativeMake, target: str) -> None:
    binary = native_make.root / "bin"
    binary.mkdir()
    event = native_make.root / "uv-called"
    uv = binary / "uv"
    uv.write_text(f"#!/bin/sh\nprintf called > '{event}'\nexit 79\n", encoding="utf-8")
    uv.chmod(0o700)
    native_make.environment["PATH"] = f"{binary}{os.pathsep}{native_make.environment['PATH']}"
    arguments = (
        ("PATTERN=native", "SEARCH_PATH=input.txt")
        if target == "search"
        else ("FILE=input.txt",)
        if target == "cat-file"
        else ()
    )
    before = native_make.authority()
    result = native_make.run(target, *arguments, without_runtime=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not event.exists(), "dependency-free read-only recipes must not invoke uv at all"
    assert native_make.authority() == before
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()


def test_mixed_owner_preserves_explicit_uv_failure_instead_of_echo_success(native_make: NativeMake) -> None:
    uv = native_make.root / "unavailable-uv"
    uv.write_text("#!/bin/sh\nprintf 'selected uv unavailable\n' >&2\nexit 73\n", encoding="utf-8")
    uv.chmod(0o700)
    registry = native_make.root / "active.json"
    result = native_make.run(
        "worktree-state",
        "workstream-register",
        f"UV={uv}",
        "BRANCH=agent-owned",
        f"WORKTREE={native_make.root}",
        f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    assert result.returncode != 0 and "selected uv unavailable" in result.stderr
    assert "Error 73" in result.stderr and not registry.exists()


@pytest.mark.parametrize(("target", "expected_no_sync"), [("workstream-register", "0"), ("notify-test", "1")])
def test_real_owner_keeps_global_no_sync_and_selected_toolchain(
    native_make: NativeMake, target: str, expected_no_sync: str,
) -> None:
    """Pure stdlib owners clear only an inapplicable flag; project owners keep it."""
    native_uv = shutil.which("uv", path=native_make.environment["PATH"])
    assert native_uv is not None
    policy = native_make.root / "runtime-policy.json"
    selected = native_make.root / "selected-uv"
    selected.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
        f"Path({str(policy)!r}).write_text(json.dumps({{key: os.environ.get(key) for key in "
        "('UV_NO_SYNC', 'UV_PROJECT_ENVIRONMENT', 'VIRTUAL_ENV', 'UV_PYTHON_DOWNLOADS')}))\n"
        f"os.execv({native_uv!r}, [{native_uv!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    selected.chmod(0o700)
    registry = native_make.root / "active.json"
    result = native_make.run(
        "worktree-state",
        target,
        f"UV={selected}",
        "UV_NO_SYNC=0",
        "BRANCH=agent-owned",
        f"WORKTREE={native_make.root}",
        f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    if target == "workstream-register":
        assert result.returncode == 0 and registry.is_file(), result.stdout + result.stderr
        assert "warning:" not in result.stderr.lower(), result.stderr
    else:
        assert result.returncode != 0 and "application import forbidden" in result.stderr
        assert not registry.exists()
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()
    observed = json.loads(policy.read_text(encoding="utf-8"))
    assert observed == {
        "UV_NO_SYNC": expected_no_sync,
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_PROJECT_ENVIRONMENT": native_make.environment["UV_PROJECT_ENVIRONMENT"],
        "VIRTUAL_ENV": native_make.environment["VIRTUAL_ENV"],
    }


def test_checkout_first_multi_goal_refusal_still_precedes_all_bodies(native_make: NativeMake) -> None:
    event = _capture_checkout(native_make)
    before = native_make.authority()
    result = native_make.run("git-checkout", "worktree-state", "MSG=fixture-target")
    assert result.returncode != 0 and "Quote multi-word variable values for git-checkout" in result.stderr
    assert not event.exists() and native_make.authority() == before


def test_no_project_workstream_owners_run_without_uv_policy_warnings(native_make: NativeMake) -> None:
    """Actual registration and removal must be warning-free without project sync."""
    registry = native_make.root / "warning-free-active.json"
    before = native_make.authority()
    parent = dict(os.environ)
    head = before["refs/heads/fixture-main"].decode("ascii").strip()
    for target, active in (("workstream-register", True), ("workstream-unregister", False)):
        result = native_make.run(
            target,
            "BRANCH=fixture-main",
            f"WORKTREE={native_make.root}",
            f"WORKSTREAM_EXPECTED_HEAD={head}",
            f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "warning:" not in result.stderr.lower(), result.stderr
        payload = json.loads(registry.read_text(encoding="utf-8"))
        row = payload["workstreams"]["fixture-main"]
        assert (row["status"] == "active") is active
        if not active:
            assert row["status"] == "released" and row["head"] == head
            assert json.loads(result.stdout)["native_ownership_verified"] == 1
        assert native_make.authority() == before and os.environ == parent
        assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()


@pytest.mark.parametrize("make_options", ((), ("-e",)))
@pytest.mark.parametrize("input_origin", ("command-line", "environment"))
@pytest.mark.parametrize(("target", "variable"), (
    ("workstream-unregister", "WORKSTREAM_EXPECTED_HEAD"),
    ("agent-cleanup", "AGENT_CLEANUP_EXPECTED_HEAD"),
    ("agent-cleanup", "AGENT_CLEANUP_PURPOSE"),
))
def test_selected_owner_context_stays_literal_before_native_startup(
    native_make: NativeMake, monkeypatch: pytest.MonkeyPatch,
    make_options: tuple[str, ...], input_origin: str, target: str, variable: str,
) -> None:
    """The same native Make expression runs unguarded and is refused by the owner."""
    marker = native_make.root / "owned witness marker"
    payload = f"$(shell touch '{marker}')"
    values = {
        "BRANCH": "fixture-main", "WORKTREE": str(native_make.root),
        "WORKSTREAM_EXPECTED_HEAD": "f" * 40, "AGENT_CLEANUP_EXPECTED_HEAD": "f" * 40,
        "AGENT_CLEANUP_PURPOSE": "self-improve-evaluation", "AGENT_CLEANUP_VALIDATE_ONLY": "1",
        "ACTIVE_WORKSTREAM_REGISTRY": str(native_make.root / "no-release.json"),
    }
    for key in values:
        monkeypatch.delitem(native_make.environment, key, raising=False)
    if input_origin == "environment":
        monkeypatch.setitem(native_make.environment, variable, payload)
        values.pop(variable)
        data: tuple[str, ...] = ()
    else:
        data = (f"{variable}={payload}",)
        values.pop(variable)
    witness = native_make.root / "unguarded-selected-owner.mk"
    witness.write_text(
        f".PHONY: witness\nwitness:\n\t@printf '%s' '$({variable})'\n", encoding="utf-8",
    )
    unguarded = native_make.run(*make_options, "-f", str(witness), "witness", *data)
    assert unguarded.returncode == 0 and marker.exists(), unguarded.stdout + unguarded.stderr
    marker.unlink()
    before = native_make.authority()
    parent = dict(os.environ)
    result = native_make.run(*make_options, target, *data, *(f"{key}={value}" for key, value in values.items()))
    assert result.returncode != 0 and "must be literal data" in result.stderr
    assert not marker.exists() and not (native_make.root / "no-release.json").exists()
    assert native_make.authority() == before and os.environ == parent
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()


@pytest.mark.parametrize(
    "multi_goal_case",
    [
        pytest.param(("lint-markdown", "MARKDOWN_FILES=one file.md", "worktree-state"), id="quoted-markdown"),
        pytest.param(("lint-files", "FILES=one file.py", "worktree-state"), id="quoted-python"),
        pytest.param(("search", "PATTERN=two words", "worktree-state"), id="quoted-search"),
        pytest.param(("git-checkout", "MSG=fixture-target", "worktree-state"), id="checkout"),
        pytest.param(("lint-markdown", "MARKDOWN_FILES=one.md", "two.md"), id="split-value"),
    ],
)
def test_guarded_multi_goal_refusal_teaches_both_input_repairs_before_any_body(
    native_make: NativeMake, multi_goal_case: tuple[str, str, str],
) -> None:
    """A complete assignment is one argv element; quoted values can still have extra goals."""
    checkout = _capture_checkout(native_make)
    uv_event = native_make.root / "uv-called"
    uv = native_make.root / "bin" / "uv"
    uv.write_text(f"#!/bin/sh\nprintf called > '{uv_event}'\nexit 79\n", encoding="utf-8")
    uv.chmod(0o700)
    first, assignment, extra = multi_goal_case
    before = native_make.authority()
    parent = dict(os.environ)
    result = native_make.run(first, extra, assignment, f"UV={uv}")
    assert result.returncode == 2
    assert "Quote multi-word variable values for " + first in result.stderr
    assert "stray make goals: " + extra in result.stderr
    assert not checkout.exists() and not uv_event.exists()
    assert native_make.authority() == before and os.environ == parent
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()
    prompts = [line for line in result.stderr.splitlines() if "AGENT_NEXT " in line]
    assert len(prompts) == 1, result.stdout + result.stderr
    prompt = prompts[0].split("AGENT_NEXT ", 1)[1]
    assert "target=" + first + " " in prompt
    assert "outcome=refused native_status=2 " in prompt
    assert "gate_evidence=false release_ready=false " in prompt
    assert "next_action=quote-values-or-split-goals" in prompt
    assert "quote the whole assignment" in prompt
    assert "already quoted" in prompt and "separate Make invocations" in prompt
    assert "one guarded target" in prompt


def test_single_guarded_target_keeps_quoted_value_and_path_executable(native_make: NativeMake) -> None:
    owned = native_make.root / "two word inputs.txt"
    owned.write_text("two word needle\n", encoding="utf-8")
    before = native_make.authority()
    parent = dict(os.environ)
    result = native_make.run("search", "PATTERN=two word", f"SEARCH_PATH={owned.name}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "1:two word needle"
    assert result.stderr == "" and "AGENT_NEXT " not in result.stdout
    assert owned.read_text(encoding="utf-8") == "two word needle\n"
    assert native_make.authority() == before and os.environ == parent
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()
