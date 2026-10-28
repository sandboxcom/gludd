"""Guard against dispatched subagents that waste floor slots with sleep-in-poll-loops.

Per AGENTS.md "CI-Poll Subagents Are Forbidden" (2026-07-08) and
"Background Operations NEVER Block Dispatch" (2026-07-06): a dispatched
subagent that loops with sleep (poll CI until terminal, poll gate-status
every N seconds, etc.) holds a subagent slot and the orchestrator's
attention for 30-40 minutes while producing zero value.

This test scans scripts/ for .py and .sh files that contain both ``sleep``
and a loop construct (``while`` / ``for``).  A file that matches and is
NOT in the ALLOWLIST causes a test failure — it is a potential
sleep-in-poll-loop script that should either be allowlisted (with
documented justification) or refactored to remove the sleep-in-loop.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

ALLOWLIST = frozenset(
    {
        # Daemon/watchdog infrastructure — legitimate sleep-in-loop for background polling.
        "task_watchdog.py",
        "azure_event_guard.sh",
        # Background test runners supervise child processes and emit visible heartbeats.
        "heavy_sem.py",
        "run_test_background.sh",
        "run_ci_shards_parallel.py",
        "token_window_monitor.py",
        # Bounded service-readiness loop; it never occupies a delegated agent slot.
        "smoke_daemon.py",
        # Bounded infrastructure readiness/locking loops surface attempt heartbeats.
        "collection_lock.py",
        "postgres_e2e_runner.py",
        # Background lifecycle supervisors are signal-stoppable and continuously observable.
        "e2e_supervisor.py",
        # Exact gate-tree waits have computed attempt ceilings and emit wait heartbeats.
        "kill_owned_gate.py",
        # Native Safari driver readiness has a fixed ten-second deadline and one-second heartbeats.
        "run_presentation_safari_smoke.py",
        # The gate watcher has an explicit total timeout and periodic log heartbeats;
        # termination waits have fixed deadlines and the identity retry is capped at 20 attempts.
        "start_gate_background.py",
        # Release-only CI observers are bounded, emit heartbeats, and remain blocked
        # from delegated-agent prompts by enforce-no-wait.ts.
        "ci_annotations_poll.py",
        "ci_await.py",
        "ci_poll.py",
        "ci_push_and_verify.sh",
        "ci_signal_exact_sha.py",
        # Native command observer owned by run-watched; bounded by explicit
        # --max-secs/--quiet-secs, emits heartbeats, and drains the exact child
        # process tree before returning.
        "stream_command.py",
        # The local-model E2E readiness loop is bounded to 120 seconds and test-owned.
        "test_llama_3_2_game_gen.py",
    }
)


class _PythonSleepInLoopVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loop_depth = 0
        self.found = False

    def generic_visit(self, node: ast.AST) -> None:
        """Walk every native field without allocating iter_fields pairs."""
        for field in node._fields:
            try:
                value = getattr(node, field)
            except AttributeError:
                continue
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        self.visit(item)
            elif isinstance(value, ast.AST):
                self.visit(value)

    def _visit_loop(self, node: ast.AST) -> None:
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        is_sleep = (isinstance(function, ast.Name) and function.id == "sleep") or (
            isinstance(function, ast.Attribute) and function.attr == "sleep"
        )
        if self.loop_depth and is_sleep:
            self.found = True
        self.generic_visit(node)


def _python_has_sleep_in_loop(text: str, file_path: Path) -> bool:
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        relative_path = file_path.relative_to(REPO_ROOT)
        raise AssertionError(f"Source file is not valid Python: {relative_path}") from exc
    visitor = _PythonSleepInLoopVisitor()
    visitor.visit(tree)
    return visitor.found


def _shell_has_sleep_in_loop(text: str) -> bool:
    loop_depth = 0
    pending_loop = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if not line:
            continue
        if re.match(r"^(for|while|until)\b", line):
            pending_loop = True
        if pending_loop and re.search(r"(?:^|;)\s*do(?:\s|;|$)", line):
            loop_depth += 1
            pending_loop = False
        if loop_depth and re.search(r"(?:^|[;&|()])\s*sleep(?:\s|$)", line):
            return True
        if loop_depth and re.match(r"^done(?:\s|;|$)", line):
            loop_depth -= 1
    return False


def _has_sleep_in_loop(file_path: Path) -> bool:
    """Return True only when an executable sleep call is nested in a loop."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        relative_path = file_path.relative_to(REPO_ROOT)
        raise AssertionError(f"Source file is not valid UTF-8: {relative_path}") from exc
    if file_path.suffix == ".py":
        return _python_has_sleep_in_loop(text, file_path)
    return _shell_has_sleep_in_loop(text)


def _collect_sleep_in_loop_files() -> frozenset[str]:
    result: set[str] = set()
    for ext in ("*.py", "*.sh"):
        for file_path in sorted(SCRIPTS_DIR.glob(ext)):
            if _has_sleep_in_loop(file_path):
                result.add(file_path.name)
    return frozenset(result)


def test_allowlisted_files_exist() -> None:
    """Every file in ALLOWLIST must exist in scripts/."""
    missing = [f for f in ALLOWLIST if not (SCRIPTS_DIR / f).is_file()]
    assert not missing, f"ALLOWLIST entries do not exist in scripts/: {', '.join(sorted(missing))}"


def test_canonical_disk_alias_requires_no_polling_allowance() -> None:
    """Keep the narrowed canonical disk facade outside polling exceptions."""
    assert not _has_sleep_in_loop(SCRIPTS_DIR / "disk-guard.sh")
    assert "disk-guard.sh" not in ALLOWLIST


def test_disk_alias_polling_contract_is_checked_before_publication() -> None:
    """Execute the native owner plan and retain full-gate authority."""
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "_pytest-runtime-compatibility-admission"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assignments = [
        argument.removeprefix("TESTFILES=")
        for argument in shlex.split(result.stdout)
        if argument.startswith("TESTFILES=")
    ]
    assert assignments and "tests/unit/test_no_sleep_subagent.py" in assignments[0].split()
    manifest = json.loads((REPO_ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    family = next(entry for entry in manifest["families"] if entry["id"] == "disk-alias-polling-exception-drift")
    assert family == {
        "id": "disk-alias-polling-exception-drift",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": "tests/unit/test_no_sleep_subagent.py::test_allowlist_entries_are_still_valid",
        "full_gate_phase": "test",
    }


def test_no_unexpected_sleep_in_loop_files() -> None:
    """Only allowlisted files may contain sleep inside a loop construct."""
    found = _collect_sleep_in_loop_files()
    unexpected = found - ALLOWLIST
    assert not unexpected, (
        "Unexpected sleep-in-loop files in scripts/ (not in ALLOWLIST):\n"
        + "\n".join(f"  - {f}" for f in sorted(unexpected))
        + "\n\nEither add to ALLOWLIST with documented justification in the test file,"
        + "\nor refactor to remove the sleep-in-poll-loop anti-pattern."
    )


def test_allowlist_entries_are_still_valid() -> None:
    """Allowlisted files that exist but no longer match the pattern should be
    re-evaluated — the test flags them as stale."""
    found = _collect_sleep_in_loop_files()
    stale = ALLOWLIST - found
    assert not stale, "ALLOWLIST entries no longer contain sleep-in-loop — remove them from ALLOWLIST:\n" + "\n".join(
        f"  - {f}" for f in sorted(stale)
    )


def test_python_scan_ignores_sleep_words_outside_loops(tmp_path: Path) -> None:
    script = tmp_path / "false_positive.py"
    script.write_text(
        'RULE = "never sleep"\nfor item in ():\n    print(item)\n',
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is False


def test_python_scan_detects_sleep_call_inside_loop(tmp_path: Path) -> None:
    script = tmp_path / "poller.py"
    script.write_text(
        "import time\nwhile True:\n    time.sleep(1)\n",
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is True


def test_shell_scan_ignores_sleep_outside_loop(tmp_path: Path) -> None:
    script = tmp_path / "false_positive.sh"
    script.write_text(
        'echo "sleep"\nfor item in one two; do\n  echo "$item"\ndone\n',
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is False


def test_shell_scan_detects_sleep_inside_loop(tmp_path: Path) -> None:
    script = tmp_path / "poller.sh"
    script.write_text(
        "while true; do\n  sleep 1\ndone\n",
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is True


class _NoSleepNativeOrder(ast.NodeVisitor):
    """Record the unmodified native traversal independently of the audit."""

    def __init__(self) -> None:
        self.nodes: list[ast.AST] = []

    def visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().visit(node)


class _NoSleepRecordedAudit(_PythonSleepInLoopVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[ast.AST] = []
        self.depths: list[int] = []

    def visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        self.depths.append(self.loop_depth)
        super().visit(node)


class _NoSleepNativeAudit(_NoSleepRecordedAudit):
    # The semantic witness retains the actual stdlib traversal, not a copy.
    generic_visit = ast.NodeVisitor.generic_visit


def _no_sleep_native_field_work(
    visitor: _NoSleepRecordedAudit,
    tree: ast.AST,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, int]:
    """Observe the real native generator while forwarding every native pair."""
    native_fields = ast.iter_fields
    owner_thread = threading.get_ident()
    counts = [0, 0]
    observing = False

    def observed_fields(node: ast.AST) -> Iterator[tuple[str, object]]:
        owned_call = observing and threading.get_ident() == owner_thread
        if owned_call:
            counts[0] += 1
        for field, value in native_fields(node):
            if owned_call:
                counts[1] += 1
            yield field, value

    with monkeypatch.context() as scope:
        scope.setattr(ast, "iter_fields", observed_fields)
        observing = True
        try:
            visitor.visit(tree)
        finally:
            observing = False
    return counts[0], counts[1]


def _assert_no_sleep_native_equivalence(
    tree: ast.AST,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _NoSleepNativeOrder()
    order.visit(tree)
    native = _NoSleepNativeAudit()
    native_work = _no_sleep_native_field_work(native, tree, monkeypatch)
    actual = _NoSleepRecordedAudit()
    actual_work = _no_sleep_native_field_work(actual, tree, monkeypatch)

    # Keep the semantic and whole-order oracle ahead of the work assertion.
    assert native.found is expected
    assert actual.found is expected
    assert native.nodes == actual.nodes == order.nodes
    assert native.depths == actual.depths
    assert native.loop_depth == actual.loop_depth == 0
    assert native_work[0] > 0 and native_work[1] > 0, "the real native generator witness was inert"
    assert actual_work == (0, 0), "the complete equivalent audit still performs native generator work"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "for outer in ():\n    while False:\n        clock.sleep(0)\n",
            True, id="nested-for-while",
        ),
        pytest.param(
            "for item in ():\n    pass\nelse:\n    sleep(0)\n",
            True, id="loop-orelse-retains-original-depth",
        ),
        pytest.param(
            "def owned():\n    while False:\n        sleep(0)\n",
            True, id="loop-in-function",
        ),
        pytest.param(
            "for item in ():\n    def owned():\n        sleep(0)\n",
            True, id="function-inside-loop-retains-original-depth",
        ),
        pytest.param(
            "async def owned():\n    async for item in source:\n        await clock.sleep(0)\n",
            True, id="async-for-and-await",
        ),
        pytest.param(
            "values = [sleep(0) for item in ()]\n",
            False, id="comprehension-retains-original-nonloop-recognition",
        ),
        pytest.param(
            "for outer in ():\n    values = [clock.sleep(0) for item in ()]\n",
            True, id="comprehension-in-recognized-loop",
        ),
        pytest.param(
            "sleep(0)\nfor item in ():\n    pass\n",
            False, id="sleep-outside-loop",
        ),
        pytest.param(
            "for item in ():\n    clock.sleep(0)\n",
            True, id="current-attribute-call-recognition",
        ),
        pytest.param(
            "for item in ():\n    sleep(0)\n",
            True, id="current-name-call-recognition",
        ),
        pytest.param(
            "for item in ():\n    clock['sleep'](0)\n    getattr(clock, 'sleep')(0)\n",
            False, id="current-indirect-call-nonrecognition",
        ),
        pytest.param(
            "for item in ():\n    \uff53\uff4c\uff45\uff45\uff50(0)\n",
            True, id="native-normalized-identifier",
        ),
    ],
)
def test_no_sleep_native_walk_keeps_semantics_order_and_removes_measured_generator_work(
    source: str,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the actual parser/audit plus an independent native-order oracle."""
    assert _python_has_sleep_in_loop(source, Path(__file__)) is expected
    tree = ast.parse(source, filename="owned-no-sleep-grammar.py")
    _assert_no_sleep_native_equivalence(tree, expected, monkeypatch)


class _NoSleepFieldError(RuntimeError):
    pass


class _NoSleepFieldProbe(ast.AST):
    _fields = ("first", "missing", "last")

    def __init__(self, *, fatal: bool) -> None:
        self.fatal = fatal
        self.accesses: list[str] = []
        self._first = ast.parse("while False:\n    clock.sleep(0)\n").body[0]
        self._last = ast.parse("outside = 1\n").body

    @property
    def first(self) -> ast.AST:
        self.accesses.append("first")
        return self._first

    @property
    def missing(self) -> ast.AST:
        self.accesses.append("missing")
        if self.fatal:
            raise _NoSleepFieldError("owned no-sleep field fault")
        raise AttributeError("owned absent optional field")

    @property
    def last(self) -> list[ast.stmt]:
        self.accesses.append("last")
        return self._last


def test_no_sleep_native_walk_preserves_missing_field_and_getattr_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native AST field contract includes absent fields and ordered getters."""
    tree = _NoSleepFieldProbe(fatal=False)
    _assert_no_sleep_native_equivalence(tree, True, monkeypatch)
    assert tree.accesses == ["first", "missing", "last"] * 3


def test_no_sleep_native_walk_propagates_field_error_after_a_positive_match() -> None:
    """Finding a sleep must not suppress later native field-access failures."""
    tree = _NoSleepFieldProbe(fatal=True)
    order = _NoSleepNativeOrder()
    with pytest.raises(_NoSleepFieldError, match="owned no-sleep field fault"):
        order.visit(tree)
    expected_order = list(order.nodes)
    assert tree.accesses == ["first", "missing"]
    tree.accesses.clear()

    native = _NoSleepNativeAudit()
    with pytest.raises(_NoSleepFieldError, match="owned no-sleep field fault"):
        native.visit(tree)
    assert tree.accesses == ["first", "missing"]
    tree.accesses.clear()

    actual = _NoSleepRecordedAudit()
    with pytest.raises(_NoSleepFieldError, match="owned no-sleep field fault"):
        actual.visit(tree)
    assert tree.accesses == ["first", "missing"]
    assert actual.nodes == native.nodes == expected_order
    assert actual.depths == native.depths
    assert actual.found is native.found is True
    assert actual.loop_depth == native.loop_depth == 0


def test_no_sleep_native_collection_keeps_fresh_inputs_and_parse_failure_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh same-size/same-mtime edits and every discovery/error remain live."""
    owned = tmp_path / "owned-repository"
    scripts = owned / "scripts"
    scripts.mkdir(parents=True)
    monkeypatch.setitem(globals(), "REPO_ROOT", owned)
    monkeypatch.setitem(globals(), "SCRIPTS_DIR", scripts)
    first = scripts / "a_first.py"
    source = "while False:\n    clock.sleep(0)\n"
    first.write_text(source, encoding="utf-8")
    assert _collect_sleep_in_loop_files() == frozenset({"a_first.py"})

    original = first.stat()
    replacement = source.replace("sleep", "other")
    assert len(replacement.encode()) == len(source.encode())
    first.write_text(replacement, encoding="utf-8")
    os.utime(first, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert first.stat().st_size == original.st_size
    assert first.stat().st_mtime_ns == original.st_mtime_ns
    assert _collect_sleep_in_loop_files() == frozenset()

    shell = scripts / "owned.sh"
    shell.write_text("while true; do\n    sleep 1\ndone\n", encoding="utf-8")
    assert _collect_sleep_in_loop_files() == frozenset({"owned.sh"})
    shell.unlink()
    assert _collect_sleep_in_loop_files() == frozenset()

    later = scripts / "b_later.py"
    later.write_bytes(b"\xff")
    first.write_text("def owned(:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Source file is not valid Python") as invalid_python:
        _collect_sleep_in_loop_files()
    assert str(first.relative_to(owned)) in str(invalid_python.value)

    first.write_text("owned = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Source file is not valid UTF-8") as invalid_utf8:
        _collect_sleep_in_loop_files()
    assert str(later.relative_to(owned)) in str(invalid_utf8.value)
    later.write_text("owned = 2\n", encoding="utf-8")
    assert _collect_sleep_in_loop_files() == frozenset()
