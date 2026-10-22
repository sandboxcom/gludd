"""Administrative focus and command admission; never completion evidence."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import shlex
import sys
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if TYPE_CHECKING:
    from gate_status_attestation import repository_state_id
    from stream_command import _atomic_write_json, _load_status, _pid_alive, _safe_name
    from task_scope import TASK_LINE_RE, task_inventory
else:
    from scripts.gate_status_attestation import repository_state_id
    from scripts.stream_command import _atomic_write_json, _load_status, _pid_alive, _safe_name
    from scripts.task_scope import TASK_LINE_RE, task_inventory

_MAX_BYTES = 128 * 1024
_MAX_COMMANDS = 32
_DIAGNOSTICS = frozenset({
    "help", "cat-file", "show-lines", "search", "ps", "ps-gludd",
    "active-work-status", "observed-status", "observed-tail", "git-status",
    "verify-state", "worktree-state", "pipeline-status", "ci-await", "ci-run-summary",
})
_STATUS_WAIT_VARIABLES = frozenset({
    "INTERVAL", "COUNT", "STATUS_HEARTBEAT_VALIDATE_ONLY", "STATUS_HEARTBEAT_STOP_ON_TERMINAL",
    "PIPELINE_STATUS_REPO", "PIPELINE_STATUS_BRANCH", "PIPELINE_STATUS_REMOTE",
    "PIPELINE_STATUS_SHA", "PIPELINE_STATUS_FAILURE_LEDGER", "PIPELINE_STATUS_VALIDATE_ONLY",
    "PIPELINE_STATUS_OUTPUT",
})
_VOLATILE_ENV = frozenset({
    "RUN_ID", "OBSERVED_LABEL", "OBSERVED_ROOT", "LOG", "OBSERVED_HEARTBEAT_SECS",
    "OBSERVED_RETAIN_RUNS", "MAKEFLAGS", "MAKELEVEL", "MFLAGS", "SHLVL", "_",
    "GLUDD_XDIST_TRACE_LOG", "GLUDD_XDIST_TRACE_RUN_ID", "GLUDD_XDIST_TRACE_TRUNCATE",
    "WORK_ITEM_ID", "WORK_ITEM_ACTION", "WORK_ITEM_REASON", "WORK_ITEM_VALIDATE_ONLY",
    "MAX_SECS", "STALL_SECS",
})
_FIELDS = frozenset({
    "schema_version", "kind", "producer", "root", "primary_task_id",
    "supporting_tasks", "observations", "reservations", "release_ready", "gate_evidence",
})


def _command_key(command: object) -> str:
    encoded = json.dumps(command, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _digest(value: object, length: int = 64) -> bool:
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def _private(root: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(root) or any(
        parent.is_symlink() for parent in (path, *path.parents) if parent != root
    ):
        raise ValueError("item progress paths must be project-private and not symlinks")


def _read(root: Path, path: Path) -> dict[str, object]:
    _private(root, path)
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("item progress input exceeds 128 KiB")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("item progress input must be an object")
    return cast(dict[str, object], value)


def _supports(value: object, primary: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 2:
        raise ValueError("support must contain at most two direct tasks")
    for task, reason in value.items():
        if (not isinstance(task, str) or not task or task == primary
                or not isinstance(reason, str) or not reason.strip()
                or len(reason) > 240 or "\0" in reason):
            raise ValueError("support requires an ID and bounded direct reason")
    return cast(dict[str, str], value)


def progress_status(root: Path) -> dict[str, object] | None:
    """Read validated administrative focus without inferring task completion."""
    root = root.resolve()
    path = root / ".gludd/item-progress.json"
    _private(root, path)
    if not path.exists():
        return None
    state = _read(root, path)
    primary = state.get("primary_task_id")
    observations = state.get("observations")
    if (set(state) not in {_FIELDS, _FIELDS - {"reservations"}} or state.get("schema_version") != 1
            or state.get("kind") != "item_progress" or state.get("producer") != "stream_command"
            or state.get("root") != str(root) or state.get("release_ready") is not False
            or state.get("gate_evidence") is not False
            or not isinstance(primary, str) or not primary or len(primary) > 80
            or not isinstance(observations, dict) or len(observations) > _MAX_COMMANDS):
        raise ValueError("invalid item progress owner, schema or authority")
    _supports(state["supporting_tasks"], primary)
    for key, pointer in observations.items():
        if (not _digest(key)
                or not isinstance(pointer, dict) or set(pointer) != {"root", "label", "run_id"}):
            raise ValueError("invalid item progress observer pointer")
        if not isinstance(pointer["root"], str):
            raise ValueError("observer root is missing")
        if not Path(pointer["root"]).is_absolute():
            raise ValueError("observer root must be an absolute project-private path")
        _private(root, Path(pointer["root"]))
        for field in ("label", "run_id"):
            if not isinstance(pointer[field], str) or len(pointer[field]) > 128:
                raise ValueError("invalid observer pointer name")
            _safe_name(pointer[field], field=field)
    reservations = state.get("reservations", {})
    if not isinstance(reservations, dict) or len(reservations) > _MAX_COMMANDS:
        raise ValueError("invalid item progress reservation inventory")
    for key, reservation in reservations.items():
        if (not _digest(key) or not isinstance(reservation, dict)
                or set(reservation) != {"owner_pid", "token"}
                or type(reservation["owner_pid"]) is not int or reservation["owner_pid"] <= 0
                or not _digest(reservation["token"], 32)):
            raise ValueError("invalid item progress reservation")
    return state


def preserved_run_ids(root: Path, observed_root: Path, label: str) -> frozenset[str]:
    """Protect canonical references even when diagnostics have no admission slot."""
    state = progress_status(root)
    return _referenced_run_ids(state, observed_root, label)


def _referenced_run_ids(
    state: dict[str, object] | None, observed_root: Path, label: str,
) -> frozenset[str]:
    """Derive references only from the native owner's validated state."""
    if state is None:
        raise ValueError("item progress disappeared before retention")
    observations = cast(dict[str, dict[str, str]], state["observations"])
    return frozenset(
        pointer["run_id"] for pointer in observations.values()
        if pointer["root"] == str(observed_root.resolve()) and pointer["label"] == label
    )


@contextmanager
def protected_run_ids(root: Path, observed_root: Path, label: str) -> Iterator[frozenset[str]]:
    """Serialize a reference read and its bounded retention against record updates."""
    with _locked(root) as (_path, state):
        yield _referenced_run_ids(state, observed_root, label)


@contextmanager
def _locked(root: Path) -> Iterator[tuple[Path, dict[str, object] | None]]:
    from filelock import FileLock

    path = root / ".gludd/item-progress.json"
    lock_path = path.with_suffix(".lock")
    _private(root, path)
    _private(root, lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=2, mode=0o600):
        yield path, progress_status(root)


def _select(root: Path, state: dict[str, object] | None, primary: str) -> dict[str, object]:
    tasks = (root / "TASKS.md").read_text(encoding="utf-8")
    active = task_inventory(tasks)["open_task_ids"]
    if primary not in active:
        raise ValueError("primary must name an open canonical active task")
    if state is not None and state["primary_task_id"] != primary:
        previous = str(state["primary_task_id"])
        lines = [match.group(0) for match in TASK_LINE_RE.finditer(tasks)
                 if match.group("task_id") == previous]
        if not lines or previous in task_inventory("\n".join(lines))["open_task_ids"]:
            raise ValueError(f"primary {previous} remains open or is unavailable")
        state = None
    if state is None:
        state = {"schema_version": 1, "kind": "item_progress", "producer": "stream_command",
                 "root": str(root), "primary_task_id": primary, "supporting_tasks": {},
                 "observations": {}, "reservations": {}, "release_ready": False, "gate_evidence": False}
    return state


def focus_item(root: Path, primary_task_id: str) -> dict[str, object]:
    """Select one primary; ledger closure permits only administrative advancement."""
    root = root.resolve()
    with _locked(root) as (path, state):
        state = _select(root, state, primary_task_id)
        _atomic_write_json(path, state)
        return state


def support_item(root: Path, task_id: str, reason: str) -> dict[str, object]:
    """Declare at most two direct supporting items without nesting prerequisites."""
    root = root.resolve()
    with _locked(root) as (path, state):
        if state is None:
            raise ValueError("select a primary active item before declaring support")
        primary = str(state["primary_task_id"])
        state = _select(root, state, primary)
        supporting = {**_supports(state["supporting_tasks"], primary), task_id: reason}
        _supports(supporting, primary)
        if task_id not in task_inventory((root / "TASKS.md").read_text(encoding="utf-8"))["open_task_ids"]:
            raise ValueError("support must name a canonical active task")
        state["supporting_tasks"] = supporting
        _atomic_write_json(path, state)
        return state


def _environment_identity(environment: Mapping[str, str], cwd: Path | str | None = None) -> str:
    """Hash the effective private child context without publishing its values."""
    with Path(sys.executable).open("rb") as executable:
        executable_digest = hashlib.file_digest(executable, "sha256").hexdigest()
    return _command_key({
        "variables": {key: value for key, value in environment.items()
                      if key not in _VOLATILE_ENV and not key.startswith("OBSERVED_")},
        "cwd": str((Path.cwd() if cwd is None else Path(cwd)).resolve()),
        "python": sys.version, "executable": executable_digest,
        "installed_distributions": sorted(
            (metadata["Name"], metadata["Version"])
            for distribution in importlib.metadata.distributions()
            for metadata in (distribution.metadata,)
        ),
    })



def _canonical_native_command(command: Sequence[str]) -> Sequence[str]:
    """Recognize only an exactly quoted native argv inside the default shell.

    Execution and persisted identity retain the complete shell command. This
    conservative view exists only for the owner's established Make exemptions
    and fixed-command environment normalization; it never interprets a script.
    """
    if len(command) != 3 or list(command[:2]) != ["/bin/sh", "-c"]:
        return command
    try:
        arguments = shlex.split(command[2], comments=False, posix=True)
    except ValueError:
        return command
    return arguments if arguments and shlex.join(arguments) == command[2] else command


def _semantic_environment(command: Sequence[str], environment: Mapping[str, str]) -> Mapping[str, str]:
    """Normalize only lifecycle metadata for the fixed compatibility command."""
    command = _canonical_native_command(command)
    arguments = [arg for arg in command[1:] if arg != "--no-print-directory"]
    if not command or Path(command[0]).name != "make" or arguments != ["_pytest-runtime-compatibility-admission"]:
        return environment
    lifecycle = {"GATE_CANDIDATE_SHA", "GLUDD_CANDIDATE_SHA",
                 "GLUDD_GATE_OWNER_PID", "GATE_RUN_LOCK_OWNER_PID"}
    normalized = {name: value for name, value in environment.items() if name not in lifecycle}
    # GNU Make carries command assignments through MAKEFLAGS (already volatile).
    # MAKEOVERRIDES may be an opaque native reference or explicit semantic input;
    # preserve its bytes rather than interpreting Make escaping as shell syntax.
    return normalized


def _identity(root: Path, command: Sequence[str], environment: Mapping[str, str],
              quiet_seconds: float, max_seconds: float, *, cwd: Path | str | None = None) -> dict[str, object]:
    if (not command or len(command) > 128
            or any(not isinstance(arg, str) or not arg or len(arg) > 8192 for arg in command)
            or any(isinstance(limit, bool) or not math.isfinite(limit) or limit < 0
                   for limit in (quiet_seconds, max_seconds))):
        raise ValueError("command arguments and runtime bounds are invalid")
    return {
        "root": str(root), "command": list(command),
        "source": repository_state_id(root, include_head=False, exclude_bookkeeping=True),
        "environment": _environment_identity(_semantic_environment(command, environment), cwd),
        "limits": [quiet_seconds, max_seconds],
    }


def _native(root: Path, pointer: Mapping[str, str]) -> dict[str, object]:
    directory = Path(pointer["root"])
    _read(root, directory / pointer["label"] / f"{pointer['run_id']}.json")
    native = _load_status(directory, pointer["label"], run_id=pointer["run_id"])
    identity = native.get("progress_identity")
    if (not isinstance(identity, dict)
            or set(identity) != {"root", "command", "source", "environment", "limits"}
            or identity.get("root") != str(root)
            or not isinstance(identity.get("command"), list) or not identity["command"]
            or any(not isinstance(arg, str) or not arg for arg in identity["command"])
            or any(not isinstance(identity.get(field), str) or len(identity[field]) != 64
                   or any(char not in "0123456789abcdef" for char in identity[field])
                   for field in ("source", "environment"))
            or not isinstance(identity.get("limits"), list) or len(identity["limits"]) != 2
            or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   or not math.isfinite(value) or value < 0 for value in identity["limits"])):
        raise ValueError("native observer command identity is invalid")
    state, code = native.get("state"), native.get("exit_code")
    if (state not in {"starting", "running", "passed", "failed", "timed_out", "interrupted"}
            or (state in {"starting", "running"} and code is not None)
            or (state not in {"starting", "running"} and (type(code) is not int or code < 0))
            or (state == "passed" and code != 0) or (state == "failed" and code == 0)
            or (state == "timed_out" and code != 124)
            or native.get("release_ready", False) is not False
            or native.get("gate_evidence", False) is not False):
        raise ValueError("native observer result is invalid")
    return cast(dict[str, object], native)


@dataclass
class Admission:
    """A short-lived reservation whose pointer follows only a native publication."""

    root: Path
    path: Path
    state: dict[str, object]
    identity: dict[str, object]
    token: str

    def validate_execution(
        self, command: Sequence[str], *, environment: Mapping[str, str], cwd: Path | str | None,
        quiet_seconds: float, max_seconds: float,
    ) -> None:
        """Refuse an effective child context not admitted by the existing owner."""
        if (self.identity["command"] != list(command)
                or self.identity["environment"] != _environment_identity(
                    _semantic_environment(command, environment), cwd,
                )
                or self.identity["limits"] != [quiet_seconds, max_seconds]):
            raise ValueError("observed child execution context differs from native admission")

    def preserved_run_ids(self, observed_root: Path, label: str) -> frozenset[str]:
        """Protect bounded referenced native runs from ordinary observer pruning."""
        return preserved_run_ids(self.root, observed_root, label)

    @contextmanager
    def protected_run_ids(self, observed_root: Path, label: str) -> Iterator[frozenset[str]]:
        """Keep this owner's reference protection stable until retention completes."""
        with protected_run_ids(self.root, observed_root, label) as preserved:
            yield preserved

    def record(self, observed_root: Path, label: str, run_id: str) -> None:
        """Remember the existing observer's run, never a caller-provided outcome."""
        pointer = {"root": str(observed_root.resolve()), "label": label, "run_id": run_id}
        native = _native(self.root, pointer)
        if native["progress_identity"] != self.identity or native.get("owner_pid") != os.getpid():
            raise ValueError("native publication does not belong to this admission owner")
        key = _command_key(self.identity["command"])
        with _locked(self.root) as (path, state):
            if state is None or state["primary_task_id"] != self.state["primary_task_id"]:
                raise ValueError("primary changed before native publication")
            reservations = cast(dict[str, object], state.get("reservations", {}))
            if reservations.get(key) != {"owner_pid": os.getpid(), "token": self.token}:
                raise ValueError("native publication has no matching owned reservation")
            observations = cast(dict[str, object], state["observations"])
            if key not in observations and len(observations) >= _MAX_COMMANDS:
                raise ValueError("primary has reached the bounded command inventory")
            observations[key] = pointer
            del reservations[key]
            _atomic_write_json(path, state)


def _release(root: Path, key: str, token: str) -> None:
    with _locked(root) as (path, state):
        if state is None:
            return
        reservations = cast(dict[str, object], state.get("reservations", {}))
        reservation = reservations.get(key)
        if isinstance(reservation, dict) and reservation.get("token") == token:
            del reservations[key]
            _atomic_write_json(path, state)


def _retire_superseded_observation(
    root: Path, observations: dict[str, object], reservations: Mapping[str, object],
    identity: Mapping[str, object],
) -> tuple[str, str] | None:
    """Release one obsolete pointer without granting failure cleanup authority."""
    for key, value in sorted(observations.items()):
        if key in reservations:
            continue
        try:
            native = _native(root, cast(dict[str, str], value))
        except (OSError, ValueError):
            # Missing, malformed or inaccessible evidence cannot authorize retirement.
            continue
        previous = cast(dict[str, object], native["progress_identity"])
        owner_pid = native.get("owner_pid")
        if _command_key(previous["command"]) != key:
            continue
        if (native["state"] == "passed"
                and any(previous[field] != identity[field] for field in ("source", "environment"))):
            reason = "superseded-native-success"
        elif (native["state"] in {"failed", "timed_out"}
                and previous["source"] != identity["source"]
                and type(owner_pid) is int and owner_pid > 0
                and not _pid_alive(owner_pid)):
            reason = "superseded-native-failure-source"
        else:
            continue
        del observations[key]
        return key, reason
    return None


def _read_only_capacity_diagnostic(command: Sequence[str]) -> bool:
    """Recognize only fixed read-only disk inspection, never cleanup execution."""
    command = _canonical_native_command(command)
    if not command or Path(command[0]).name != "make":
        return False
    arguments = [arg for arg in command[1:] if arg != "--no-print-directory"]
    if arguments == ["disk-check"]:
        return True
    if len(arguments) != 4 or arguments[0] != "disk-cleanup-preflight":
        return False
    if any("=" not in arg for arg in arguments[1:]):
        return False
    settings = dict(arg.split("=", 1) for arg in arguments[1:])
    if (set(settings) != {"DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY", "DISK_CLEANUP_PREFLIGHT_DRY_RUN",
                         "DISK_CLEANUP_RECEIPT_GRACE_SECONDS"}
            or settings["DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY"] != "0"
            or settings["DISK_CLEANUP_PREFLIGHT_DRY_RUN"] != "1"):
        return False
    grace = settings["DISK_CLEANUP_RECEIPT_GRACE_SECONDS"]
    return grace.isascii() and grace.isdecimal() and len(grace) <= 10 and int(grace) >= 1800


def _bounded_status_wait(command: Sequence[str], max_seconds: float) -> bool:
    """Exempt only the existing finite, supervised external-state observer."""
    arguments = [arg for arg in command[1:] if arg != "--no-print-directory"]
    if not arguments or arguments[0] != "status-heartbeat":
        return False
    settings: dict[str, str] = {}
    for argument in arguments[1:]:
        key, separator, value = argument.partition("=")
        if not separator or key not in _STATUS_WAIT_VARIABLES or key in settings:
            return False
        settings[key] = value
    for key in ("INTERVAL", "COUNT"):
        value = settings.get(key, "")
        if not value.isascii() or not value.isdecimal() or len(value) > 10:
            return False
    if (isinstance(max_seconds, bool) or not math.isfinite(max_seconds) or max_seconds <= 0
            or any(settings.get(key, "0") not in {"0", "1"} for key in (
                "STATUS_HEARTBEAT_VALIDATE_ONLY", "STATUS_HEARTBEAT_STOP_ON_TERMINAL",
                "PIPELINE_STATUS_VALIDATE_ONLY",
            ))):
        return False
    count, interval = int(settings["COUNT"]), int(settings["INTERVAL"])
    return 1 <= count <= 24 and interval >= 300 and (count - 1) * interval <= max_seconds


@contextmanager
def command_admission(
    root: Path, command: Sequence[str], *, environment: Mapping[str, str],
    quiet_seconds: float, max_seconds: float, primary_task_id: str | None = None,
    task_id: str | None = None, supporting_tasks: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> Iterator[Admission | None]:
    """Admit scoped execution; diagnostics and external-state waits stay available."""
    semantic_command = _canonical_native_command(command)
    targets = [arg for arg in semantic_command[1:] if "=" not in arg and arg != "--no-print-directory"]
    if _read_only_capacity_diagnostic(semantic_command) or (
            semantic_command and Path(semantic_command[0]).name == "make" and len(targets) == 1
            and (targets[0] in _DIAGNOSTICS or _bounded_status_wait(semantic_command, max_seconds))
            and not any(arg.split("=", 1)[0] in {"UV", "PYTHON", "SHELL", "MAKEFILES", "MAKEFLAGS"}
                        for arg in semantic_command[1:])
            and all(not arg.startswith("-") or arg == "--no-print-directory" for arg in semantic_command[1:])):
        yield None
        return
    root = root.resolve()
    identity = _identity(root, command, environment, quiet_seconds, max_seconds,
                         **({"cwd": cwd} if cwd is not None else {}))
    key = _command_key(list(command))
    token = uuid.uuid4().hex
    with _locked(root) as (path, state):
        primary = primary_task_id if primary_task_id is not None else str((state or {}).get("primary_task_id", ""))
        state = _select(root, state, primary)
        supporting = _supports(dict(supporting_tasks) if supporting_tasks is not None
                               else state["supporting_tasks"], primary)
        active = task_inventory((root / "TASKS.md").read_text(encoding="utf-8"))["open_task_ids"]
        if supporting_tasks is not None and any(task not in active for task in supporting):
            raise ValueError("support must name canonical active tasks")
        supporting = {task: reason for task, reason in supporting.items() if task in active}
        if (task_id or primary) not in {primary, *supporting}:
            raise ValueError("execution must belong to primary or declared direct support")
        state["supporting_tasks"] = supporting
        observations = cast(dict[str, object], state["observations"])
        reservations = cast(dict[str, object], state.setdefault("reservations", {}))
        reservation = reservations.get(key)
        if isinstance(reservation, dict) and _pid_alive(reservation.get("owner_pid")):
            raise ValueError("command is already reserved by an active execution owner")
        if key in observations:
            pointer = cast(dict[str, str], observations[key])
            native = _native(root, pointer)
            native_identity = cast(dict[str, object], native["progress_identity"])
            if _command_key(native_identity["command"]) != key:
                raise ValueError("native command differs from the persisted input")
            unchanged = all(native_identity[field] == identity[field]
                            for field in ("root", "command", "source", "environment"))
            if unchanged and native["state"] in {"failed", "timed_out"}:
                raise ValueError(
                    f"unchanged failed inputs: {pointer['label']}/{pointer['run_id']}; change the failing input"
                )
            if native["state"] in {"starting", "running"} and _pid_alive(native.get("owner_pid")):
                raise ValueError("prior native command still has an active owner")
        retired: list[tuple[str, str]] = []
        while len(set(observations) | set(reservations) | {key}) > _MAX_COMMANDS:
            obsolete = _retire_superseded_observation(root, observations, reservations, identity)
            if obsolete is None:
                raise ValueError("primary has reached the bounded command inventory")
            retired.append(obsolete)
        reservations[key] = {"owner_pid": os.getpid(), "token": token}
        _atomic_write_json(path, state)
        for retired_key, reason in retired:
            print(f"ITEM_PROGRESS_RETENTION_RELEASED command={retired_key} reason={reason} "
                  "receipt_deleted=false release_ready=false gate_evidence=false", flush=True)
        admission = Admission(root, path, state, identity, token)
    try:
        yield admission
    finally:
        _release(root, key, token)



def prepare_runtime_focus(root: Path, task_id: str = "") -> dict[str, object]:
    """Require/revalidate existing focus before the bounded runtime can start."""
    root = root.resolve()
    with _locked(root) as (path, state):
        primary = str(state["primary_task_id"]) if state is not None else task_id
        if not primary:
            raise ValueError(
                "runtime admission requires native focus or explicit WORK_ITEM_ID; use "
                "make work-item WORK_ITEM_ACTION=focus WORK_ITEM_ID=<active-task> WORK_ITEM_VALIDATE_ONLY=0"
            )
        state = _select(root, state, primary)
        supporting = _supports(state["supporting_tasks"], primary)
        if task_id and task_id not in {primary, *supporting}:
            raise ValueError("runtime task must be the current primary or declared direct support")
        _atomic_write_json(path, state)
        return state


def _result_guidance(outcome: str) -> None:
    """Teach the actual administrative result without interpreting native proofs."""
    actions = {
        "planned": (
            "Inspect work-item status in mode0; no focus was selected, task eligibility was not checked "
            "and no runtime admission ran. This plan is not acceptance."
        ),
        "uninitialized": (
            "Select one open canonical active task with work-item focus/mode0; inspect "
            "active-work-status for capacity and ownership before bounded acceptance."
        ),
        "observed": (
            "Inspect exact linked receipts with observed-status and actual owners with active-work-status; "
            "pointers do not prove live workers or acceptance. Repair the actual blocked or changed input "
            "before retrying its owner."
        ),
        "updated": (
            "This is an administrative declaration; no runtime admission ran. Inspect active-work-status "
            "for capacity and ownership, then use the existing bounded acceptance owner."
        ),
        "prepared": (
            "Only administrative focus was prepared; no runtime admission ran. Inspect active-work-status "
            "for capacity and ownership before the existing bounded runtime owner."
        ),
        "refused": (
            "Preserve the native reason and reconcile existing state before retry; repair the actual "
            "changed input, and never delete native state or locks or manufacture observation JSON."
        ),
    }
    status = 2 if outcome == "refused" else 0
    print(
        f"AGENT_NEXT target=work-item outcome={outcome} native_status={status} "
        f"gate_evidence=false release_ready=false next_action={actions[outcome]}",
        file=sys.stderr, flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Expose bounded administrative controls without executing a second runner."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "focus", "support", "prepare-runtime"))
    parser.add_argument("--task", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--validate-only", choices=("0", "1"), default="0")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 2:
            _result_guidance("refused")
        raise
    try:
        if args.action in {"focus", "support"} and not args.task.strip():
            raise ValueError("focus/support requires one canonical task ID")
        if args.action == "support" and not args.reason.strip():
            raise ValueError("support requires a direct acceptance reason")
        if args.validate_only == "1":
            record: dict[str, object] = {
                "kind": "work_item_plan", "action": args.action, "task": args.task,
                "release_ready": False, "gate_evidence": False,
            }
        elif args.action == "prepare-runtime":
            record = prepare_runtime_focus(Path.cwd(), args.task)
        elif args.action == "focus":
            record = focus_item(Path.cwd(), args.task)
        elif args.action == "support":
            record = support_item(Path.cwd(), args.task, args.reason)
        else:
            record = progress_status(Path.cwd()) or {
                "kind": "item_progress", "state": "uninitialized",
                "release_ready": False, "gate_evidence": False,
            }
        print(json.dumps(record, sort_keys=True))
        if args.validate_only == "1":
            outcome = "planned"
        elif args.action == "status":
            outcome = "uninitialized" if record.get("state") == "uninitialized" else "observed"
        else:
            outcome = "prepared" if args.action == "prepare-runtime" else "updated"
        _result_guidance(outcome)
        return 0
    except (OSError, ValueError) as exc:
        print(f"work-item: {exc}", file=sys.stderr)
        _result_guidance("refused")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
