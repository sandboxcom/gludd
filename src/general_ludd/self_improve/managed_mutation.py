"""Atomic, approval-bound mutation contracts for managed self-improvement."""

from __future__ import annotations

import difflib
import os
import tempfile
from enum import StrEnum
from pathlib import Path

from general_ludd.self_improve.codex_comparison import ProposalManifest


class ModelPlanFailure(StrEnum):
    """Secret-safe terminal states for bounded model selection."""

    EXHAUSTED = "model_plan_exhausted"


class ModelPlanError(RuntimeError):
    """Typed failure raised before a candidate can begin an attempt."""

    def __init__(self, failure: ModelPlanFailure) -> None:
        """Retain only a stable category and operator-safe message."""
        if failure is not ModelPlanFailure.EXHAUSTED:
            raise ValueError("unsupported typed model plan failure")
        super().__init__("managed model candidate plan failed: model_plan_exhausted")
        self.failure = failure


class SelfImprovePolicyViolation(ValueError):
    """Secret-safe rejection raised when project privacy cannot be proven."""

    def __init__(self) -> None:
        """Use one fixed message so paths, source, and parser errors never escape."""
        super().__init__("self-improvement blocked by project privacy policy")


def apply_proposal(repo_root: Path, proposal: ProposalManifest) -> int:
    """Transactionally apply confined exact patches and return changed line count."""
    proposal.validate_paths(repo_root)
    originals: dict[Path, tuple[bool, str, int]] = {}
    planned: dict[Path, tuple[bool, str]] = {}
    for edit in proposal.edits:
        destination = repo_root / edit.path
        if destination.is_symlink():
            raise ValueError(f"proposal path must not be a symlink: {edit.path}")
        if destination not in originals:
            exists = destination.is_file()
            before = destination.read_text(encoding="utf-8") if exists else ""
            mode = destination.stat().st_mode if exists else 0o644
            originals[destination] = (exists, before, mode)
            planned[destination] = (exists, before)
        exists, current = planned[destination]
        if edit.operation == "replace":
            if proposal.schema_version == 2:
                if not exists or current != edit.old_text:
                    raise ValueError(
                        f"replace old_text must equal the complete trusted snapshot: {edit.path}"
                    )
                planned[destination] = (True, edit.new_text)
            else:
                if not exists or current.count(edit.old_text) != 1:
                    raise ValueError(
                        f"replace old_text must occur exactly once: {edit.path}"
                    )
                planned[destination] = (
                    True,
                    current.replace(edit.old_text, edit.new_text, 1),
                )
        elif edit.operation == "create":
            if exists:
                raise ValueError(f"create target already exists: {edit.path}")
            planned[destination] = (True, edit.new_text)
        elif edit.operation == "delete":
            if not exists or current != edit.old_text:
                raise ValueError(
                    f"delete old_text must equal the complete file: {edit.path}"
                )
            planned[destination] = (False, "")
        else:
            raise ValueError(f"unsupported edit operation: {edit.operation}")

    changed_lines = sum(
        _line_delta(originals[path][1], final_text)
        for path, (_exists, final_text) in planned.items()
    )
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    try:
        for destination, (final_exists, final_text) in planned.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            original_exists, original_text, original_mode = originals[destination]
            if original_exists:
                backups[destination] = _write_atomic_temp(
                    destination,
                    original_text,
                    original_mode,
                    ".self-improve-backup",
                )
            if final_exists:
                staged[destination] = _write_atomic_temp(
                    destination,
                    final_text,
                    original_mode,
                    ".self-improve-tmp",
                )
        try:
            for destination, (final_exists, _final_text) in planned.items():
                if final_exists:
                    os.replace(staged[destination], destination)
                    staged.pop(destination)
                else:
                    destination.unlink()
        except BaseException:
            for destination, (original_exists, _text, _mode) in originals.items():
                if original_exists:
                    backup = backups.get(destination)
                    if backup is not None and backup.exists():
                        os.replace(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            raise
        return changed_lines
    finally:
        for temporary in (*staged.values(), *backups.values()):
            temporary.unlink(missing_ok=True)


def _write_atomic_temp(
    destination: Path,
    content: str,
    mode: int,
    suffix: str,
) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=".gludd-self-improve-",
        suffix=suffix,
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    return temporary


def _line_delta(before: str, after: str) -> int:
    delta = 0
    for line in difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        lineterm="",
    ):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            delta += 1
    return delta


__all__ = [
    "ModelPlanError",
    "ModelPlanFailure",
    "SelfImprovePolicyViolation",
    "apply_proposal",
]
