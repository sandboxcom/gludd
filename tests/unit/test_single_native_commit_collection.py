"""Execute native commit recipes with harmless Git/UV capture boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

import pytest
from scripts.makefile_layout import compose_makefile


class CaptureEvent(TypedDict):
    kind: str
    args: list[str]
    raw_args: list[str]
    carrier_is_owned: bool
    ansible_tmp: str
    tmp_exists: bool


@dataclass(frozen=True)
class _PrivateEnvironment:
    values: dict[str, str] = field(repr=False)


ROOT = Path(os.environ.get("GLUDD_SINGLE_COLLECTION_SOURCE_ROOT", Path.cwd()))
SOURCE = ROOT / "make"
FILES = "src/feature.py tests/unit/test_feature.py"
GUARDS = (
    "_commit-lock-acquire",
    "_commit-lint-guard",
    "_commit-docstring-guard",
    "_pre-commit-stage-guard",
    "_stash-leak-guard",
    "_push-parameter-audit",
    "_pre-commit-stash-audit",
    "_edit-commit-atomicity-guard",
    "check-file-line-limits",
    "check-duplicate-code",
)
CAPTURE = r'''import json
import os
import sys
from pathlib import Path

raw_args = sys.argv[1:]
args = list(raw_args)
name = Path(sys.argv[0]).name
kind = "git" if name == "git" else "uv"
if args and args[0] == "guard":
    kind = "guard"
elif "scripts/stream_command.py" in args:
    kind = "tail" if "--tail" in args else "observe"
if kind == "uv" and args[:2] == ["run", "--active"]:
    args = ["run", *args[2:]]
if kind == "uv" and args[:2] == ["run", "python"]:
    kind = "bootstrap"
if kind == "git" and os.environ.get("CAPTURE_GUARD_CHILD") == "1" and args[:1] != ["--no-optional-locks"]:
    kind = "guard-git"
temp = os.environ.get("ANSIBLE_LOCAL_TEMP", "")
event = {"kind": kind, "args": args, "raw_args": raw_args, "ansible_tmp": temp,
         "tmp_exists": bool(temp and Path(temp).is_dir()),
         "carrier_is_owned": os.environ.get("GLUDD_SINGLE_COLLECTION_TEST_CARRIER") == "owned-carrier"}
with Path(os.environ["CAPTURE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(event) + "\n")
if kind == "bootstrap":
    assert raw_args[:3] == ["run", "--active", "python"]
    child_environment = dict(os.environ)
    child_environment["CAPTURE_GUARD_CHILD"] = "1"
    os.execve(sys.executable, [sys.executable, *args[2:]], child_environment)
if kind == "guard":
    raise SystemExit(1 if args[1] == os.environ.get("FAIL_GUARD") else 0)
if kind == "observe":
    raise SystemExit(int(os.environ.get("COLLECTION_RC", "0")))
if kind == "tail":
    print("123537 tests collected in 56.00s (capture fixture)")
    raise SystemExit(int(os.environ.get("TAIL_RC", "0")))
if kind == "uv":
    assert args[:3] == ["run", "pre-commit", "run"]
    hook = args[3]
    raise SystemExit(1 if hook == os.environ.get("FAIL_GUARD") else 0)
if kind == "guard-git":
    if args == ["write-tree"]:
        print("guard-context-tree")
    raise SystemExit(0)
if args == ["rev-parse", "--local-env-vars"]:
    print("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", sep=chr(10))
elif args == ["diff", "--cached", "--name-only"]:
    print(os.environ.get("STAGED_FILES", "src/feature.py\ntests/unit/test_feature.py"))
elif args == ["--no-optional-locks", "diff", "--cached", "--name-only", "-z", "--"]:
    names = os.environ.get("STAGED_FILES", "src/feature.py\ntests/unit/test_feature.py")
    for filename in names.splitlines():
        sys.stdout.buffer.write(os.fsencode(filename) + bytes((0,)))
elif args == ["--no-optional-locks", "diff", "--cached", "--name-only", "--diff-filter=U", "-z", "--"]:
    pass
elif args == ["write-tree"]:
    counter = Path(os.environ["CAPTURE_LOG"] + ".trees")
    count = int(counter.read_text() if counter.exists() else "0") + 1
    counter.write_text(str(count))
    failure = os.environ.get("TREE_FAILURE")
    if failure == "both" or failure == ("first" if count == 1 else "second"):
        print("fixture git write-tree refused", file=sys.stderr)
        raise SystemExit(73)
    print("changed-tree" if count > 1 and os.environ.get("INDEX_DRIFT") == "1" else "staged-tree")
elif args == ["diff", "--cached", "--quiet"]:
    raise SystemExit(1)
'''


def _block(text: str, target: str) -> str:
    match = re.search(rf"^{re.escape(target)}:[^\n]*\n(?:\t[^\n]*\n)*", text, re.MULTILINE)
    assert match, f"missing native Make target: {target}"
    return match.group()


def _source() -> str:
    tests = (SOURCE / "10-observability-and-tests.mk").read_text(encoding="utf-8")
    commits = (SOURCE / "40-cross-version-and-worktrees.mk").read_text(encoding="utf-8")
    macro = re.search(r"^define _collection-admission\n.*?^endef\n", tests, re.MULTILINE | re.DOTALL)
    blocks = [macro.group() if macro else ""]
    blocks.extend(_block(tests, target) for target in ("test-count", "collect-check"))
    blocks.extend(
        _block(commits, target)
        for target in (
            "_pre-commit-secrets-guard", "_pre-commit-content-guard",
            "_commit-locked-ship-commit", "_commit-locked-ship-commit-files",
        )
    )
    # Compose the real include graph, then retain its declarative context without
    # executing unrelated recipes or parse-time metadata probes in this fixture.
    native = compose_makefile(ROOT / "Makefile")
    declarations: list[str] = []
    lines = iter(native.splitlines(keepends=True))
    for line in lines:
        if line.startswith(".PHONY:"):
            declaration = line
            while declaration.endswith("\\\n"):
                declaration += next(lines)
            declarations.append(declaration)
    defaults = re.findall(
        r"(?m)^(?:FILE_LINE_LIMIT_POLICY|DUPLICATE_CODE_(?:CONFIG|ENGINE|SOURCE|BASE_REF|"
        r"CURRENT_REF|VALIDATE_ONLY)) \?=[^\n]*\n",
        native,
    )
    providers = re.findall(
        r"(?m)^(?:test-count|check-duplicate-code): override (?:UV|MAKEFLAGS) [^\n]*\n",
        native,
    )
    assert declarations and len(defaults) == 7 and len(providers) == 4
    return "\n".join([*declarations, *defaults, *providers, *blocks])


def _run(
    tmp_path: Path,
    target: str = "ship-commit-files",
    *,
    text: str | None = None,
    state: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[CaptureEvent]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    capture = tmp_path / "capture.py"
    capture.write_text(CAPTURE, encoding="utf-8")
    git = tmp_path / "git"
    git.write_text(f"#!{sys.executable}\n{CAPTURE}", encoding="utf-8")
    git.chmod(0o700)
    helper = f"{shlex.quote(sys.executable)} {shlex.quote(str(capture))}"
    guard_blocks = "\n".join(f"{name}:\n\t@{helper} guard {name}\n" for name in GUARDS)
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        f"UV := {helper}\nPYTHON := {shlex.quote(sys.executable)}\n"
        "PUSH := 0\nFILES := " + FILES + "\n"
        "git-add:\n\t@git add $(FILES)\n" + guard_blocks + "\n" + (text or _source()),
        encoding="utf-8",
    )
    private_environment = _PrivateEnvironment({
        key: value for key, value in os.environ.items()
        if key not in {
            "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS", "CAPTURE_GUARD_CHILD",
        }
    })
    env = private_environment.values
    log = tmp_path / "capture.jsonl"
    env.update({"PATH": f"{tmp_path}{os.pathsep}{env['PATH']}", "CAPTURE_LOG": str(log)})
    env.update(state or {})
    env["PRE_COMMIT_HOME"] = str(tmp_path / "pre-commit-home")
    # The native mutex is measured by test_make_commit_transaction. This existing
    # capture seam still owns the unchanged guard/body/collection assertions.
    selected_target = "_commit-locked-" + target if target in {"ship-commit", "ship-commit-files"} else target
    result = subprocess.run(
        [
            "make", "--no-print-directory", selected_target, "MSG=collection admission",
            "UV=" + helper, "PYTHON=" + shlex.quote(sys.executable),
            f"OBSERVED_ROOT={tmp_path / 'observed'}", "OBSERVED_HEARTBEAT_SECS=13",
            "OBSERVED_QUIET_SECS=901", "OBSERVED_MAX_SECS=3601",
            "OBSERVED_RETAIN_RUNS=7", "OBSERVED_TAIL_LINES=80",
        ],
        cwd=tmp_path, env=env, check=False, capture_output=True, text=True, timeout=20,
    )
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return result, events


def _observes(events: list[CaptureEvent]) -> list[CaptureEvent]:
    return [event for event in events if event["kind"] == "observe"]


def _commits(events: list[CaptureEvent]) -> list[CaptureEvent]:
    return [event for event in events if event["kind"] == "git" and event["args"][0] == "commit"]


def _collection_contract(event: CaptureEvent, tmp_path: Path, label: str) -> None:
    args = event["args"]
    assert isinstance(args, list)
    assert args[args.index("--label") + 1] == label
    for name, expected in (
        ("--root", str(tmp_path / "observed")), ("--heartbeat-secs", "13"),
        ("--quiet-secs", "901"), ("--max-secs", "3601"), ("--retain-runs", "7"),
    ):
        assert args[args.index(name) + 1] == expected
    assert "--quiet" in args and "--pytest-trace" in args
    lock = args.index("scripts/collection_lock.py")
    assert args[lock + 1] == "--run"
    pytest_at = args.index("pytest")
    assert args[pytest_at + 1:] == [
        "tests/", "--co", "-q", "-o", "verbosity_test_cases=-2", "-p", "scripts.xdist_trace_plugin",
    ]
    if label == "test-count":
        assert args.count("--no-sync") == 3
    temp = Path(str(event["ansible_tmp"]))
    assert temp.parent == tmp_path / "observed"
    assert event["tmp_exists"] is True
    assert not temp.exists(), "Ansible temp directory must be removed at exit"


def _success_contract(
    result: subprocess.CompletedProcess[str], events: list[CaptureEvent], tmp_path: Path,
) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    observes = _observes(events)
    assert len(observes) == 1
    _collection_contract(observes[0], tmp_path, "test-count")
    assert "123537 tests collected" in result.stdout
    assert "Collection OK" in result.stdout
    assert len(_commits(events)) == 1
    guards = {event["args"][1] for event in events if event["kind"] == "guard"}
    assert guards == set(GUARDS)
    hooks = [event["args"][3] for event in events if event["kind"] == "uv"]
    assert hooks == ["trailing-whitespace", "end-of-file-fixer", "detect-secrets-readonly"]
    assert [event["args"] for event in events if event["kind"] == "uv"] == [
        ["run", "pre-commit", "run", hook, "--files", *FILES.split()] for hook in hooks
    ]
    assert [event["raw_args"] for event in events if event["kind"] == "uv"] == [
        ["run", "--active", "pre-commit", "run", hook, "--files", *FILES.split()] for hook in hooks
    ]
    bootstraps = [event for event in events if event["kind"] == "bootstrap"]
    assert len(bootstraps) == 1
    assert bootstraps[0]["raw_args"][:5] == ["run", "--active", "python", "-P", "-c"]
    trees = [
        index for index, event in enumerate(events)
        if event["kind"] == "git" and event["args"] == ["write-tree"]
    ]
    assert len(trees) == 2
    context_trees = [
        index for index, event in enumerate(events)
        if event["kind"] == "guard-git" and event["args"] == ["write-tree"]
    ]
    assert context_trees and max(context_trees) < trees[0]
    collected = events.index(observes[0])
    committed = events.index(_commits(events)[0])
    assert trees[0] < collected < trees[1] < committed
    assert "Committed locally" in result.stdout


def test_ship_commit_owns_one_fresh_native_count_and_all_admission_guards(tmp_path: Path) -> None:
    result, events = _run(tmp_path)
    _success_contract(result, events, tmp_path)


@pytest.mark.parametrize("target", ["test-count", "collect-check"])
def test_collection_console_uses_native_file_counts_without_global_quiet_override(
    tmp_path: Path, target: str,
) -> None:
    result, events = _run(tmp_path, target=target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_observes(events)) == 1
    args = _observes(events)[0]["args"]
    pytest_args = args[args.index("pytest") + 1:]
    assert pytest_args == [
        "tests/", "--co", "-q", "-o", "verbosity_test_cases=-2", "-p", "scripts.xdist_trace_plugin",
    ]
    assert "--no-summary" not in pytest_args
    assert "-qq" not in pytest_args and "-qqq" not in pytest_args
    assert "Collection OK" in result.stdout
    if target == "test-count":
        assert "123537 tests collected" in result.stdout


@pytest.mark.parametrize("condition", ["success", "import-error", "warning-error", "missing-file"])
def test_native_pytest_compact_collection_preserves_inventory_and_failure_diagnostics(
    tmp_path: Path, condition: str,
) -> None:
    project = tmp_path / "collection-fixture"
    tests = project / "tests"
    tests.mkdir(parents=True)
    snapshot = project / "snapshot.json"
    (project / "pytest.ini").write_text(
        '[pytest]\naddopts = -v --tb=short -m "not excluded"\nmarkers =\n    excluded: fixture selection\n',
        encoding="utf-8",
    )
    (project / "conftest.py").write_text(
        "import json\nfrom pathlib import Path\nimport pytest\n"
        f"SNAPSHOT = Path({str(snapshot)!r})\n"
        "@pytest.fixture\ndef fixture_marker():\n    raise AssertionError('collection executed fixture')\n"
        "def pytest_collection_finish(session):\n"
        "    rows = [(item.nodeid, sorted(mark.name for mark in item.iter_markers()),\n"
        "             sorted(item.fixturenames), item.callspec.id if hasattr(item, 'callspec') else None)\n"
        "            for item in session.items]\n"
        "    SNAPSHOT.write_text(json.dumps(rows), encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tests / "test_inventory.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('index', range(240), ids=lambda value: f'case-{value}')\n"
        "def test_native_inventory(index, fixture_marker):\n"
        "    raise AssertionError('collection executed test')\n"
        "@pytest.mark.excluded\ndef test_excluded():\n    raise AssertionError('excluded test executed')\n",
        encoding="utf-8",
    )
    if condition in {"import-error", "warning-error"}:
        body = (
            "raise RuntimeError('native collection import refusal')\n" if condition == "import-error"
            else "import warnings\nwarnings.warn('native collection warning refusal', UserWarning)\n"
        )
        (tests / "test_refusal.py").write_text(body, encoding="utf-8")
    selections = ["tests/missing.py"] if condition == "missing-file" else ["tests/"]
    snapshots: list[object] = []
    results: list[subprocess.CompletedProcess[str]] = []
    traces: list[list[dict[str, object]]] = []
    for compact in (False, True):
        snapshot.unlink(missing_ok=True)
        trace = project / f"trace-{compact}.jsonl"
        env = dict(os.environ)
        env.pop("PYTEST_ADDOPTS", None)
        env.update({
            "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "GLUDD_XDIST_TRACE_LOG": str(trace), "GLUDD_XDIST_TRACE_RUN_ID": f"collection-{compact}",
            "GLUDD_XDIST_TRACE_TRUNCATE": "1",
        })
        command = [sys.executable, "-m", "pytest", *selections, "--co", "-q", "-W", "error"]
        if compact:
            command.extend(["-o", "verbosity_test_cases=-2"])
        command.extend(["-p", "scripts.xdist_trace_plugin"])
        results.append(subprocess.run(
            command, cwd=project, env=env, capture_output=True, text=True, check=False, timeout=20,
        ))
        snapshots.append(json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.exists() else None)
        traces.append([json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else [])
    original, compact_result = results
    assert snapshots[0] == snapshots[1], "exact node IDs, marks, fixtures and parameter IDs must match"
    assert original.returncode == compact_result.returncode
    for events in traces:
        finishes = [event for event in events if event["event"] == "RUN_FINISH"]
        assert len(finishes) == 1 and finishes[0]["exitstatus"] == original.returncode
        if condition != "missing-file":
            collected = [event for event in events if event["event"] == "COLLECTION_FINISH"]
            assert len(collected) == 1 and collected[0]["collected"] == 240
    if condition == "success":
        assert original.returncode == 0
        assert isinstance(snapshots[0], list) and len(snapshots[0]) == 240
        assert len({row[0] for row in snapshots[0]}) == 240
        assert "tests/test_inventory.py: 240" in compact_result.stdout
        for result in results:
            assert "240/241 tests collected (1 deselected)" in result.stdout
        assert len(compact_result.stdout.encode()) < len(original.stdout.encode()) / 3
    else:
        diagnostic = {
            "import-error": "RuntimeError: native collection import refusal",
            "warning-error": "UserWarning: native collection warning refusal",
            "missing-file": "file or directory not found",
        }[condition]
        assert original.returncode == (4 if condition == "missing-file" else 2)
        for result in results:
            assert diagnostic in result.stdout + result.stderr
            assert "no tests collected" in result.stdout if condition == "missing-file" else "1 error" in result.stdout


@pytest.mark.parametrize("rc", [2, 3, 5, 124])
def test_collection_failure_refuses_commit_and_preserves_bounded_diagnostics(tmp_path: Path, rc: int) -> None:
    result, events = _run(tmp_path, state={"COLLECTION_RC": str(rc)})
    assert result.returncode != 0
    assert not _commits(events)
    assert len(_observes(events)) == 1
    _collection_contract(_observes(events)[0], tmp_path, "test-count")
    assert f"COLLECTION ERRORS DETECTED (rc={rc}" in result.stdout
    tails = [event["args"] for event in events if event["kind"] == "tail"]
    assert len(tails) == 1 and tails[0][tails[0].index("--tail") + 1] == "80"
    assert sum(event["kind"] == "git" and event["args"] == ["write-tree"] for event in events) == 1


@pytest.mark.parametrize("failure", ["first", "second", "both"])
def test_failed_native_tree_capture_refuses_before_unnecessary_work(tmp_path: Path, failure: str) -> None:
    result, events = _run(tmp_path, state={"TREE_FAILURE": failure})
    assert result.returncode != 0
    assert "fixture git write-tree refused" in result.stderr
    assert "Error 73" in result.stderr
    assert not _commits(events)
    assert len(_observes(events)) == (1 if failure == "second" else 0)
    assert "staged index changed" not in result.stderr
    assert sum(
        event["kind"] == "git" and event["args"] == ["write-tree"] for event in events
    ) == (2 if failure == "second" else 1)


def test_success_tail_failure_also_refuses_commit(tmp_path: Path) -> None:
    result, events = _run(tmp_path, state={"TAIL_RC": "2"})
    assert result.returncode != 0
    assert not _commits(events)


def test_collection_failure_status_survives_a_diagnostic_tail_failure(tmp_path: Path) -> None:
    result, events = _run(tmp_path, target="test-count", state={"COLLECTION_RC": "3", "TAIL_RC": "2"})
    assert result.returncode != 0
    assert "Error 3" in result.stderr
    assert not _commits(events)


@pytest.mark.parametrize("guard", [*GUARDS, "trailing-whitespace", "end-of-file-fixer", "detect-secrets-readonly"])
def test_every_existing_content_secret_and_commit_guard_still_refuses_failure(tmp_path: Path, guard: str) -> None:
    result, events = _run(tmp_path, state={"FAIL_GUARD": guard})
    assert result.returncode != 0
    assert not _observes(events) and not _commits(events)
    if guard in GUARDS:
        assert any(event["kind"] == "guard" and event["args"] == ["guard", guard] for event in events)
    else:
        assert any(
            event["kind"] == "uv"
            and event["raw_args"] == ["run", "--active", "pre-commit", "run", guard, "--files", *FILES.split()]
            for event in events
        )


def test_staged_scope_drift_refuses_collection_and_commit(tmp_path: Path) -> None:
    result, events = _run(tmp_path, state={"STAGED_FILES": "src/unrequested.py"})
    assert result.returncode != 0
    assert "staged files differ from requested commit scope" in result.stderr
    assert not _observes(events) and not _commits(events)


def test_staged_tree_drift_after_collection_refuses_mixed_commit(tmp_path: Path) -> None:
    result, events = _run(tmp_path, state={"INDEX_DRIFT": "1"})
    assert result.returncode != 0
    assert "staged index changed during preflight" in result.stderr
    assert len(_observes(events)) == 1 and not _commits(events)


@pytest.mark.parametrize("target", ["test-count", "collect-check"])
def test_public_collection_entry_points_keep_native_observation_and_isolation(tmp_path: Path, target: str) -> None:
    result, events = _run(tmp_path, target=target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_observes(events)) == 1
    _collection_contract(_observes(events)[0], tmp_path, target)
    if target == "test-count":
        assert "123537 tests collected" in result.stdout
    else:
        assert "Collection OK" in result.stdout


def test_each_commit_invocation_recollects_even_after_prior_success(tmp_path: Path) -> None:
    first, first_events = _run(tmp_path / "first")
    _success_contract(first, first_events, tmp_path / "first")
    second, second_events = _run(tmp_path / "second", state={"COLLECTION_RC": "2"})
    assert second.returncode != 0
    assert len(_observes(second_events)) == 1 and not _commits(second_events)


@pytest.mark.parametrize("old,new", [
    ("scripts/collection_lock.py --run", "scripts/collection_lock.py --resource bypass"),
    ("pytest tests/ --co", "pytest tests/unit/ --co"),
    ("--heartbeat-secs", "--missing-heartbeat-secs"),
    ("_commit-locked-ship-commit: _commit-lock-acquire _pre-commit-content-guard",
     "_commit-locked-ship-commit: _commit-lock-acquire"),
])
def test_capture_contract_detects_owner_and_guard_mutations(tmp_path: Path, old: str, new: str) -> None:
    text = _source()
    assert old in text, f"mutation anchor disappeared: {old}"
    result, events = _run(tmp_path, text=text.replace(old, new))
    with pytest.raises((AssertionError, ValueError)):
        _success_contract(result, events, tmp_path)

def _environment_fingerprint() -> bytes:
    return hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True).encode()).digest()


@pytest.mark.parametrize("guard_fixture_early_environment", [False, True])
def test_current_guard_bootstrap_keeps_native_context_and_recursive_carriers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard_fixture_early_environment: bool,
) -> None:
    """Exercise current native argv, its real context and a private Store."""
    parent_before = _environment_fingerprint()
    with monkeypatch.context() as owned:
        owned.setenv("MAKEOVERRIDES", "UV=/usr/bin/false")
        owned.setenv("GNUMAKEFLAGS", "--warn-undefined-variables")
        owned.setenv("UV", "/usr/bin/false")
        owned.setenv("GLUDD_SINGLE_COLLECTION_TEST_CARRIER", "owned-carrier")
        controlled_before = _environment_fingerprint()
        result, events = _run(
            tmp_path, state={"MAKEFLAGS": "e" if guard_fixture_early_environment else ""},
        )
        _success_contract(result, events, tmp_path)
        assert (tmp_path / "pre-commit-home").is_dir()
        measured = [event for event in events if event["kind"] in {"bootstrap", "uv", "guard-git"}]
        assert measured and all(event["carrier_is_owned"] for event in measured)
        controlled_after = _environment_fingerprint()
        assert controlled_after == controlled_before
    parent_after = _environment_fingerprint()
    assert parent_after == parent_before


def test_current_guard_fixture_keeps_native_phony_namesake_behavior(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for target in (
        "git-add", "test-count", "_pre-commit-secrets-guard",
        "check-file-line-limits", "check-duplicate-code",
    ):
        (tmp_path / target).write_text("owned target namesake\n", encoding="utf-8")
    result, events = _run(tmp_path)
    _success_contract(result, events, tmp_path)


@pytest.mark.parametrize("guard_fixture_early_environment", [False, True])
@pytest.mark.parametrize("guard_fixture_shadow_module", ["pre_commit", "subprocess"])
def test_current_guard_fixture_executes_active_safe_path_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    guard_fixture_early_environment: bool, guard_fixture_shadow_module: str,
) -> None:
    """The same guard must activate the CWD shadow only when its -P is removed."""
    parent_before = _environment_fingerprint()
    carrier = tmp_path / "explicit python carrier"
    carrier.mkdir()
    startup = tmp_path / "owned-python-startups.jsonl"
    (carrier / "sitecustomize.py").write_text(
        "import json, os, sys\n"
        f"with open({str(startup)!r}, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'argv': sys.argv, 'cwd': os.getcwd(),\n"
        "        'pythonpath': os.environ.get('PYTHONPATH'),\n"
        "        'carrier': os.environ.get('GLUDD_SINGLE_COLLECTION_TEST_CARRIER'),\n"
        "        'safe_path': sys.flags.safe_path}) + '\\n')\n",
        encoding="utf-8",
    )
    native_source = _source()
    assert native_source.count("python -P -c") == 1
    project = tmp_path / "same-native-entrypoint"
    project.mkdir()
    entered = project / "owned-shadow-entered"
    shadow = project / f"{guard_fixture_shadow_module}.py"
    shadow.write_text(
        "from pathlib import Path\n"
        f"Path({str(entered)!r}).write_text('owned shadow executed', encoding='utf-8')\n"
        "raise SystemExit(79)\n",
        encoding="utf-8",
    )
    shadow_bytes = shadow.read_bytes()
    with monkeypatch.context() as owned:
        owned.setenv("PYTHONPATH", str(carrier))
        owned.setenv("GLUDD_SINGLE_COLLECTION_TEST_CARRIER", "owned-carrier")
        if os.environ.get("PYTHONSAFEPATH"):
            owned.delenv("PYTHONSAFEPATH")
        controlled_before = _environment_fingerprint()
        for guarded in (False, True):
            selected = native_source if guarded else native_source.replace("python -P -c", "python -c")
            result, events = _run(
                project, text=selected,
                state={"MAKEFLAGS": "e" if guard_fixture_early_environment else ""},
            )
            rows = [json.loads(line) for line in startup.read_text(encoding="utf-8").splitlines()]
            bootstraps = [
                row for row in rows if row["argv"][0] == "-c" and row["cwd"] == str(project)
            ]
            assert len(bootstraps) == 1
            assert bootstraps[0]["safe_path"] is guarded
            assert bootstraps[0]["pythonpath"] == str(carrier)
            assert bootstraps[0]["carrier"] == "owned-carrier"
            assert shadow.read_bytes() == shadow_bytes
            if guarded:
                _success_contract(result, events, project)
                assert not entered.exists()
                assert (project / "pre-commit-home").is_dir()
            else:
                assert result.returncode != 0
                assert "Error 79" in result.stderr
                assert entered.read_text(encoding="utf-8") == "owned shadow executed"
                assert not _observes(events) and not _commits(events)
                entered.unlink()
                (project / "capture.jsonl").rename(project / "unguarded-capture.jsonl")
                startup.rename(tmp_path / "unguarded-python-startups.jsonl")
        controlled_after = _environment_fingerprint()
        assert controlled_after == controlled_before
    parent_after = _environment_fingerprint()
    assert parent_after == parent_before
