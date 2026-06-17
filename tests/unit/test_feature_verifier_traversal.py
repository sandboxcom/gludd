"""Path-traversal hardening tests for FeatureVerifier._check_file_symbol.

A ``file:<path>::<symbol>`` evidence ref is caller/feature-database-derived and
is joined onto the repo root before being read.  Without containment, a crafted
``path`` such as ``../outside_secret.txt`` could escape the repo root and let an
attacker (a) read an arbitrary file off disk and (b) forge "met" evidence by
planting the symbol in a file outside the tree.

The fix resolves ``(root / path).resolve()`` and requires the result to be
``is_relative_to(root.resolve())``, returning ``(False, "path escapes repo
root")`` otherwise — fail-closed, and crucially WITHOUT ever reading the file.

These tests are hermetic: no subprocess, no real pytest.  ``tmp_path`` may itself
be a symlink (macOS ``/var`` -> ``/private/var``), which is exactly why the
source compares against ``self._root.resolve()``.
"""

from __future__ import annotations

from pathlib import Path

from general_ludd.quality.feature_verifier import FeatureVerifier


def _fake_runner_pass(node_id: str) -> int:
    """Always passes — kept so a traversal ref is the only failing evidence."""
    return 0


def _make_verifier(repo_root: Path) -> FeatureVerifier:
    """Scaffold a minimal in-root source file that genuinely contains a symbol."""
    src_file = repo_root / "src" / "general_ludd" / "quality" / "feature_verifier.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("class FeatureVerifier:\n    pass\n")
    return FeatureVerifier(repo_root=str(repo_root), runner=_fake_runner_pass)


class TestFileSymbolTraversalRejected:
    """A ``file:`` ref whose path escapes the repo root must fail-closed."""

    def test_relative_escape_does_not_read_outside_file(self, tmp_path: Path) -> None:
        """``file:../outside_secret.txt::root`` must NOT read the outside file.

        The outside file literally contains the symbol ``root``; if the verifier
        read it, the ref would be (incorrectly) met.  Containment proves it never
        reads it: the ref is not met and the detail says the path escaped.
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        # Plant the symbol OUTSIDE the repo root (sibling of repo_root).
        outside = tmp_path / "outside_secret.txt"
        outside.write_text("root\n")  # contains the symbol we will ask for

        v = _make_verifier(repo_root)
        feature = {
            "id": "FEAT-traversal1",
            "name": "rel_escape",
            "status": "requested",
            "evidence": ["file:../outside_secret.txt::root"],
        }
        result = v.verify_feature(feature)

        # Never reached the outside file -> ref not met -> not verified.
        assert result["status"] == "requested", result
        assert result["evidence_results"]["all_met"] is False
        per_ref = result["evidence_results"]["per_ref"][0]
        assert per_ref["met"] is False
        assert "path escapes repo root" in per_ref["detail"]

    def test_deep_dotdot_escape_to_etc_passwd_degrades_never_raises(
        self, tmp_path: Path
    ) -> None:
        """A deep ``../../../../../../etc/passwd::root`` escape degrades, no raise."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        v = _make_verifier(repo_root)
        feature = {
            "id": "FEAT-traversal2",
            "name": "deep_escape",
            "status": "verified",  # was verified; must degrade, not stay verified
            "evidence": ["file:../../../../../../etc/passwd::root"],
        }
        # Must not raise.
        result = v.verify_feature(feature)
        assert result["status"] != "verified", result
        assert result["evidence_results"]["all_met"] is False
        per_ref = result["evidence_results"]["per_ref"][0]
        assert per_ref["met"] is False
        assert "path escapes repo root" in per_ref["detail"]

    def test_absolute_path_outside_root_rejected(self, tmp_path: Path) -> None:
        """An absolute path pointing outside the repo root is rejected.

        ``root / "/etc/passwd"`` collapses to ``/etc/passwd`` (pathlib drops the
        left operand on an absolute right operand), which is outside the root.
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        v = _make_verifier(repo_root)
        feature = {
            "id": "FEAT-traversal3",
            "name": "abs_escape",
            "status": "requested",
            "evidence": ["file:/etc/passwd::root"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested", result
        per_ref = result["evidence_results"]["per_ref"][0]
        assert per_ref["met"] is False
        assert "path escapes repo root" in per_ref["detail"]


class TestFileSymbolInRootStillVerifies:
    """A genuine in-root ``file:`` ref must still verify (no false negatives)."""

    def test_genuine_in_root_symbol_verifies(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        v = _make_verifier(repo_root)
        rel = "src/general_ludd/quality/feature_verifier.py"
        feature = {
            "id": "FEAT-traversal4",
            "name": "in_root_ok",
            "status": "requested",
            "evidence": [f"file:{rel}::FeatureVerifier"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified", result
        assert result["evidence_results"]["all_met"] is True
        per_ref = result["evidence_results"]["per_ref"][0]
        assert per_ref["met"] is True
        assert "FeatureVerifier" in per_ref["detail"]
