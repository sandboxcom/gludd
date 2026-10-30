#!/usr/bin/env python3
"""Enforce a minimum interval between CI status checks.

Anti-pattern this prevents: an agent dispatches a "push + poll CI until
terminal" subagent that loops ``make ci-verdict`` every 60-90s for 30-40
minutes, holding a subagent slot and the orchestrator's attention while
producing zero value. CI runs on its own schedule; polling it does not
make it finish faster.

Usage::

    make ci-verdict-safe           # checks cooldown, then runs ci-verdict
    make ci-verdict-safe FORCE=1   # bypass cooldown (for release-cut only)
    make deploy-and-forget         # push + record timestamp + print checkback
    make ci-cooldown-status        # show remaining cooldown seconds

The internal ``record-push`` command runs only after a push succeeds. It
atomically arms verdict history and increments the restart counter; failed
preflight checks therefore cannot consume restart budget.

Exit codes for ``check``: 0=proceed to ci-verdict, 1=cooldown blocks AND last
verdict was RED/failure (the failure IS surfaced), 3=cooldown blocks and last
verdict was success/pending/unknown. In all cases the last known verdict is
printed to stderr so the caller never sees "PENDING" when CI was already RED.

State file: ``/tmp/gludd-ci-check-state.json``
    {"last_check_epoch": <float>, "last_push_epoch": <float>,
     "last_head_sha": "<sha>", "check_count": <int>,
     "last_verdict": "<success|failure|pending|unknown>",
     "last_verdict_epoch": <float>}

Verdict history file: ``/tmp/gludd-ci-verdict-history.json``
    ``record-verdict <verdict> <sha>`` additionally sets ``last_checked_sha``
    (and ``last_verdict``/``last_checked_ts``) in this file via an atomic
    tmp+rename write, preserving ``last_push_sha`` and any other existing
    keys. This is what unblocks the Makefile ``_ci-verdict-history-guard``
    (AA032), which refuses a push while ``last_checked_sha`` !=
    ``last_push_sha``.

Default cooldown: 600s (10 min). Override via ``CI_CHECK_COOLDOWN_SEC``.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict, cast


class CIState(TypedDict, total=False):
    """Durable cooldown state persisted between operator invocations."""

    last_check_epoch: float
    last_push_epoch: float
    last_head_sha: str
    check_count: int
    last_verdict: str
    last_verdict_epoch: float
    last_verdict_kind: str

STATE_FILE = Path(os.environ.get("GLUDD_CI_STATE_FILE", "/tmp/gludd-ci-check-state.json"))
HISTORY_FILE = Path(os.environ.get("GLUDD_CI_HISTORY_FILE", "/tmp/gludd-ci-verdict-history.json"))
RESTART_COUNT_FILE = Path(os.environ.get("GLUDD_CI_RESTART_COUNT_FILE", "/tmp/gludd-ci-restart-count"))
PUSH_STATE_FILE = Path(os.environ.get("GLUDD_PUSH_STATE_FILE", "/tmp/gludd-push-state.json"))
DEFAULT_COOLDOWN_SEC = int(os.environ.get("CI_CHECK_COOLDOWN_SEC", "600"))


def load_state() -> CIState:
    """Read cooldown state, retaining defaults for absent or corrupt JSON."""
    try:
        loaded: object = json.loads(STATE_FILE.read_text())
        if isinstance(loaded, dict):
            return cast(CIState, loaded)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {
        "last_check_epoch": 0.0,
        "last_push_epoch": 0.0,
        "last_head_sha": "",
        "check_count": 0,
        "last_verdict": "",
        "last_verdict_epoch": 0.0,
    }


def save_state(state: CIState) -> None:
    """Atomically persist the existing cooldown state."""
    _replace_atomic(STATE_FILE, json.dumps(state))


def load_history() -> dict[str, object]:
    """Read the CI verdict history file; missing/corrupt → empty dict."""
    try:
        loaded: object = json.loads(HISTORY_FILE.read_text())
        if isinstance(loaded, dict):
            return cast(dict[str, object], loaded)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _temporary_owned(path: Path, original: os.stat_result) -> bool:
    """Match the created regular file without following a replaced symbolic link."""
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(current.st_mode) and (
        current.st_dev, current.st_ino
    ) == (original.st_dev, original.st_ino)


def _replace_atomic(path: Path, contents: str) -> None:
    """Write through an exclusively created descriptor and replace one owner file."""
    temporary: Path | None = None
    identity: os.stat_result | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f".{path.name}.",
            suffix=".tmp", dir=path.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            identity = os.fstat(handle.fileno())
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
            if not _temporary_owned(temporary, identity):
                raise OSError("CI state temporary ownership changed before replacement")
            os.replace(temporary, path)
    finally:
        if temporary is not None and identity is not None and _temporary_owned(temporary, identity):
            temporary.unlink()


def save_history_atomic(history: dict[str, object]) -> None:
    """Atomically persist the existing verdict history."""
    _replace_atomic(HISTORY_FILE, json.dumps(history))


@contextmanager
def _history_lock() -> Iterator[None]:
    """Refuse concurrent history writers through the existing native OS pattern."""
    path = HISTORY_FILE.with_name(f"{HISTORY_FILE.name}.lock")
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OSError(f"CI history writer busy: {path}; retain state and drain its owner") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def remaining_cooldown_sec(state: CIState, cooldown: int = DEFAULT_COOLDOWN_SEC) -> float:
    """Return the seconds remaining in the native CI-check cooldown."""
    elapsed = time.time() - state.get("last_check_epoch", 0.0)
    return max(0.0, cooldown - elapsed)


def record_check(head_sha: str) -> None:
    """Persist check time, source identity and incremented check count."""
    state = load_state()
    state["last_check_epoch"] = time.time()
    state["last_head_sha"] = head_sha
    state["check_count"] = state.get("check_count", 0) + 1
    save_state(state)


def record_push(head_sha: str) -> None:
    """Persist the deployment push time and source identity."""
    state = load_state()
    state["last_push_epoch"] = time.time()
    state["last_head_sha"] = head_sha
    save_state(state)


def _load_restart_count() -> int:
    """Return the durable restart count, rejecting corrupt or negative state."""
    try:
        raw = RESTART_COUNT_FILE.read_text().strip()
    except FileNotFoundError:
        return 0
    value = int(raw)
    if value < 0:
        raise ValueError("restart count cannot be negative")
    return value


def _save_restart_count_atomic(value: int) -> None:
    """Atomically replace the restart-count state file."""
    _replace_atomic(RESTART_COUNT_FILE, str(value))


def cmd_record_restart_block(restart_count: int) -> int:
    """Atomically publish a structured AA023 denial event."""
    if restart_count < 3:
        print("RESTART-BLOCK-FAILED: count is below the blocking threshold", file=sys.stderr)
        return 2
    event = {
        "last_push_blocked": True,
        "block_reason": "_ci-restart-cap:limit",
        "restart_count": restart_count,
        "max_allowed": 3,
        "epoch": int(time.time()),
    }
    tmp_path = PUSH_STATE_FILE.with_name(f"{PUSH_STATE_FILE.name}.tmp")
    try:
        tmp_path.write_text(json.dumps(event))
        os.replace(tmp_path, PUSH_STATE_FILE)
    except OSError as exc:
        print(f"RESTART-BLOCK-FAILED: state write failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_record_push(
    expected_sha: str | None = None, branch: str | None = None,
    purpose: str | None = None,
) -> int:
    """Record exactly one push after it has successfully landed.

    Verdict history is armed before the restart count changes. If either
    durable write fails, the command surfaces the error and returns nonzero;
    callers must never retry the push itself. Diagnostic publication provenance
    completes only after both durable writes.
    """
    if expected_sha is not None and (len(expected_sha) != 40 or any(
            character not in "0123456789abcdefABCDEF" for character in expected_sha)):
        print("RECORD-PUSH-FAILED: explicit SHA must be full40hex", file=sys.stderr)
        return 2
    diagnostic = branch is not None or purpose is not None
    if diagnostic and (
        expected_sha is None or branch is None or not _diagnostic_branch(branch)
        or purpose != "feature-ci"
    ):
        print("RECORD-PUSH-FAILED: diagnostic metadata requires saved feature branch/SHA/purpose",
              file=sys.stderr)
        return 2
    head_sha = expected_sha.lower() if expected_sha is not None else get_head_sha()
    if not head_sha:
        print("RECORD-PUSH-FAILED: unable to resolve HEAD", file=sys.stderr)
        return 1
    try:
        with _history_lock():
            try:
                restart_count = _load_restart_count()
                history = load_history()
                generation = _push_generation(history)
                if generation is None:
                    raise ValueError("invalid push generation")
            except (OSError, ValueError) as exc:
                print(f"RECORD-PUSH-FAILED: invalid restart count/history: {exc}", file=sys.stderr)
                return 1
            previous = history.get("diagnostic_run")
            run_floor = previous.get("id", 0) if isinstance(previous, dict) else 0
            if type(run_floor) is not int or run_floor < 0:
                print("RECORD-PUSH-FAILED: invalid diagnostic run floor", file=sys.stderr)
                return 1
            generation += 1
            history.update({
                "last_push_sha": head_sha, "last_checked_sha": "", "ts": int(time.time()),
                "push_generation": generation, "push_record_complete": False,
            })
            history.pop("diagnostic_push", None)
            save_history_atomic(history)
            new_count = restart_count + 1
            _save_restart_count_atomic(new_count)
            history["push_record_complete"] = True
            if diagnostic:
                history["diagnostic_push"] = {
                    "repo": "sandboxcom/gludd", "branch": branch, "sha": head_sha,
                    "purpose": purpose, "generation": generation, "run_floor": run_floor,
                    "complete": True,
                }
            save_history_atomic(history)
    except OSError as exc:
        print(f"RECORD-PUSH-FAILED: state write failed: {exc}", file=sys.stderr)
        return 1

    print(f"CI-RESTART-CAP: successful push {new_count}/3 recorded.")
    print(f"recorded push verdict history for {head_sha}")
    return 0


def get_head_sha() -> str:
    """Return Git HEAD or an empty identity after native lookup failure."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return ""


def cmd_check(cooldown: int, force: bool) -> int:
    """Apply the check cooldown while retaining prior verdict and force semantics."""
    state = load_state()
    head_sha = get_head_sha()
    remaining = remaining_cooldown_sec(state, cooldown)
    if remaining > 0 and not force:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        last_verdict = state.get("last_verdict", "")
        last_verdict_epoch = state.get("last_verdict_epoch", 0.0)
        verdict_detail = _format_last_verdict(last_verdict, last_verdict_epoch)
        print(
            f"CI-COOLDOWN: {mins}m{secs}s remaining since last check "
            f"(check #{state.get('check_count', 0)}). "
            f"{verdict_detail} "
            f"CI runs on its own schedule; polling does not speed it up. "
            f"Resume real work; check back later with `make ci-verdict-safe`. "
            f"(override with FORCE=1 ONLY for release-cut)",
            file=sys.stderr,
        )
        if last_verdict in ("failure", "red", "error", "cancelled", "timed_out"):
            return 1
        return 3
    record_check(head_sha)
    print(f"CI-CHECK-{state.get('check_count', 0) + 1}: running ci-verdict for {head_sha[:12]}")
    return 0


def _format_last_verdict(verdict: str, epoch: float) -> str:
    if not verdict:
        return "Last known verdict: unknown (no prior check recorded)."
    age_str = _age_str(epoch)
    return f"Last known verdict: {verdict.upper()}{age_str}."


def _age_str(epoch: float) -> str:
    if not epoch:
        return ""
    age = int(time.time() - epoch)
    if age < 60:
        return f" ({age}s ago)"
    return f" ({age // 60}m{age % 60}s ago)"


def cmd_record_verdict(verdict: str, sha: str | None = None) -> int:
    """Record the existing verdict through the shared native history writer."""
    with _history_lock():
        state = load_state()
        state["last_verdict"] = verdict.strip().lower()
        state["last_verdict_epoch"] = time.time()
        state.pop("last_verdict_kind", None)
        save_state(state)
        if sha:
            history = load_history()
            history["last_checked_sha"] = sha
            history["last_verdict"] = verdict.strip().lower()
            history["last_checked_ts"] = int(time.time())
            history.pop("last_verdict_kind", None)
            save_history_atomic(history)
        _reset_restart_cap_on_terminal_verdict(verdict)
    return 0


def _reset_restart_cap_on_terminal_verdict(verdict: str, *, strict: bool = False) -> None:
    """Reset after terminal CI; diagnostic feedback retains actual write errors."""
    if verdict.strip().lower() not in {"success", "failure"}:
        return
    try:
        _save_restart_count_atomic(0)
    except OSError:
        if strict:
            raise


def _diagnostic_branch(branch: str) -> bool:
    return (
        re.fullmatch(r"(?:feature|diagnostic)/[A-Za-z0-9][A-Za-z0-9._/-]*", branch) is not None
        and ".." not in branch and "//" not in branch and not branch.endswith(("/", "."))
        and not any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))
    )


def _push_generation(history: Mapping[str, object]) -> int | None:
    value = history.get("push_generation", 0)
    return value if type(value) is int and value >= 0 else None


def diagnostic_push_generation() -> int | None:
    """Capture the current history generation before starting a GitHub lookup."""
    return _push_generation(load_history())


def _diagnostic_identity(
    *, repo: str, branch: str, sha: str, workflow: str, event: str,
    run: Mapping[str, object], after_run_id: int,
) -> bool:
    run_id = run.get("databaseId")
    return (
        repo == "sandboxcom/gludd" and _diagnostic_branch(branch)
        and re.fullmatch(r"[0-9a-f]{40}", sha) is not None
        and workflow == "Build and Release" and event == "workflow_dispatch"
        and type(run_id) is int and run_id > 0
        and type(after_run_id) is int and after_run_id >= 0 and run_id > after_run_id
        and run.get("headSha") == sha and run.get("headBranch") == branch
        and run.get("workflowName") == workflow and run.get("event") == event
    )


def _diagnostic_push_matches(
    history: Mapping[str, object], *, branch: str, sha: str, generation: int,
) -> bool:
    saved = history.get("diagnostic_push")
    return (
        isinstance(saved, dict) and saved.get("complete") is True
        and saved.get("repo") == "sandboxcom/gludd" and saved.get("branch") == branch
        and saved.get("sha") == sha and saved.get("purpose") == "feature-ci"
        and type(saved.get("generation")) is int and saved.get("generation") == generation
        and type(saved.get("run_floor")) is int and saved["run_floor"] >= 0
    )


def _record_diagnostic_run(
    *, repo: str, branch: str, sha: str, workflow: str, event: str,
    run: Mapping[str, object], after_run_id: int, expected_generation: int | None,
    pending: bool,
) -> bool:
    if not _diagnostic_identity(
        repo=repo, branch=branch, sha=sha, workflow=workflow, event=event,
        run=run, after_run_id=after_run_id,
    ):
        return False
    status, conclusion = run.get("status"), run.get("conclusion")
    if pending:
        if status not in ("queued", "in_progress", "pending", "requested", "waiting") or (
            conclusion not in ("", None)
        ):
            return False
    elif status != "completed" or conclusion not in (
        "success", "failure", "action_required", "cancelled", "neutral",
        "skipped", "stale", "startup_failure", "timed_out",
    ):
        return False
    if expected_generation is not None and (
        type(expected_generation) is not int or expected_generation < 0
    ):
        return False
    run_id = cast(int, run["databaseId"])
    with _history_lock():
        history = load_history()
        generation = _push_generation(history)
        if (
            history.get("last_push_sha") != sha or generation is None
            or (expected_generation is not None and expected_generation != generation)
            or ("push_generation" in history and history.get("push_record_complete") is not True)
        ):
            return False
        owned = _diagnostic_push_matches(history, branch=branch, sha=sha, generation=generation)
        if "diagnostic_push" in history and (not owned or expected_generation is None):
            return False
        if pending and (not owned or expected_generation is None or generation == 0):
            return False
        if owned:
            saved = cast(dict[str, object], history["diagnostic_push"])
            if run_id <= cast(int, saved["run_floor"]):
                return False
        previous = history.get("diagnostic_run")
        if isinstance(previous, dict) and previous.get("sha") == sha:
            previous_id = previous.get("id")
            if type(previous_id) is int and previous_id >= run_id:
                upgrading = (
                    not pending and previous_id == run_id and owned
                    and previous.get("generation") == generation
                    and previous.get("branch") == branch
                    and previous.get("status") in ("queued", "in_progress", "pending", "requested", "waiting")
                )
                if not upgrading:
                    return False
        terminal = "success" if conclusion == "success" else "failure"
        verdict = "pending" if pending else "diagnostic_success" if terminal == "success" else "failure"
        if not pending:
            state = load_state()
            state["last_verdict"] = verdict
            state["last_verdict_kind"] = "diagnostic-workflow-dispatch"
            state["last_verdict_epoch"] = time.time()
            save_state(state)
        history.update({
            "last_checked_sha": sha, "last_verdict": verdict,
            "last_verdict_kind": "diagnostic-workflow-dispatch",
            "last_checked_ts": int(time.time()),
            "diagnostic_run": {
                "id": run_id, "sha": sha, "repo": repo, "branch": branch,
                "workflow": workflow, "event": event, "generation": generation,
                "status": status, "conclusion": conclusion,
            },
        })
        save_history_atomic(history)
        if not pending:
            _reset_restart_cap_on_terminal_verdict(terminal, strict=True)
    return True


def record_diagnostic_verdict(
    *, repo: str, branch: str, sha: str, workflow: str, event: str,
    run: Mapping[str, object], after_run_id: int = 0,
    expected_generation: int | None = None,
) -> bool:
    """Record terminal feedback, retaining legacy canonical history behavior."""
    return _record_diagnostic_run(
        repo=repo, branch=branch, sha=sha, workflow=workflow, event=event,
        run=run, after_run_id=after_run_id, expected_generation=expected_generation, pending=False,
    )


def record_diagnostic_pending(
    *, repo: str, branch: str, sha: str, workflow: str, event: str,
    run: Mapping[str, object], expected_generation: int, after_run_id: int = 0,
) -> bool:
    """Acknowledge exact producer-owned pending feedback without resetting restart debt."""
    return _record_diagnostic_run(
        repo=repo, branch=branch, sha=sha, workflow=workflow, event=event,
        run=run, after_run_id=after_run_id, expected_generation=expected_generation, pending=True,
    )


def cmd_deploy() -> int:
    """Record deployment push timing and emit the existing checkback guidance."""
    head_sha = get_head_sha()
    record_push(head_sha)
    cooldown = DEFAULT_COOLDOWN_SEC
    print(f"DEPLOY-RECORDED: pushed {head_sha[:12]} at epoch {time.time():.0f}")
    print(f"CHECKBACK: run `make ci-verdict-safe` in at least {cooldown // 60} minutes.")
    print("In the meantime: dispatch real work. Do NOT poll.")
    return 0


def cmd_status(cooldown: int) -> int:
    """Describe current cooldown state without starting a CI lookup."""
    state = load_state()
    remaining = remaining_cooldown_sec(state, cooldown)
    last_check = state.get("last_check_epoch", 0.0)
    last_push = state.get("last_push_epoch", 0.0)
    count = state.get("check_count", 0)
    head = state.get("last_head_sha", "")
    last_verdict = state.get("last_verdict", "")
    last_verdict_epoch = state.get("last_verdict_epoch", 0.0)
    if remaining > 0:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        print(f"COOLDOWN-ACTIVE: {mins}m{secs}s remaining (check #{count})")
    else:
        print(f"COOLDOWN-EXPIRED: may check now (last check was {int(time.time() - last_check)}s ago)")
    if last_push:
        print(f"  last push: {int(time.time() - last_push)}s ago ({head[:12]})")
    if last_verdict:
        age = _age_str(last_verdict_epoch)
        print(f"  last verdict: {last_verdict.upper()}{age}")
    return 0


def main() -> int:
    """Dispatch native cooldown/history commands and retain their statuses."""
    if len(sys.argv) < 2:
        print(
            "usage: ci_check_cooldown.py "
            "{check|deploy|status|record-push|record-restart-block|record-verdict} [args...]",
            file=sys.stderr,
        )
        return 2
    cmd = sys.argv[1]
    force = os.environ.get("FORCE", "") == "1"
    if cmd == "check":
        cooldown = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_COOLDOWN_SEC
        return cmd_check(cooldown, force)
    if cmd == "deploy":
        return cmd_deploy()
    if cmd == "status":
        cooldown = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_COOLDOWN_SEC
        return cmd_status(cooldown)
    if cmd == "record-verdict":
        verdict = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        sha = sys.argv[3] if len(sys.argv) > 3 else None
        return cmd_record_verdict(verdict, sha)
    if cmd == "record-push":
        if len(sys.argv) == 5:
            return cmd_record_push(sys.argv[2], sys.argv[3], sys.argv[4])
        if len(sys.argv) > 3:
            print("record-push accepts at most one explicit full40hex SHA, "
                  "optionally followed by saved branch feature-ci", file=sys.stderr)
            return 2
        return cmd_record_push(sys.argv[2]) if len(sys.argv) == 3 else cmd_record_push()
    if cmd == "record-restart-block":
        try:
            restart_count = int(sys.argv[2])
        except (IndexError, ValueError):
            print("record-restart-block requires an integer count", file=sys.stderr)
            return 2
        return cmd_record_restart_block(restart_count)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
