#!/usr/bin/env python3
"""Produce immutable native inputs for the existing reviewed-head receipt owner.

Approval is an explicit trusted reviewer instruction. Git supplies every source,
patch and application fact; the existing observed-command and receipt-auth owners
supply focused execution provenance. None of these inputs grants release authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    import build_reviewed_head_integration_receipt as receipt_owner
    import gate_status_attestation as gate_owner
    import stream_command as observed_owner
    from ci_receipt_auth import (
        ReceiptAuthContext,
        authenticate_receipt_envelope,
        create_receipt_envelope,
        load_gate_receipt_auth_context,
        parse_revoked_signers,
    )
elif __package__:
    from scripts import build_reviewed_head_integration_receipt as receipt_owner
    from scripts import gate_status_attestation as gate_owner
    from scripts import stream_command as observed_owner
    from scripts.ci_receipt_auth import (
        ReceiptAuthContext,
        authenticate_receipt_envelope,
        create_receipt_envelope,
        load_gate_receipt_auth_context,
        parse_revoked_signers,
    )
else:
    import build_reviewed_head_integration_receipt as receipt_owner
    import gate_status_attestation as gate_owner
    import stream_command as observed_owner
    from ci_receipt_auth import (
        ReceiptAuthContext,
        authenticate_receipt_envelope,
        create_receipt_envelope,
        load_gate_receipt_auth_context,
        parse_revoked_signers,
    )

from general_ludd.git_release.reviewed_head_integration import (
    AppliedHeadEvidence,
    HeadApplicationMode,
    PrerequisiteAncestor,
    PrerequisiteResolution,
    ReviewedHead,
    build_reviewed_head_integration_plan,
)


class _ShardOwner(Protocol):
    SHARDS: Mapping[str, object]

    def _validate_attestation(
        self, path: Path, payload: dict[str, object], *, sha: str, lane: str,
    ) -> tuple[set[str], dict[str, object], list[str]]: ...


# The hosted/local verifier's executable imports have their own CLI layout.
# Use its actual validator read-only without importing its runner into typing.
shard_owner = cast(
    _ShardOwner, import_module("scripts.verify_dual_track_ci" if __package__ else "verify_dual_track_ci"),
)


class _RuntimeOwner(Protocol):
    def file_sha256(self, path: Path) -> str: ...

    def _installed_distributions_sha256(self) -> str: ...

    def _pytest_plugin_identity(self) -> dict[str, object]: ...

    def normalize_junit_outcomes(self, path: Path) -> dict[str, object]: ...


runtime_owner = cast(
    _RuntimeOwner, import_module("scripts.ci_batch_receipts" if __package__ else "ci_batch_receipts"),
)
_NATIVE_EXECUTION = observed_owner.stream_command


class _ResourceOwner(Protocol):
    def resource_root(self, root: Path | str | None = None) -> Path: ...


resource_owner = cast(
    _ResourceOwner, import_module("scripts.resource_arbiter" if __package__ else "resource_arbiter"),
)


class _ProgressOwner(Protocol):
    def command_admission(
        self, root: Path, command: Sequence[str], *, environment: Mapping[str, str],
        quiet_seconds: float, max_seconds: float, task_id: str | None = None,
        primary_task_id: str | None = None, cwd: Path | str | None = None,
    ) -> AbstractContextManager[observed_owner._ProgressAdmission | None]: ...


class _TraceOwner(Protocol):
    def write_event(
        self, event: str, *, nodeid: str | None = None, extra: dict[str, object] | None = None,
    ) -> None: ...


class _PluginManager(Protocol):
    def list_name_plugin(self) -> list[tuple[str, object | None]]: ...


progress_owner = cast(_ProgressOwner, import_module("scripts.item_progress" if __package__ else "item_progress"))
trace_owner = cast(_TraceOwner, import_module("scripts.xdist_trace_plugin" if __package__ else "xdist_trace_plugin"))

_REF = re.compile(r"refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]{0,230}\Z")
_TEST = re.compile(r"tests/(?:unit|integration)/[A-Za-z0-9_./-]+\.py\Z")
_REVIEWER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@-]{0,127}\Z")
_LABEL = "reviewed-head-focus"
_OWNER_KIND = "gludd_native_reviewed_head_inputs"
_FOCUS_KIND = "gludd_native_reviewed_head_focus"
_KEY_VARIABLE = "GLUDD_GATE_KEY_PATH"


def _encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object(value: object, keys: set[str], label: str) -> Mapping[str, object]:
    result = receipt_owner._mapping(value, label)
    if set(result) != keys:
        raise ValueError(f"{label} contains missing or unsupported fields")
    return result


def _read(path: Path, label: str) -> Mapping[str, object]:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    raw, _ = receipt_owner._read_bounded_json(path, label)
    return receipt_owner._mapping(raw, label)


def read_intent(path: Path) -> Mapping[str, object]:
    """Read bounded, unambiguous explicit reviewer intent without echoing it."""
    return _read(path, "reviewer intent")


def _ref(root: Path, value: object) -> str:
    ref = receipt_owner._string(value, "named Git ref")
    if (_REF.fullmatch(ref) is None or any(part in ref for part in ("..", "//", "@{"))
            or ref.endswith(("/", ".", ".lock"))):
        raise ValueError("an actual named source or retained base ref is required")
    return receipt_owner._git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode("ascii").strip()


def _clean_identity(root: Path) -> tuple[str, str]:
    if receipt_owner._git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("native inputs require an exact clean repository")
    sha = receipt_owner._git(root, "rev-parse", "HEAD").decode("ascii").strip()
    state = gate_owner.repository_state_id(root, source="worktree")
    if state != gate_owner.repository_state_id(root, source="index"):
        raise ValueError("native worktree and index identities disagree")
    return sha, state


def _ancestor(root: Path, ancestor: str, tip: str) -> bool:
    return receipt_owner._git(root, "merge-base", ancestor, tip).decode("ascii").strip() == ancestor


def _test_files(value: object, root: Path, tip: str) -> tuple[str, ...]:
    files = receipt_owner._strings(value, "focused test files")
    if not files or len(files) > 128 or len(set(files)) != len(files):
        raise ValueError("focused test files must be bounded, nonempty and distinct")
    for path in files:
        if _TEST.fullmatch(path) is None or ".." in Path(path).parts or "//" in path:
            raise ValueError("focused test file is not a supported repository path")
        if not receipt_owner._git(root, "ls-tree", "--name-only", tip, "--", path).strip():
            raise ValueError("focused test file is absent from the candidate")
    return files


def prepare_inputs(intent: object, *, repo_root: Path) -> dict[str, object]:
    """Read-only admission deriving a continuous genuine Git application chain."""
    spec = _object(intent, {"schema_version", "integration_base_ref", "candidate_ref", "heads"}, "reviewer intent")
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("reviewer intent schema version is unsupported")
    root = repo_root.resolve()
    tip, state = _clean_identity(root)
    base = _ref(root, spec["integration_base_ref"])
    if _ref(root, spec["candidate_ref"]) != tip or not _ancestor(root, base, tip):
        raise ValueError("candidate ref or integration base does not match native HEAD")
    raw_heads = receipt_owner._sequence(spec["heads"], "reviewed heads")
    if not raw_heads or len(raw_heads) > 32:
        raise ValueError("reviewed heads must be bounded and nonempty")
    chain = receipt_owner._git(
        root, "rev-list", "--first-parent", "--reverse", f"{base}..{tip}",
    ).decode("ascii").split()
    if len(chain) != len(raw_heads):
        raise ValueError("native integration chain requires exactly one application per reviewed head")
    reviews: list[dict[str, object]] = []
    manifests: list[dict[str, object]] = []
    typed_heads: list[ReviewedHead] = []
    sources: list[str] = []
    focused_files: list[str] = []
    before = base
    for ordinal, (raw, after) in enumerate(zip(raw_heads, chain, strict=True), start=1):
        head = _object(
            raw, {"source_ref", "reviewed_base_ref", "reviewer", "verdict", "focused_test_files"},
            "reviewed source intent",
        )
        reviewer = receipt_owner._string(head["reviewer"], "trusted reviewer")
        if _REVIEWER.fullmatch(reviewer) is None or head["verdict"] != "approved":
            raise ValueError("explicit approved trusted reviewer intent is required")
        source_ref = receipt_owner._string(head["source_ref"], "source ref")
        source = _ref(root, source_ref)
        reviewed_base = _ref(root, head["reviewed_base_ref"])
        if source in {after, before, base, *sources} or source in chain:
            raise ValueError("original source must be distinct from every application SHA")
        if not _ancestor(root, reviewed_base, source) or reviewed_base == source:
            raise ValueError("reviewed base must be an original source ancestor")
        parents = receipt_owner._commit_parents(root, after)
        if parents == (before,):
            mode = HeadApplicationMode.CHERRY_PICK
            if (receipt_owner._commit_parents(root, source) != (reviewed_base,)
                    or receipt_owner._patch_id(root, source) != receipt_owner._patch_id(root, after)):
                raise ValueError("native cherry-pick patch or reviewed source base differs")
        elif parents == (before, source):
            mode = HeadApplicationMode.MERGE_TWO_PARENT
            if receipt_owner._git(
                root, "show", "--remerge-diff", "--format=", "--binary", "--full-index",
                "--no-ext-diff", "--no-textconv", after, "--",
            ).strip():
                raise ValueError("native merge contains an unreviewed resolution or unrelated change")
        else:
            raise ValueError("native chain has unsupported one-head parent topology")
        prerequisites: list[PrerequisiteAncestor] = []
        if reviewed_base != base:
            if _ancestor(root, reviewed_base, base):
                resolution = PrerequisiteResolution.ALREADY_REACHABLE
            elif reviewed_base in sources:
                resolution = PrerequisiteResolution.REQUIRED_HEAD
            else:
                raise ValueError("original reviewed base has no named prerequisite resolution")
            prerequisites.append(PrerequisiteAncestor(reviewed_base, resolution))
        files = _test_files(head["focused_test_files"], root, tip)
        command_ids = tuple(f"test-files:{path}" for path in files)
        patch = receipt_owner._git(root, "diff", "--binary", "--full-index", reviewed_base, source, "--")
        changed = receipt_owner._git(root, "diff", "--name-only", "-z", reviewed_base, source, "--")
        review: dict[str, object] = {
            "schema_version": 1, "source_ref": source_ref, "source_sha": source,
            "reviewed_base_sha": reviewed_base, "reviewer": reviewer, "verdict": "approved",
            "focused_validation_ids": list(command_ids),
            "native": {"diff_sha256": _digest(patch),
                       "changed_files": [os.fsdecode(path) for path in changed.split(b"\0") if path],
                       "source_tree": receipt_owner._git(
                           root, "rev-parse", f"{source}^{{tree}}",
                       ).decode("ascii").strip()},
        }
        digest = _digest(_encoded(review))
        typed = ReviewedHead(source_ref, source, reviewed_base, digest, command_ids, mode, tuple(prerequisites))
        AppliedHeadEvidence(ordinal, source_ref, source, digest, mode, before, after, parents)
        typed_heads.append(typed)
        reviews.append(review)
        manifests.append({
            "source_ref": source_ref, "source_sha": source, "reviewed_base_sha": reviewed_base,
            "review_receipt_file": f"review-{ordinal:02d}.json", "focused_validation_ids": list(command_ids),
            "application_mode": mode.value, "prerequisite_ancestry": [
                {"ancestor_sha": item.ancestor_sha, "resolution": item.resolution.value}
                for item in typed.prerequisite_ancestry
            ], "before_sha": before, "after_sha": after, "parent_shas": list(parents),
        })
        focused_files.extend(path for path in files if path not in focused_files)
        sources.append(source)
        before = after
    plan = build_reviewed_head_integration_plan(base_sha=base, heads=tuple(typed_heads), exact_gate_id="gate")
    if len(plan.focused_validation_ids) > 128:
        raise ValueError("focused union exceeds the existing receipt schema bound")
    if _clean_identity(root) != (tip, state):
        raise ValueError("native source changed during admission")
    return {
        "schema_version": 1, "kind": _OWNER_KIND, "intent": dict(spec),
        "base_sha": base, "candidate_sha": tip, "repository_state_id": state,
        "heads": manifests, "reviews": reviews,
        "focused_validation_ids": list(plan.focused_validation_ids), "focused_test_files": focused_files,
        "focused_command": [
            "make", "--no-print-directory", "test-files", "TESTFILES=" + " ".join(focused_files),
            'PYTEST_ARGS=-W error --junitxml="$${OBSERVED_JUNIT_PATH}" '
            "-p scripts.xdist_trace_plugin -p scripts.reviewed_head_inputs",
            "NPROC=1", "GLUDD_XDIST_WORKERS=1",
        ],
    }


def _external_bundle(bundle: Path, repo_root: Path) -> Path:
    receipt_owner._require_external_path(bundle, repo_root, "native input bundle")
    if any(path.is_symlink() for path in (bundle, *bundle.absolute().parents) if path != Path("/tmp")):
        raise ValueError("native input bundle must not be a symbolic link")
    resolved = bundle.resolve()
    if resolved == Path(resolved.anchor) or not resolved.name:
        raise ValueError("native input bundle requires one owned directory")
    return resolved


def _publish(path: Path, payload: Mapping[str, object]) -> None:
    """Reuse the native fsynced no-overwrite writer, accepting exact owner retries."""
    if path.is_symlink():
        raise ValueError("native artifact output must not be a symbolic link")
    if path.exists():
        if not path.is_file() or path.read_bytes() != _encoded(payload):
            raise ValueError("native artifact already belongs to another input or owner")
        return
    try:
        observed_owner._atomic_write_json(path, payload, exclusive=True)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != _encoded(payload):
            raise ValueError("native artifact publication lost its immutable owner claim") from None


def record_inputs(prepared: Mapping[str, object], bundle: Path, *, repo_root: Path) -> None:
    """Publish reviewed JSON first, then its immutable admission owner marker."""
    current = prepare_inputs(prepared.get("intent"), repo_root=repo_root)
    if _encoded(current) != _encoded(prepared):
        raise ValueError("prepared native facts differ from the current admission")
    destination = _external_bundle(bundle, repo_root)
    reviews = receipt_owner._sequence(current["reviews"], "native reviews")
    expected = {"inputs.json", *(f"review-{index:02d}.json" for index in range(1, len(reviews) + 1))}
    if destination.exists():
        if not destination.is_dir() or not (destination / "inputs.json").is_file():
            raise ValueError("native bundle already belongs to an unrecorded owner")
        existing = _read(destination / "inputs.json", "native input owner")
        if _encoded(existing) != _encoded(current):
            raise ValueError("native bundle already belongs to another reviewer or candidate")
        allowed = expected | {
            "observed", "focused.json", "focused-auth.json", "focused-junit.xml", "manifest.json", "gate-binding.json",
        }
        if any(path.name not in allowed for path in destination.iterdir()):
            raise ValueError("native bundle contains an unowned artifact")
    else:
        destination.mkdir(parents=True, mode=0o700)
    for index, raw in enumerate(reviews, start=1):
        _publish(destination / f"review-{index:02d}.json", receipt_owner._mapping(raw, "native review"))
    _publish(destination / "inputs.json", current)


def _load_inputs(repo_root: Path, bundle: Path) -> tuple[Path, Mapping[str, object]]:
    destination = _external_bundle(bundle, repo_root)
    recorded = _read(destination / "inputs.json", "native input owner")
    current = prepare_inputs(recorded.get("intent"), repo_root=repo_root)
    if _encoded(current) != _encoded(recorded):
        raise ValueError("recorded native source, topology or reviewer intent changed")
    for index, review in enumerate(receipt_owner._sequence(current["reviews"], "native reviews"), start=1):
        if _encoded(_read(destination / f"review-{index:02d}.json", "native review")) != _encoded(review):
            raise ValueError("recorded native reviewer provenance changed")
    return destination, recorded


def _auth_context(key_path: Path, epoch: int | None = None) -> ReceiptAuthContext:
    # Consumer/capture admission must never create a signing key as a side effect.
    if not key_path.is_file() or key_path.is_symlink():
        raise ValueError("native receipt authentication owner is unavailable")
    return load_gate_receipt_auth_context(
        key_path, issued_at=int(time.time()) if epoch is None else epoch,
        revoked_signers=parse_revoked_signers(os.environ.get("GLUDD_RECEIPT_REVOKED_SIGNERS", "")),
    )


def _focus_directory(root: Path) -> Path:
    directory = root.resolve() / ".gate-logs/observed" / _LABEL
    if any(path.is_symlink() or (path.exists() and not path.is_dir())
           for path in (directory.parent.parent, directory.parent, directory)):
        raise ValueError("focused native observation directory is not owned")
    return directory


def _focus_paths(root: Path, run_id: str) -> tuple[Path, Path, Path, Path]:
    if re.fullmatch(r"focus-[0-9a-f]{64}", run_id) is None:
        raise ValueError("focused native run identity is invalid")
    directory = _focus_directory(root)
    return (directory / f"{run_id}.json", directory / f"{run_id}.log",
            directory / f"{run_id}.pytest.jsonl", directory / f"{run_id}.junit.xml")


def _execution_environment(root: Path) -> dict[str, str]:
    # External artifact choices are not test inputs and cannot clear a failure.
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("GLUDD_REVIEWED_HEAD_INPUTS_", "REVIEWED_HEAD_INPUTS_"))
    }
    producer_root = Path(__file__).resolve().parents[1]
    python_paths = [str(producer_root), str(producer_root / "scripts"), str(producer_root / "src")]
    python_paths.extend(path for path in environment.get("PYTHONPATH", "").split(os.pathsep) if path)
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(python_paths))
    environment["GLUDD_REVIEWED_HEAD_FOCUS_ROOT"] = str(root.resolve())
    return environment


def _focus_runtime(manager: _PluginManager) -> dict[str, object]:
    """Fingerprint the actual pytest child, including its enabled plugin manager."""
    return {
        "python": sys.version, "executable_sha256": runtime_owner.file_sha256(Path(sys.executable)),
        "installed_distributions_sha256": runtime_owner._installed_distributions_sha256(),
        "plugins": runtime_owner._pytest_plugin_identity(),
        "loaded_plugins": sorted(name for name, plugin in manager.list_name_plugin() if plugin is not None),
        "producer_sha256": runtime_owner.file_sha256(Path(__file__)),
        "observer_sha256": runtime_owner.file_sha256(Path(__file__).with_name("stream_command.py")),
    }


def _runtime_event(session: pytest.Session, event: str) -> None:
    configured_root = os.environ.get("GLUDD_REVIEWED_HEAD_FOCUS_ROOT")
    if not configured_root or Path.cwd().resolve() != Path(configured_root).resolve():
        return
    root = Path(configured_root).resolve()
    run_id = os.environ.get("GLUDD_XDIST_TRACE_RUN_ID")
    if run_id is None:
        raise ValueError("focused native run identity is required")
    status_path, _, trace_path, _ = _focus_paths(root, run_id)
    if os.environ.get("GLUDD_XDIST_TRACE_LOG") != str(trace_path):
        raise ValueError("focused pytest child has no native trace ownership")
    status = _read(status_path, "focused native live status")
    child_pid = status.get("child_pid")
    if (status.get("state") != "running" or type(child_pid) is not int
            or os.getsid(0) != child_pid or os.getpgid(0) != child_pid):
        raise ValueError("focused runtime is not the existing observer's executing child")
    sha, state = _clean_identity(root)
    trace_owner.write_event(event, extra={
        "candidate_sha": sha, "repository_state_id": state, "native_child_pid": child_pid,
        "runtime": _focus_runtime(cast(_PluginManager, session.config.pluginmanager)),
    })


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    """Append actual executor facts through the existing native pytest trace owner."""
    _runtime_event(session, "FOCUS_RUNTIME")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Bind terminal executor facts after native test execution, without a runner."""
    _runtime_event(session, "FOCUS_RUNTIME_FINISH")


def _native_runtimes(
    trace_path: Path, status: Mapping[str, object], recorded: Mapping[str, object],
) -> list[dict[str, object]]:
    if trace_path.is_symlink() or trace_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("focused native runtime trace is unsafe or exceeds its bound")
    events: list[Mapping[str, object]] = []
    with trace_path.open("rb") as handle:
        for line in handle:
            if len(line) > 1_048_576:
                raise ValueError("focused native runtime trace event exceeds its bound")
            raw: object = json.loads(line, object_pairs_hook=receipt_owner._reject_duplicate_keys)
            event = receipt_owner._mapping(raw, "focused native trace event")
            if event.get("run_id") != status.get("run_id"):
                raise ValueError("focused native runtime trace belongs to another invocation")
            events.append(event)
    executors = {event.get("pid") for event in events if event.get("event") == "START"}
    if not executors or any(type(pid) is not int for pid in executors):
        raise ValueError("focused native runtime trace has no actual test executor")
    result: list[dict[str, object]] = []
    for pid in sorted(cast(set[int], executors)):
        starts = [event for event in events if event.get("pid") == pid and event.get("event") == "FOCUS_RUNTIME"]
        finishes = [event for event in events
                    if event.get("pid") == pid and event.get("event") == "FOCUS_RUNTIME_FINISH"]
        terminal = [event for event in events if event.get("pid") == pid and event.get("event") == "RUN_FINISH"]
        if (len(starts) != 1 or len(finishes) != 1 or len(terminal) != 1 or terminal[0].get("exitstatus") != 0):
            raise ValueError("focused native runtime lacks one completed actual executor")
        for event in (*starts, *finishes):
            if (event.get("candidate_sha") != recorded["candidate_sha"]
                    or event.get("repository_state_id") != recorded["repository_state_id"]
                    or event.get("native_child_pid") != status.get("child_pid")):
                raise ValueError("focused native runtime source or executing child differs")
        if starts[0].get("runtime") != finishes[0].get("runtime"):
            raise ValueError("focused executing child runtime or enabled plugins changed")
        runtime = receipt_owner._mapping(finishes[0].get("runtime"), "focused executing runtime")
        if (runtime.get("producer_sha256") != runtime_owner.file_sha256(Path(__file__))
                or runtime.get("observer_sha256") != runtime_owner.file_sha256(
                    Path(__file__).with_name("stream_command.py"))):
            raise ValueError("focused child provenance adapter implementation changed")
        result.append({"pid": pid, "worker": finishes[0].get("worker"), "runtime": dict(runtime)})
    return result


def _outcomes(path: Path) -> Mapping[str, object]:
    if path.is_symlink():
        raise ValueError("focused native outcomes must not be a symbolic link")
    try:
        outcomes = runtime_owner.normalize_junit_outcomes(path)
    except (OSError, UnicodeError, ValueError):
        raise ValueError("focused native outcomes are unavailable") from None
    counts = receipt_owner._mapping(outcomes.get("counts"), "focused native outcome counts")
    if counts.get("errors") != 0 or counts.get("failures") != 0 or int(str(counts.get("passed", 0))) < 1:
        raise ValueError("focused native outcomes lack an executed passing test")
    return outcomes


def validate_focused(
    *, repo_root: Path, bundle: Path, key_path: Path, verification_epoch: int | None = None,
) -> Mapping[str, object]:
    """Authenticate final-tip evidence and all referenced native status/log bytes."""
    destination, recorded = _load_inputs(repo_root, bundle)
    focused = _read(destination / "focused.json", "focused binding")
    envelope = _read(destination / "focused-auth.json", "focused authentication")
    context = _auth_context(key_path, verification_epoch)
    auth = authenticate_receipt_envelope(
        envelope, expected_kind="pass", expected_action_digest=_digest(_encoded(recorded)),
        expected_content_sha256=_digest(_encoded(focused)), trust_policy=context.trust_policy,
    )
    if not auth.verified:
        raise ValueError("focused native binding authentication failed")
    run_id = receipt_owner._string(focused.get("run_id"), "focused native run ID")
    status_path, log_path, trace_path, junit_path = _focus_paths(repo_root, run_id)
    status = _read(status_path, "focused native status")
    runtimes = _native_runtimes(trace_path, status, recorded)
    if (focused.get("schema_version") != 1 or focused.get("kind") != _FOCUS_KIND
            or focused.get("candidate_sha") != recorded["candidate_sha"]
            or focused.get("repository_state_id") != recorded["repository_state_id"]
            or focused.get("command") != recorded["focused_command"]
            or focused.get("execution_runtimes") != runtimes or focused.get("runtime") != runtimes[0]["runtime"]
            or focused.get("trace_sha256") != runtime_owner.file_sha256(trace_path)
            or focused.get("outcomes") != _outcomes(junit_path)
            or focused.get("junit_file") != str(junit_path)
            or focused.get("junit_sha256") != runtime_owner.file_sha256(junit_path)
            or focused.get("command_ids") != recorded["focused_validation_ids"]
            or focused.get("run_count") != 1 or focused.get("passed") is not True
            or focused.get("run_id") != run_id or focused.get("native_status") != status
            or status.get("schema_version") != 1 or status.get("kind") != "observed_command"
            or status.get("state") != "passed" or type(status.get("exit_code")) is not int
            or status.get("exit_code") != 0 or status.get("run_id") != run_id
            or status.get("label") != _LABEL or status.get("termination_reason") is not None
            or status.get("log_path") != str(log_path)
            or focused.get("status_sha256") != _digest(status_path.read_bytes())
            or log_path.is_symlink() or focused.get("log_sha256") != _digest(log_path.read_bytes())):
        raise ValueError("focused native execution reference changed or is ineligible")
    return focused


def capture_focused(*, repo_root: Path, bundle: Path, key_path: Path) -> Mapping[str, object]:
    """Bind one fixed native focused make invocation; never duplicate its run."""
    destination, recorded = _load_inputs(repo_root, bundle)
    if (destination / "focused.json").exists():
        return validate_focused(repo_root=repo_root, bundle=bundle, key_path=key_path)
    if observed_owner.stream_command is not _NATIVE_EXECUTION:
        raise ValueError("substituted focused execution owner cannot produce native evidence")
    context = _auth_context(key_path)
    _focus_directory(repo_root)
    command = receipt_owner._strings(recorded["focused_command"], "focused native command")
    environment = _execution_environment(repo_root)
    identity = _clean_identity(repo_root)
    task_id = environment.get("WORK_ITEM_ID") or None
    with progress_owner.command_admission(
        repo_root.resolve(), command, environment=environment, quiet_seconds=600, max_seconds=3600,
        task_id=task_id, primary_task_id=task_id, cwd=repo_root.resolve(),
    ) as admission:
        if admission is None:
            raise ValueError("focused execution requires canonical native progress admission")
        run_id = "focus-" + _digest(_encoded({
            "admission": admission.identity, "candidate_sha": recorded["candidate_sha"],
        }))
        status_path, log_path, trace_path, junit_path = _focus_paths(repo_root, run_id)
        if any(path.exists() or path.is_symlink() for path in (status_path, log_path, trace_path, junit_path)):
            raise ValueError(
                "focused invocation is already reserved; preserve its native failure or incomplete binding",
            )
        environment["OBSERVED_JUNIT_PATH"] = str(junit_path)
        returncode = observed_owner.stream_command(
            command, log_path, observed_root=log_path.parent.parent, label=_LABEL, run_id=run_id,
            heartbeat_seconds=30, quiet_seconds=600, max_seconds=3600, retain_runs=1, cwd=repo_root.resolve(),
            progress_admission=admission, environment=environment, pytest_trace=True,
        )
    if returncode != 0 or _clean_identity(repo_root) != identity:
        raise ValueError("focused native execution failed or changed the admitted source")
    status = _read(status_path, "focused native status")
    if status.get("state") != "passed" or status.get("exit_code") != 0:
        raise ValueError("focused native owner did not publish terminal success")
    outcomes = _outcomes(junit_path)
    runtimes = _native_runtimes(trace_path, status, recorded)
    focused: dict[str, object] = {
        "schema_version": 1, "kind": _FOCUS_KIND, "candidate_sha": recorded["candidate_sha"],
        "repository_state_id": recorded["repository_state_id"], "command": list(command),
        "runtime": runtimes[0]["runtime"], "execution_runtimes": runtimes,
        "trace_file": str(trace_path), "trace_sha256": runtime_owner.file_sha256(trace_path),
        "junit_file": str(junit_path), "outcomes": dict(outcomes),
        "junit_sha256": runtime_owner.file_sha256(junit_path),
        "command_ids": recorded["focused_validation_ids"], "run_id": run_id,
        "started_at": status.get("started_at"), "completed_at": status.get("updated_at"),
        "run_count": 1, "passed": True, "native_status": dict(status),
        "status_file": str(status_path), "status_sha256": _digest(status_path.read_bytes()),
        "log_file": str(log_path), "log_sha256": _digest(log_path.read_bytes()),
    }
    envelope = create_receipt_envelope(
        receipt_kind="pass", action_digest=_digest(_encoded(recorded)), content_sha256=_digest(_encoded(focused)),
        signer=context.signer,
    )
    _publish(destination / "focused-auth.json", envelope)
    _publish(destination / "focused.json", focused)
    return validate_focused(repo_root=repo_root, bundle=bundle, key_path=key_path)


def _timestamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(receipt_owner._string(value, "native timestamp").replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("native evidence timestamp is invalid") from None
    if result.tzinfo is None:
        raise ValueError("native evidence timestamp must carry its timezone")
    return result


def finalize_inputs(
    *, repo_root: Path, bundle: Path, gate_attestation: Path, key_path: Path, validate_only: bool = False,
) -> dict[str, object]:
    """Bind the final manifest only after both native shard and outer gate pass."""
    destination, recorded = _load_inputs(repo_root, bundle)
    focused = validate_focused(repo_root=repo_root, bundle=bundle, key_path=key_path)
    root = repo_root.resolve()
    native_gate_path = resource_owner.resource_root(root) / "ci-shards/attestation.json"
    if gate_attestation.resolve() != native_gate_path.resolve():
        raise ValueError("native gate attestation path is not the existing canonical producer output")
    gate = _read(gate_attestation, "native shard attestation")
    sha = receipt_owner._string(recorded["candidate_sha"], "native candidate SHA")
    shards, _, errors = shard_owner._validate_attestation(gate_attestation, dict(gate), sha=sha, lane="local")
    if errors or shards != set(shard_owner.SHARDS):
        raise ValueError("native gate shard attestation is incomplete or ineligible")
    state_path = root / ".gate-logs/gate-background-state.json"
    state = _read(state_path, "native outer gate lifecycle")
    command = receipt_owner._strings(state.get("command"), "native outer gate command")
    if (state.get("schema_version") != 1 or state.get("kind") != "gludd_gate_background"
            or state.get("state") != "finished" or state.get("termination_reason") != "gate-exited"
            or state.get("project_root") != str(root) or command[0] != "make" or "gate" not in command):
        raise ValueError("native outer gate is not the terminal canonical gate")
    run_id = receipt_owner._string(state.get("run_id"), "native gate run ID")
    focused_completed = _timestamp(focused.get("completed_at"))
    gate_started, gate_completed = _timestamp(gate.get("started_at")), _timestamp(gate.get("completed_at"))
    outer_started, outer_completed = _timestamp(state.get("started_at")), _timestamp(state.get("finished_at"))
    if not (focused_completed <= outer_started <= gate_started <= gate_completed <= outer_completed):
        raise ValueError("native focused and final gate evidence is stale, substituted or out of order")
    status_path = root / ".gate-status"
    if status_path.is_symlink():
        raise ValueError("native outer gate status must not be a symbolic link")
    verified = gate_owner.verify_status(
        status_path, state_id=receipt_owner._string(recorded["repository_state_id"], "native state"),
        key=_auth_context(key_path).signer.key,
    )
    if not verified.ok:
        raise ValueError("native outer full-gate attestation is not authenticated terminal success")
    _, authenticated_fields, _ = gate_owner._split_status(status_path.read_text(encoding="utf-8"))
    signed_at = datetime.fromtimestamp(int(authenticated_fields["attestation-epoch"]), tz=outer_started.tzinfo)
    # The native signer timestamps whole seconds; allow that rounding only.
    if not (gate_started.timestamp() - 1 <= signed_at.timestamp() <= outer_completed.timestamp() + 1):
        raise ValueError("native outer gate lifecycle does not match the authenticated gate time")
    manifest: dict[str, object] = {
        "schema_version": 1, "base_sha": recorded["base_sha"], "heads": recorded["heads"],
        "exact_gate_id": "gate:" + run_id,
        "focused_validation": {"tip_sha": sha, "source_shas": [
            receipt_owner._mapping(head, "native reviewed head")["source_sha"]
            for head in receipt_owner._sequence(recorded["heads"], "native reviewed heads")
        ], "command_ids": recorded["focused_validation_ids"],
            "run_count": focused["run_count"], "passed": focused["passed"]},
    }
    receipt_owner.build_receipt_from_manifest(manifest, manifest_dir=destination, gate_attestation=gate, repo_root=root)
    binding: dict[str, object] = {
        "schema_version": 1, "candidate_sha": sha, "gate_id": manifest["exact_gate_id"],
        "native_gate_attestation_file": str(gate_attestation.resolve()),
        "native_gate_attestation_sha256": _digest(gate_attestation.read_bytes()),
        "native_outer_state_sha256": _digest(state_path.read_bytes()),
        "native_outer_status_sha256": _digest(status_path.read_bytes()),
        "focused_binding_sha256": _digest((destination / "focused.json").read_bytes()),
        "manifest_sha256": _digest(_encoded(manifest)), "release_ready": False,
    }
    if not validate_only:
        _publish(destination / "gate-binding.json", binding)
        _publish(destination / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    """Expose dry planning, read-only admission, capture and final binding."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (
        ("action", "admit"), ("intent", ""), ("output", ""), ("repo-root", "."),
        ("gate-attestation", ""), ("validate-only", "1"),
    ):
        parser.add_argument(
            "--" + name,
            default=os.environ.get("GLUDD_REVIEWED_HEAD_INPUTS_" + name.upper().replace("-", "_"), default),
        )
    args = parser.parse_args(argv)
    try:
        if args.action not in {"admit", "record", "focus", "finalize"} or args.validate_only not in {"0", "1"}:
            raise ValueError("native input action or validation policy is unsupported")
        if args.validate_only == "1":
            print("REVIEWED-HEAD-INPUTS-PLAN action=" + args.action
                  + " release_ready=false gate_evidence=false writes=false")
            return 0
        root, bundle = Path(args.repo_root), Path(args.output)
        if not args.output:
            raise ValueError("native input output directory is required")
        if args.action in {"admit", "record"}:
            if not args.intent:
                raise ValueError("explicit reviewer intent path is required")
            prepared = prepare_inputs(read_intent(Path(args.intent)), repo_root=root)
            if args.action == "record":
                record_inputs(prepared, bundle, repo_root=root)
        else:
            key = Path(os.environ.get(_KEY_VARIABLE, str(gate_owner._DEFAULT_KEY_PATH)))
            if args.action == "focus":
                capture_focused(repo_root=root, bundle=bundle, key_path=key)
            else:
                if not args.gate_attestation:
                    raise ValueError("native exact gate attestation path is required")
                finalize_inputs(
                    repo_root=root, bundle=bundle, gate_attestation=Path(args.gate_attestation), key_path=key,
                )
        print("REVIEWED-HEAD-INPUTS-VALID action=" + args.action + " release_ready=false gate_evidence=false")
        return 0
    except (OSError, RuntimeError, TypeError, ValueError):
        print("reviewed-head native inputs refused; required native provenance is unavailable or mismatched",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
