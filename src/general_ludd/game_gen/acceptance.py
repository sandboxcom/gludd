"""Acceptance gate for model-generated game code.

Evaluates generated Python source and returns a single
:class:`AcceptanceResult` (accept/reject + reasons). Checks, in order:

1. **Content sanity** — empty or mostly non-ASCII sources are junk.
2. **Syntax** — the module must parse.
3. **Forbidden imports/calls** — no ``os``/``subprocess``/``socket`` imports,
   no ``eval``/``exec``, no ``open()`` of arbitrary paths. When any forbidden
   construct is present the module is rejected WITHOUT being executed.
4. **Game class contract** — exactly the first game class with the required
   methods (``__init__``, ``start``, ``tick``, ``score``, ``is_game_over``,
   ``restart``).
5. **Bounded runtime** — the module is executed in a fresh namespace under a
   hard wall-clock budget; the game is instantiated and exercised. Hangs,
   exceptions, non-int ``score()``, non-bool ``is_game_over()``, and junk
   output all reject.

CLI (the local_game_gen role's verify steps can call this directly):

.. code-block:: text

    python -m general_ludd.game_gen.acceptance /tmp/artifacts/snake.py

Exit code 0 on accept, 1 on reject.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout, suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import NoReturn, cast

DEFAULT_MODULE_NAME = "generated_game"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_OUTPUT_CHARS = 4096
MAX_TICKS = 5
NON_ASCII_RATIO_LIMIT = 0.10
STDOUT_SNIPPET_CHARS = 500

REQUIRED_METHODS: tuple[str, ...] = ("__init__", "start", "tick", "score", "is_game_over", "restart")

FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "builtins",
        "ctypes",
        "fcntl",
        "importlib",
        "marshal",
        "multiprocessing",
        "os",
        "pickle",
        "pty",
        "resource",
        "shutil",
        "socket",
        "subprocess",
        "sys",
    }
)

FORBIDDEN_CALLS: frozenset[str] = frozenset({"__import__", "compile", "eval", "exec", "input", "open"})

FORBIDDEN_ATTR_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("io", "open"),
        ("os", "popen"),
        ("os", "spawnl"),
        ("os", "spawnlp"),
        ("os", "spawnv"),
        ("os", "spawnvp"),
        ("os", "system"),
    }
)

_RUNTIME_PROBE_SCRIPT = """
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout, suppress
from pathlib import Path

MAX_TICKS = 5


def _fs_snapshot(root: Path) -> dict[str, tuple[float, int]]:
    snapshot: dict[str, tuple[float, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".venv"}]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
                snapshot[str(path.relative_to(root))] = (stat.st_mtime, stat.st_size)
            except OSError:
                continue
    return snapshot


def _run(source: str, module_name: str) -> dict[str, object]:
    namespace: dict[str, object] = {"__name__": module_name, "__builtins__": __builtins__}
    stdout = io.StringIO()
    stderr = io.StringIO()
    verdict: dict[str, object] = {"failure": None, "class_name": None, "output": ""}
    fs_before = _fs_snapshot(Path.cwd())
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(compile(source, "<generated_game>", "exec"), namespace)
    except BaseException as exc:
        verdict["failure"] = f"generated module raised {type(exc).__name__}: {exc}"
        return verdict
    fs_after = _fs_snapshot(Path.cwd())
    created = sorted(set(fs_after) - set(fs_before))
    modified = sorted(rel for rel in set(fs_after) & set(fs_before) if fs_after[rel] != fs_before[rel])
    if created or modified:
        for rel in created:
            with suppress(OSError):
                (Path.cwd() / rel).unlink()
        verdict["failure"] = (
            "import-time filesystem side effect detected: "
            f"created={created} modified={modified}"
        )
        verdict["output"] = stdout.getvalue() + stderr.getvalue()
        return verdict
    game_class: type | None = None
    for value in namespace.values():
        if isinstance(value, type) and getattr(value, "__module__", None) == module_name:
            game_class = value
            break
    if game_class is None:
        verdict["failure"] = "no game class defined at runtime"
        return verdict
    verdict["class_name"] = game_class.__name__
    try:
        instance = game_class()
    except BaseException as exc:
        verdict["failure"] = f"instantiation raised {type(exc).__name__}: {exc}"
        return verdict
    try:
        instance.start()
    except BaseException as exc:
        verdict["failure"] = f"start() raised {type(exc).__name__}: {exc}"
        return verdict
    try:
        score_value = instance.score()
    except BaseException as exc:
        verdict["failure"] = f"score() raised {type(exc).__name__}: {exc}"
        return verdict
    if not isinstance(score_value, int) or isinstance(score_value, bool):
        verdict["failure"] = f"score() returned {type(score_value).__name__}, expected int"
        return verdict
    try:
        over_value = instance.is_game_over()
    except BaseException as exc:
        verdict["failure"] = f"is_game_over() raised {type(exc).__name__}: {exc}"
        return verdict
    if not isinstance(over_value, bool):
        verdict["failure"] = f"is_game_over() returned {type(over_value).__name__}, expected bool"
        return verdict
    for _ in range(MAX_TICKS):
        try:
            over_value = instance.is_game_over()
        except BaseException as exc:
            verdict["failure"] = f"is_game_over() raised {type(exc).__name__}: {exc}"
            return verdict
        if over_value is True:
            break
        try:
            instance.tick("right")
        except BaseException as exc:
            verdict["failure"] = f"tick() raised {type(exc).__name__}: {exc}"
            return verdict
    try:
        instance.restart()
    except BaseException as exc:
        verdict["failure"] = f"restart() raised {type(exc).__name__}: {exc}"
        return verdict
    verdict["output"] = stdout.getvalue() + stderr.getvalue()
    return verdict


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    source_path, verdict_path, module_name = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        with open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except OSError as exc:
        payload: dict[str, object] = {"failure": f"cannot read file: {exc}", "class_name": None, "output": ""}
    else:
        payload = _run(source, module_name)
    try:
        with open(verdict_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


class RuntimeBudgetExceeded(TimeoutError):
    """Raised when generated code exceeds its hard runtime budget."""


@dataclass
class AcceptanceResult:
    """Verdict of a single acceptance run."""

    accepted: bool
    reasons: list[str] = field(default_factory=list)
    game_class_name: str | None = None
    stdout_snippet: str = ""
    elapsed_seconds: float = 0.0


def _non_ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for ch in text if ord(ch) > 127) / len(text)


def _scan_forbidden(tree: ast.Module) -> list[str]:
    """Return one reason per dangerous import/call found in the tree."""
    reasons: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    reasons.append(f"forbidden import: {alias.name} at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                root = node.module.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    reasons.append(f"forbidden import: from {node.module} at line {node.lineno}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                reasons.append(f"forbidden call: {func.id}() at line {node.lineno}")
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)
                if pair in FORBIDDEN_ATTR_CALLS:
                    reasons.append(f"forbidden call: {func.value.id}.{func.attr}() at line {node.lineno}")
            elif (
                isinstance(func, ast.Subscript) and isinstance(func.value, ast.Name) and func.value.id == "__builtins__"
            ):
                reasons.append(f"forbidden builtins access at line {node.lineno}")
    return reasons


def _class_contract(tree: ast.Module) -> tuple[str | None, str]:
    """Return (game class name, problem). Problem is empty when the contract holds."""
    top_level = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    classes = top_level or [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    if not classes:
        return None, "no game class defined"
    game = classes[0]
    methods = {node.name for node in game.body if isinstance(node, ast.FunctionDef)}
    missing = sorted(set(REQUIRED_METHODS) - methods)
    if missing:
        return None, f"class {game.name} missing required methods: {', '.join(missing)}"
    return game.name, ""


def _output_snippet(stdout: io.StringIO, stderr: io.StringIO) -> str:
    return stdout.getvalue() + stderr.getvalue()


def _fs_snapshot(root: Path) -> dict[str, tuple[float, int]]:
    """Return {relative path: (mtime, size)} for the CWD tree.

    The acceptance probe rejects any change the generated module makes to
    this snapshot — games must not touch the filesystem at import time.
    """
    snapshot: dict[str, tuple[float, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".venv"}]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
                snapshot[str(path.relative_to(root))] = (stat.st_mtime, stat.st_size)
            except OSError:
                continue
    return snapshot


def _cleanup_new_files(root: Path, before: dict[str, tuple[float, int]]) -> None:
    """Remove files the generated module created during import."""
    after = _fs_snapshot(root)
    for rel in set(after) - set(before):
        with suppress(OSError):
            (root / rel).unlink()


def _runtime_probe(
    source: str,
    module_name: str,
    timeout: float,
) -> tuple[str | None, str | None, str]:
    """Execute the module and exercise its game class under a hard budget.

    Returns ``(failure_reason, game_class_name, output_snippet)``. A
    non-None failure reason means the code must be rejected.
    """
    namespace: dict[str, object] = {"__name__": module_name, "__builtins__": __builtins__}
    stdout = io.StringIO()
    stderr = io.StringIO()
    current_phase: list[str] = [f"module import ({module_name})"]

    def _alarm_handler(_signum: int, _frame: FrameType | None) -> NoReturn:
        raise RuntimeBudgetExceeded(f"{current_phase[0]} exceeded the {timeout:g}s runtime budget")

    previous_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    timer_armed = False
    previous_timer: tuple[float, float] = (0.0, 0.0)
    try:
        try:
            previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout)
            timer_armed = True
        except (ValueError, OSError):
            return "runtime watchdog unavailable in this thread", None, ""
        try:
            try:
                fs_before = _fs_snapshot(Path.cwd())
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exec(compile(source, f"<{module_name}>", "exec"), namespace)
                fs_after = _fs_snapshot(Path.cwd())
                created = sorted(set(fs_after) - set(fs_before))
                modified = sorted(rel for rel in set(fs_after) & set(fs_before) if fs_after[rel] != fs_before[rel])
                if created or modified:
                    _cleanup_new_files(Path.cwd(), fs_before)
                    return (
                        f"import-time filesystem side effect detected: created={created} modified={modified}",
                        None,
                        _output_snippet(stdout, stderr),
                    )
            except (Exception, SystemExit, KeyboardInterrupt, GeneratorExit) as exc:
                return (
                    f"generated module raised {type(exc).__name__}: {exc}",
                    None,
                    _output_snippet(stdout, stderr),
                )

            game_class: type | None = None
            for value in namespace.values():
                if isinstance(value, type) and getattr(value, "__module__", None) == module_name:
                    game_class = value
                    break
            if game_class is None:
                return "no game class defined at runtime", None, _output_snippet(stdout, stderr)
            class_name = game_class.__name__
            factory = cast(Callable[[], object], game_class)

            current_phase[0] = f"{class_name} instantiation"
            try:
                instance = factory()
            except (Exception, SystemExit, KeyboardInterrupt, GeneratorExit) as exc:
                return (
                    f"instantiation raised {type(exc).__name__}: {exc}",
                    class_name,
                    _output_snippet(stdout, stderr),
                )

            def _call(method_name: str, *args: object) -> tuple[object | None, str | None]:
                current_phase[0] = f"{class_name}.{method_name}()"
                try:
                    method = cast(Callable[..., object], getattr(instance, method_name))
                except AttributeError:
                    return None, f"{method_name}() missing at runtime"
                try:
                    return method(*args), None
                except (Exception, SystemExit, KeyboardInterrupt, GeneratorExit) as exc:
                    return None, f"{method_name}() raised {type(exc).__name__}: {exc}"

            _, failure = _call("start")
            if failure is not None:
                return failure, class_name, _output_snippet(stdout, stderr)

            score_value, failure = _call("score")
            if failure is not None:
                return failure, class_name, _output_snippet(stdout, stderr)
            if not isinstance(score_value, int) or isinstance(score_value, bool):
                return (
                    f"score() returned {type(score_value).__name__}, expected int",
                    class_name,
                    _output_snippet(stdout, stderr),
                )

            over_value, failure = _call("is_game_over")
            if failure is not None:
                return failure, class_name, _output_snippet(stdout, stderr)
            if not isinstance(over_value, bool):
                return (
                    f"is_game_over() returned {type(over_value).__name__}, expected bool",
                    class_name,
                    _output_snippet(stdout, stderr),
                )

            for _ in range(MAX_TICKS):
                over_value, failure = _call("is_game_over")
                if failure is not None:
                    return failure, class_name, _output_snippet(stdout, stderr)
                if over_value is True:
                    break
                _, failure = _call("tick", "right")
                if failure is not None:
                    return failure, class_name, _output_snippet(stdout, stderr)

            _, failure = _call("restart")
            if failure is not None:
                return failure, class_name, _output_snippet(stdout, stderr)

            return None, class_name, _output_snippet(stdout, stderr)
        except RuntimeBudgetExceeded as exc:
            return str(exc), None, _output_snippet(stdout, stderr)
    finally:
        if timer_armed:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
        signal.signal(signal.SIGALRM, previous_handler)


def check_source(
    source: str,
    *,
    module_name: str = DEFAULT_MODULE_NAME,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> AcceptanceResult:
    """Accept or reject generated Python source. Never executes unsafe code."""
    started = time.monotonic()

    def _verdict(
        accepted: bool,
        reasons: list[str],
        class_name: str | None = None,
        snippet: str = "",
    ) -> AcceptanceResult:
        return AcceptanceResult(
            accepted=accepted,
            reasons=reasons,
            game_class_name=class_name,
            stdout_snippet=snippet[:STDOUT_SNIPPET_CHARS],
            elapsed_seconds=time.monotonic() - started,
        )

    if not source.strip():
        return _verdict(False, ["empty source"])

    reasons: list[str] = []
    ratio = _non_ascii_ratio(source)
    if ratio > NON_ASCII_RATIO_LIMIT:
        reasons.append(f"source is {ratio:.0%} non-ASCII — likely junk output")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        reasons.append(f"syntax error: line {exc.lineno}: {exc.msg}")
        return _verdict(False, reasons)

    reasons.extend(_scan_forbidden(tree))

    class_name, class_problem = _class_contract(tree)
    if class_problem:
        reasons.append(class_problem)

    if reasons:
        return _verdict(False, reasons, class_name)

    failure, runtime_class, snippet = _runtime_probe(source, module_name, timeout)
    if failure is not None:
        reasons.append(failure)
    if snippet:
        if len(snippet) > MAX_OUTPUT_CHARS:
            reasons.append(f"module printed excessive output ({len(snippet)} chars)")
        output_ratio = _non_ascii_ratio(snippet)
        if output_ratio > NON_ASCII_RATIO_LIMIT:
            reasons.append(f"module printed non-ASCII junk output ({output_ratio:.0%})")

    if reasons:
        return _verdict(False, reasons, runtime_class or class_name, snippet)
    return _verdict(True, [], runtime_class or class_name, snippet)


def check_file(
    path: str | Path,
    *,
    module_name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> AcceptanceResult:
    """Accept or reject a generated .py file on disk."""
    file_path = Path(path)
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return AcceptanceResult(
            accepted=False,
            reasons=["file is not valid UTF-8 text"],
            elapsed_seconds=0.0,
        )
    except OSError as exc:
        return AcceptanceResult(
            accepted=False,
            reasons=[f"cannot read file: {exc}"],
            elapsed_seconds=0.0,
        )
    return check_source(source, module_name=module_name or file_path.stem, timeout=timeout)


def _subprocess_probe(
    file_path: Path,
    timeout: float,
    module_name: str = DEFAULT_MODULE_NAME,
) -> tuple[str | None, str | None, str]:
    """Execute the module in a child process under a hard wall-clock budget.

    Returns ``(failure_reason, game_class_name, output_snippet)``. The child
    writes its verdict as JSON; a ``TimeoutExpired`` kill means the module
    hung (infinite loop at import or inside a method) and must be rejected.
    """
    fd, verdict_name = tempfile.mkstemp(prefix="gludd-accept-", suffix=".json")
    os.close(fd)
    verdict_path = Path(verdict_name)
    try:
        try:
            completed = subprocess.run(
                [sys.executable, "-c", _RUNTIME_PROBE_SCRIPT, str(file_path), verdict_name, module_name],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=file_path.parent,
            )
        except subprocess.TimeoutExpired:
            return f"generated module exceeded the {timeout:g}s runtime budget", None, ""
        except OSError as exc:
            return f"runtime probe could not start: {exc}", None, ""
        try:
            payload: dict[str, object] = json.loads(verdict_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stderr_tail = (completed.stderr or "").strip()[:200]
            detail = f" (probe stderr: {stderr_tail})" if stderr_tail else ""
            return f"runtime probe produced no verdict{detail}", None, ""
        failure = payload.get("failure")
        class_name = payload.get("class_name")
        output = payload.get("output")
        if failure is None and completed.returncode != 0:
            stderr_tail = (completed.stderr or "").strip()[:200]
            detail = f": {stderr_tail}" if stderr_tail else ""
            probe_class = class_name if isinstance(class_name, str) else None
            probe_output = output if isinstance(output, str) else ""
            return f"runtime probe exited {completed.returncode}{detail}", probe_class, probe_output
        return (
            failure if isinstance(failure, str) else None,
            class_name if isinstance(class_name, str) else None,
            output if isinstance(output, str) else "",
        )
    finally:
        with suppress(OSError):
            verdict_path.unlink()


def accept_generated_code(
    path: str,
    timeout_seconds: float = 10.0,
    module_name: str | None = None,
) -> AcceptanceResult:
    """Accept or reject a generated ``.py`` file on disk.

    Static checks (parse, forbidden imports/calls, class contract, junk) run
    in-process and never execute the code. The runtime exercise runs in a
    subprocess killed after ``timeout_seconds`` — an infinite loop at import
    or inside a method can therefore never hang the caller.
    """
    started = time.monotonic()

    def _verdict(
        accepted: bool,
        reasons: list[str],
        class_name: str | None = None,
        snippet: str = "",
    ) -> AcceptanceResult:
        return AcceptanceResult(
            accepted=accepted,
            reasons=reasons,
            game_class_name=class_name,
            stdout_snippet=snippet[:STDOUT_SNIPPET_CHARS],
            elapsed_seconds=time.monotonic() - started,
        )

    file_path = Path(path)
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return AcceptanceResult(
            accepted=False,
            reasons=["file is not valid UTF-8 text"],
            elapsed_seconds=0.0,
        )
    except OSError as exc:
        return AcceptanceResult(
            accepted=False,
            reasons=[f"cannot read file: {exc}"],
            elapsed_seconds=0.0,
        )

    reasons: list[str] = []
    if not source.strip():
        return _verdict(False, ["empty source"])

    ratio = _non_ascii_ratio(source)
    if ratio > NON_ASCII_RATIO_LIMIT:
        reasons.append(f"source is {ratio:.0%} non-ASCII — likely junk output")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        reasons.append(f"syntax error: line {exc.lineno}: {exc.msg}")
        return _verdict(False, reasons)

    reasons.extend(_scan_forbidden(tree))
    class_name, class_problem = _class_contract(tree)
    if class_problem:
        reasons.append(class_problem)
    if reasons:
        return _verdict(False, reasons, class_name)

    failure, runtime_class, snippet = _subprocess_probe(
        file_path,
        timeout_seconds,
        module_name=module_name or file_path.stem,
    )
    if failure is not None:
        reasons.append(failure)
    if snippet:
        if len(snippet) > MAX_OUTPUT_CHARS:
            reasons.append(f"module printed excessive output ({len(snippet)} chars)")
        output_ratio = _non_ascii_ratio(snippet)
        if output_ratio > NON_ASCII_RATIO_LIMIT:
            reasons.append(f"module printed non-ASCII junk output ({output_ratio:.0%})")

    if reasons:
        return _verdict(False, reasons, runtime_class or class_name, snippet)
    return _verdict(True, [], runtime_class or class_name, snippet)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m general_ludd.game_gen.acceptance",
        description="Accept or reject model-generated game code.",
    )
    parser.add_argument("file", help="path to the generated .py file")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="hard runtime budget in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--module-name",
        default=None,
        help="module name override (default: the file stem)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: prints the verdict; returns 0 on accept, 1 on reject."""
    args = _build_parser().parse_args(argv)
    result = accept_generated_code(
        args.file,
        timeout_seconds=args.timeout,
        module_name=args.module_name,
    )
    if result.accepted:
        print(f"ACCEPT {result.game_class_name} ({result.elapsed_seconds:.2f}s)")
        return 0
    print("REJECT")
    for reason in result.reasons:
        print(f"  - {reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
