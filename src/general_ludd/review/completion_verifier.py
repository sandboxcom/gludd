"""Evidence-gated completion verifier (Layer 2).

A TaskDecision with decision=="complete" MUST NOT transition a todo to COMPLETE
without machine-checkable proof.  verify_completion() sits between the reviewer
output and the database write — it inspects each evidence_ref, fails-closed on
missing/unverifiable evidence, and downgrades to "needs_more_work" (never raises).

Evidence reference prefixes:
  commit:<sha>       — git cat-file -e presence check (sha validated before subprocess)
  artifact:<path>    — file existence under repo_root (path-traversal blocked)
  test:<node>        — delegated to FeatureVerifier._check_ref
  file:<path>::<sym> — delegated to FeatureVerifier._check_ref
  role:<name>        — delegated to FeatureVerifier._check_ref
  module:<name>      — delegated to FeatureVerifier._check_ref
  molecule:<scen>    — delegated to FeatureVerifier._check_ref

Fail-safe rules:
  * repo_root is None or "." → all refs that need subprocess/filesystem access
    in an unknown CWD are treated as NOT-met (unverifiable ≠ verified)
  * sha outside [0-9a-fA-F]{4,64} → rejected without spawning subprocess
  * artifact with ../ → path-traversal rejected
  * Never raises; all errors downgrade to needs_more_work
"""
from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from general_ludd.quality.feature_verifier import FeatureVerifier
from general_ludd.schemas.task_decision import TaskDecision

if TYPE_CHECKING:
    from general_ludd.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
_UNRESOLVED = frozenset({"", ".", "./"})


def _repo_root_is_unresolved(repo_root: str | None) -> bool:
    if repo_root is None:
        return True
    return repo_root.strip() in _UNRESOLVED


def _check_commit(repo_root: str, sha: str) -> tuple[bool, str]:
    if not _SHA_RE.match(sha):
        return False, f"unsafe sha rejected (not [0-9a-fA-F]{{4,64}}): {sha!r}"
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "cat-file", "-e", sha],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, f"commit {sha} found"
        return False, f"commit {sha} not found (rc={result.returncode})"
    except Exception as exc:
        logger.warning("completion_verifier: git cat-file error for %s: %s", sha, exc)
        return False, f"git error: {exc}"


def _check_artifact(repo_root: str, rel_path: str) -> tuple[bool, str]:
    # Reject empty / dot-only paths BEFORE resolving: ``Path(root) / "" == root``
    # and ``root / "." == root`` both resolve to the repo root itself (which
    # always exists), so a bare ``artifact:`` or ``artifact:.`` ref would falsely
    # pass with no real proof. An empty artifact name is not verifiable evidence.
    if rel_path.strip() in _UNRESOLVED:
        return False, f"empty/degenerate artifact path rejected: {rel_path!r}"
    root = Path(repo_root).resolve()
    try:
        target = (root / rel_path).resolve()
    except Exception as exc:
        return False, f"path resolve error: {exc}"
    try:
        if not target.is_relative_to(root):
            return False, f"artifact path escapes repo_root: {rel_path!r}"
    except ValueError:
        return False, f"artifact path escapes repo_root: {rel_path!r}"
    if target.exists():
        return True, f"artifact found: {rel_path}"
    return False, f"artifact not found: {rel_path}"


def _check_single_ref(
    ref: str,
    repo_root: str | None,
    root_unresolved: bool,
    fv: FeatureVerifier | None,
) -> tuple[bool, str]:
    if ref.startswith("commit:"):
        sha = ref[len("commit:"):]
        if root_unresolved:
            return False, "repo_root unresolved — commit check skipped (fail-closed)"
        assert repo_root is not None
        return _check_commit(repo_root, sha)

    if ref.startswith("artifact:"):
        rel_path = ref[len("artifact:"):]
        if root_unresolved:
            return False, "repo_root unresolved — artifact check skipped (fail-closed)"
        assert repo_root is not None
        return _check_artifact(repo_root, rel_path)

    if ref.startswith(("test:", "file:", "role:", "module:", "molecule:")):
        if fv is None:
            return False, "repo_root unresolved — ref check skipped (fail-closed)"
        met, detail = fv._check_ref(ref)
        return met, detail

    logger.warning("completion_verifier: unknown evidence prefix in %r — failing closed", ref)
    return False, f"unknown evidence prefix: {ref}"


def verify_completion(
    decision: TaskDecision,
    task_return: TaskReturn | None,
    repo_root: str | None,
    *,
    runner: Callable[[str], int] | None = None,
) -> TaskDecision:
    """Gate a complete decision behind machine-checkable evidence.

    Returns the decision unchanged when decision.decision != "complete".
    Downgrades to "needs_more_work" (confidence 0.0) when any evidence ref
    is missing, unverifiable, or repo_root is unresolved. Never raises.
    """
    if decision.decision != "complete":
        return decision

    evidence_refs = decision.evidence_refs
    if not evidence_refs:
        logger.warning(
            "completion_verifier: complete decision %s has no evidence_refs — downgrading",
            decision.return_id,
        )
        return decision.model_copy(update={
            "decision": "needs_more_work",
            "confidence": 0.0,
            "audit_notes": [
                *decision.audit_notes,
                "Downgraded by completion_verifier: no evidence_refs supplied",
            ],
        })

    root_unresolved = _repo_root_is_unresolved(repo_root)
    fv: FeatureVerifier | None = None
    if not root_unresolved:
        assert repo_root is not None
        fv = FeatureVerifier(repo_root, runner=runner)

    unmet: list[str] = []

    for ref in evidence_refs:
        try:
            met, detail = _check_single_ref(ref, repo_root, root_unresolved, fv)
        except Exception as exc:
            logger.warning(
                "completion_verifier: unexpected error checking ref %r: %s", ref, exc
            )
            met, detail = False, f"verifier error: {exc}"

        if not met:
            unmet.append(f"{ref}: {detail}")

    if unmet:
        summary = f"Downgraded by completion_verifier: unmet/unverifiable refs: {unmet}"
        logger.warning(
            "completion_verifier: decision %s downgraded — %s",
            decision.return_id,
            summary,
        )
        return decision.model_copy(update={
            "decision": "needs_more_work",
            "confidence": 0.0,
            "audit_notes": [*decision.audit_notes, summary],
        })

    return decision
