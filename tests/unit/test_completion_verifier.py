"""Hermetic tests for the evidence-gated completion verifier (Layer 2).

Mirrors test_feature_verifier.py patterns: tmp_path for filesystem fixtures,
monkeypatch for subprocess, injectable runner for test: refs. No real git, no
real pytest, no network — all verification is deterministic.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from general_ludd.review.completion_verifier import (
    _check_artifact,
    _check_commit,
    _repo_root_is_unresolved,
    verify_completion,
)
from general_ludd.schemas.task_decision import TaskDecision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision(
    decision: str = "complete",
    evidence_refs: list[str] | None = None,
    audit_notes: list[str] | None = None,
    confidence: float = 0.9,
) -> TaskDecision:
    return TaskDecision(
        return_id="RET-TEST-001",
        matched_todo_id="TODO-TEST-001",
        decision=decision,
        confidence=confidence,
        evidence_refs=evidence_refs or [],
        audit_notes=audit_notes or [],
    )


def _passing_runner(node_id: str) -> int:
    return 0


def _failing_runner(node_id: str) -> int:
    return 1


# ---------------------------------------------------------------------------
# _repo_root_is_unresolved
# ---------------------------------------------------------------------------


class TestRepoRootIsUnresolved:
    def test_none_is_unresolved(self) -> None:
        assert _repo_root_is_unresolved(None) is True

    def test_dot_is_unresolved(self) -> None:
        assert _repo_root_is_unresolved(".") is True

    def test_dot_slash_is_unresolved(self) -> None:
        assert _repo_root_is_unresolved("./") is True

    def test_empty_is_unresolved(self) -> None:
        assert _repo_root_is_unresolved("") is True

    def test_real_path_is_resolved(self, tmp_path: Path) -> None:
        assert _repo_root_is_unresolved(str(tmp_path)) is False


# ---------------------------------------------------------------------------
# non-complete pass-through
# ---------------------------------------------------------------------------


class TestNonCompletePassThrough:
    @pytest.mark.parametrize("dec", ["needs_more_work", "failed", "blocked", "manual_hold"])
    def test_non_complete_returns_unchanged(self, dec: str) -> None:
        d = _decision(decision=dec, evidence_refs=["test:foo/bar.py"])
        result = verify_completion(d, None, None)
        assert result is d
        assert result.decision == dec

    def test_ignore_duplicate_returns_unchanged(self) -> None:
        d = _decision(decision="ignore_duplicate")
        result = verify_completion(d, None, None)
        assert result is d


# ---------------------------------------------------------------------------
# empty evidence_refs → downgrade
# ---------------------------------------------------------------------------


class TestEmptyEvidenceRefs:
    def test_empty_list_downgrades(self) -> None:
        d = _decision(decision="complete", evidence_refs=[])
        result = verify_completion(d, None, "/some/repo")
        assert result.decision == "needs_more_work"
        assert result.confidence == 0.0
        assert any("no evidence_refs" in note for note in result.audit_notes)

    def test_original_not_mutated(self) -> None:
        d = _decision(decision="complete", evidence_refs=[])
        result = verify_completion(d, None, "/some/repo")
        assert d.decision == "complete"  # original unchanged
        assert result is not d


# ---------------------------------------------------------------------------
# test: refs with injectable runner
# ---------------------------------------------------------------------------


class TestTestRefs:
    def test_failing_runner_downgrades(self, tmp_path: Path) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["test:tests/unit/test_foo.py::TestClass::test_method"],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_failing_runner)
        assert result.decision == "needs_more_work"
        assert result.confidence == 0.0
        assert any("unmet" in note for note in result.audit_notes)

    def test_passing_runner_stays_complete(self, tmp_path: Path) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["test:tests/unit/test_foo.py::TestClass::test_method"],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_passing_runner)
        assert result.decision == "complete"
        assert result is d


# ---------------------------------------------------------------------------
# file: refs
# ---------------------------------------------------------------------------


class TestFileRefs:
    def test_missing_file_downgrades(self, tmp_path: Path) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["file:src/nonexistent.py::MyClass"],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_passing_runner)
        assert result.decision == "needs_more_work"

    def test_file_missing_symbol_downgrades(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        target = tmp_path / "src" / "mymod.py"
        target.write_text("def other_func(): pass\n")
        d = _decision(
            decision="complete",
            evidence_refs=["file:src/mymod.py::MyMissingSymbol"],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_passing_runner)
        assert result.decision == "needs_more_work"

    def test_file_present_symbol_present_stays_complete(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        target = tmp_path / "src" / "mymod.py"
        target.write_text("class MyClass:\n    pass\n")
        d = _decision(
            decision="complete",
            evidence_refs=["file:src/mymod.py::MyClass"],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_passing_runner)
        assert result.decision == "complete"
        assert result is d


# ---------------------------------------------------------------------------
# commit: refs
# ---------------------------------------------------------------------------


class TestCommitRefs:
    def test_commit_present_stays_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        d = _decision(
            decision="complete",
            evidence_refs=["commit:abc123def456"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "complete"
        assert result is d

    def test_commit_absent_downgrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 1
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        d = _decision(
            decision="complete",
            evidence_refs=["commit:abc123def456"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work"

    def test_unsafe_sha_rejected_without_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        subprocess_called = []

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            subprocess_called.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        # sha with non-hex chars: injection-shaped
        d = _decision(
            decision="complete",
            evidence_refs=["commit:../../etc/passwd"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work"
        assert subprocess_called == [], "subprocess must NOT be called for unsafe sha"

    def test_sha_too_short_rejected_without_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        subprocess_called = []

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            subprocess_called.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        d = _decision(
            decision="complete",
            evidence_refs=["commit:ab1"],  # only 3 hex chars, below minimum 4
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work"
        assert subprocess_called == [], "subprocess must NOT be called for too-short sha"


# ---------------------------------------------------------------------------
# artifact: refs
# ---------------------------------------------------------------------------


class TestArtifactRefs:
    def test_artifact_present_stays_complete(self, tmp_path: Path) -> None:
        artifact = tmp_path / "output.xml"
        artifact.write_text("<results/>")
        d = _decision(
            decision="complete",
            evidence_refs=["artifact:output.xml"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "complete"
        assert result is d

    def test_artifact_missing_downgrades(self, tmp_path: Path) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["artifact:missing_output.xml"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work"

    def test_artifact_escape_downgrades(self, tmp_path: Path) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["artifact:../../etc/passwd"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work"
        assert any("escapes" in note for note in result.audit_notes)


# ---------------------------------------------------------------------------
# repo_root=None fail-safe
# ---------------------------------------------------------------------------


class TestRepoRootNoneFailSafe:
    def test_repo_root_none_with_test_ref_downgrades(self) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["test:tests/unit/test_foo.py"],
        )
        result = verify_completion(d, None, None)
        assert result.decision == "needs_more_work"
        assert result.confidence == 0.0

    def test_repo_root_dot_with_file_ref_downgrades(self) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["file:src/foo.py::bar"],
        )
        result = verify_completion(d, None, ".")
        assert result.decision == "needs_more_work"

    def test_repo_root_none_with_commit_ref_downgrades(self) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["commit:abc123def456"],
        )
        result = verify_completion(d, None, None)
        assert result.decision == "needs_more_work"

    def test_repo_root_none_with_artifact_ref_downgrades(self) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=["artifact:output.xml"],
        )
        result = verify_completion(d, None, None)
        assert result.decision == "needs_more_work"


# ---------------------------------------------------------------------------
# all real refs pass → returns original object
# ---------------------------------------------------------------------------


class TestAllRefsPass:
    def test_all_refs_met_returns_original_object(self, tmp_path: Path) -> None:
        # Set up real file + symbol
        (tmp_path / "src").mkdir()
        src_file = tmp_path / "src" / "module.py"
        src_file.write_text("def my_function(): pass\n")

        d = _decision(
            decision="complete",
            evidence_refs=[
                "test:tests/unit/test_something.py::TestClass::test_method",
                "file:src/module.py::my_function",
            ],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_passing_runner)
        assert result.decision == "complete"
        assert result is d  # same object — no copy was made

    def test_mixed_met_unmet_downgrades(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        src_file = tmp_path / "src" / "module.py"
        src_file.write_text("def my_function(): pass\n")

        d = _decision(
            decision="complete",
            evidence_refs=[
                "file:src/module.py::my_function",  # met
                "test:tests/unit/test_something.py",  # not met (runner fails)
            ],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_failing_runner)
        assert result.decision == "needs_more_work"


# ---------------------------------------------------------------------------
# audit_notes preserved on downgrade
# ---------------------------------------------------------------------------


class TestAuditNotesPreserved:
    def test_existing_audit_notes_preserved_on_downgrade(self) -> None:
        d = _decision(
            decision="complete",
            evidence_refs=[],
            audit_notes=["prior note"],
        )
        result = verify_completion(d, None, "/some/repo")
        assert "prior note" in result.audit_notes
        # And the new note was appended
        assert len(result.audit_notes) > 1


# ---------------------------------------------------------------------------
# _check_commit / _check_artifact unit tests (direct)
# ---------------------------------------------------------------------------


class TestCheckCommitDirect:
    def test_safe_sha_calls_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        met, _detail = _check_commit(str(tmp_path), "deadbeef1234")
        assert met is True
        assert len(calls) == 1
        assert "deadbeef1234" in calls[0]

    def test_64_char_sha_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            r = MagicMock()
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        sha = "a" * 64
        met, _detail = _check_commit(str(tmp_path), sha)
        assert met is True

    def test_65_char_sha_rejected(self, tmp_path: Path) -> None:
        sha = "a" * 65
        met, detail = _check_commit(str(tmp_path), sha)
        assert met is False
        assert "unsafe sha" in detail


class TestCheckArtifactDirect:
    def test_present_file_returns_true(self, tmp_path: Path) -> None:
        f = tmp_path / "output.xml"
        f.write_text("<x/>")
        met, _detail = _check_artifact(str(tmp_path), "output.xml")
        assert met is True

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        met, detail = _check_artifact(str(tmp_path), "missing.xml")
        assert met is False
        assert "not found" in detail

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        met, detail = _check_artifact(str(tmp_path), "../../etc/passwd")
        assert met is False
        assert "escapes" in detail


# ---------------------------------------------------------------------------
# Gate VERIFIES (not always-blocks) when repo_root is resolved and refs pass
# ---------------------------------------------------------------------------


class TestGateVerifiesWithResolvedRoot:
    """Regression suite for the non-functional gate (always-blocked, never verified).

    The gate must ACCEPT complete decisions whose refs genuinely pass — not just
    reject bad ones. These tests prove the happy-path: a complete decision with
    one or more well-formed, passing evidence refs stays ``complete`` when a real
    repo_root is supplied.
    """

    def test_file_ref_with_resolved_root_stays_complete(self, tmp_path: Path) -> None:
        """file:path::symbol ref resolves and stays complete when both exist."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mymod.py").write_text("class VerifiedClass:\n    pass\n")

        d = _decision(
            decision="complete",
            evidence_refs=["file:src/mymod.py::VerifiedClass"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "complete", (
            f"Gate downgraded a valid file: ref — expected complete, got {result.decision}. "
            f"audit_notes={result.audit_notes}"
        )
        assert result is d  # same object — no copy made

    def test_test_ref_passing_runner_stays_complete(self, tmp_path: Path) -> None:
        """test: ref stays complete when the injected runner returns 0."""
        d = _decision(
            decision="complete",
            evidence_refs=["test:tests/unit/test_event_loop.py::TestEventLoop::test_run_forever_can_be_stopped"],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_passing_runner)
        assert result.decision == "complete", (
            f"Gate downgraded a passing test: ref — expected complete, got {result.decision}. "
            f"audit_notes={result.audit_notes}"
        )

    def test_unprefixed_ref_still_downgrades_with_resolved_root(self, tmp_path: Path) -> None:
        """Free-text / unprefixed refs must STILL be rejected even with a resolved root."""
        d = _decision(
            decision="complete",
            evidence_refs=["all tests pass and everything looks good"],
        )
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work", (
            "Unprefixed ref should have been downgraded even with resolved root"
        )
        assert any("unknown evidence prefix" in n for n in result.audit_notes)

    def test_multiple_refs_all_pass_stays_complete(self, tmp_path: Path) -> None:
        """All refs must pass for complete to survive — and when all do, it does."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "two.py").write_text("def func_a(): pass\ndef func_b(): pass\n")

        d = _decision(
            decision="complete",
            evidence_refs=[
                "file:src/two.py::func_a",
                "test:tests/unit/test_something.py::test_func",
            ],
        )
        result = verify_completion(d, None, str(tmp_path), runner=_passing_runner)
        assert result.decision == "complete"


# ---------------------------------------------------------------------------
# Empty / degenerate artifact paths must NOT be a false positive
# ---------------------------------------------------------------------------


class TestEmptyArtifactPathRejected:
    """``Path(root) / "" == root`` and ``root / "." == root`` both resolve to the
    repo root (which always exists), so a bare ``artifact:`` or ``artifact:.``
    ref would falsely pass the existence check. The gate must reject these — an
    empty artifact name is not verifiable evidence."""

    def test_empty_artifact_ref_downgrades(self, tmp_path: Path) -> None:
        d = _decision(decision="complete", evidence_refs=["artifact:"])
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work"
        assert any("empty/degenerate artifact" in n for n in result.audit_notes)

    def test_dot_artifact_ref_downgrades(self, tmp_path: Path) -> None:
        d = _decision(decision="complete", evidence_refs=["artifact:."])
        result = verify_completion(d, None, str(tmp_path))
        assert result.decision == "needs_more_work"

    def test_check_artifact_empty_returns_false(self, tmp_path: Path) -> None:
        met, detail = _check_artifact(str(tmp_path), "")
        assert met is False
        assert "empty/degenerate artifact" in detail

    def test_check_artifact_dot_returns_false(self, tmp_path: Path) -> None:
        met, _detail = _check_artifact(str(tmp_path), ".")
        assert met is False

    def test_check_artifact_dotslash_returns_false(self, tmp_path: Path) -> None:
        met, _detail = _check_artifact(str(tmp_path), "./")
        assert met is False
