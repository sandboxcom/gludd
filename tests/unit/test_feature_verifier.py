"""Unit tests for FeatureVerifier — hermetic (fake runner, no subprocess).

All evidence dispatch branches are tested without touching the filesystem or
running real pytest so the suite stays fast and CPU-light.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from general_ludd.quality.feature_verifier import FeatureVerifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_runner_pass(node_id: str) -> int:
    """Always returns 0 (pass) for any pytest node."""
    return 0


def _fake_runner_fail(node_id: str) -> int:
    """Always returns 1 (fail) for any pytest node."""
    return 1


def _make_verifier(tmp_path: Path, runner: Any = _fake_runner_pass) -> FeatureVerifier:
    # Scaffold a minimal repo root so role/module/molecule presence checks work.
    # roles
    roles_dir = tmp_path / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "security_gate").mkdir()
    (roles_dir / "threat_model").mkdir()

    # modules
    plugins_dir = tmp_path / "plugins" / "modules"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "gludd_facts.py").write_text("# module")
    (plugins_dir / "gludd_message.py").write_text("# module")

    # molecule
    mol_dir = tmp_path / "molecule" / "playbooks" / "test_gludd_facts"
    mol_dir.mkdir(parents=True)

    # a dummy source file with a symbol
    src_file = tmp_path / "src" / "general_ludd" / "quality" / "feature_verifier.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text("class FeatureVerifier:\n    pass\n")

    return FeatureVerifier(repo_root=str(tmp_path), runner=runner)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFeatureVerifierAllPass:
    """All evidence references pass → VERIFIED status."""

    def test_all_evidence_passes_marks_verified(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-aabbccdd",
            "name": "gludd_facts",
            "status": "implemented",
            "evidence": [
                "test:tests/integration/test_molecule_coverage.py",
                "role:security_gate",
                "module:gludd_facts",
                "molecule:test_gludd_facts",
            ],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified", result
        assert result["verified_at"] is not None
        assert result["evidence_results"]["all_met"] is True

    def test_test_prefix_dispatches_to_runner(self, tmp_path: Path) -> None:
        """test: prefix delegates to the injected runner callable."""
        called: list[str] = []

        def _recording_runner(node_id: str) -> int:
            called.append(node_id)
            return 0

        v = _make_verifier(tmp_path, runner=_recording_runner)
        feature = {
            "id": "FEAT-11223344",
            "name": "my_feature",
            "status": "requested",
            "evidence": ["test:tests/unit/test_feature_verifier.py"],
        }
        result = v.verify_feature(feature)
        assert "tests/unit/test_feature_verifier.py" in called[0]
        assert result["status"] == "verified"


class TestFeatureVerifierFailing:
    """Failing evidence → REGRESSED or REQUESTED."""

    def test_failing_evidence_from_verified_marks_regressed(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path, runner=_fake_runner_fail)
        feature = {
            "id": "FEAT-deadbeef",
            "name": "ci_green",
            "status": "verified",  # was previously verified
            "evidence": ["test:tests/unit/test_feature_verifier.py"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "regressed", result

    def test_failing_evidence_from_requested_stays_requested(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path, runner=_fake_runner_fail)
        feature = {
            "id": "FEAT-cafebabe",
            "name": "spend_limiter",
            "status": "requested",
            "evidence": ["test:tests/unit/test_feature_verifier.py"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested"

    def test_failing_evidence_from_implemented_marks_regressed(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path, runner=_fake_runner_fail)
        feature = {
            "id": "FEAT-00001111",
            "name": "some_feature",
            "status": "implemented",
            "evidence": ["test:tests/unit/test_feature_verifier.py"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "regressed"


class TestFeatureVerifierPartial:
    """Partial evidence (some pass, some fail) → IMPLEMENTED."""

    def test_partial_marks_implemented(self, tmp_path: Path) -> None:
        """One test passes (role exists), one fails (fake runner fails)."""
        v = _make_verifier(tmp_path, runner=_fake_runner_fail)
        feature = {
            "id": "FEAT-12345678",
            "name": "partial_feature",
            "status": "requested",
            "evidence": [
                "role:security_gate",       # exists → pass
                "test:tests/unit/nope.py",  # runner returns 1 → fail
            ],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "implemented", result
        assert result["evidence_results"]["met_count"] == 1
        assert result["evidence_results"]["total_count"] == 2


class TestFeatureVerifierEmpty:
    """Empty evidence list MUST NEVER reach VERIFIED (fail-closed)."""

    def test_empty_evidence_never_verified(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-00000000",
            "name": "no_evidence_feature",
            "status": "implemented",
            "evidence": [],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested", result
        assert result["evidence_results"]["all_met"] is False

    def test_none_evidence_never_verified(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-00000001",
            "name": "null_evidence_feature",
            "status": "verified",
            "evidence": None,
        }
        result = v.verify_feature(feature)
        # Fail-closed: can't stay verified with no evidence
        assert result["status"] == "requested"


class TestFeatureVerifierPresenceChecks:
    """role: / module: / molecule: / file: prefix dispatches."""

    def test_role_present(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-aaaabbbb",
            "name": "role_feature",
            "status": "requested",
            "evidence": ["role:security_gate"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified"

    def test_role_absent(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-ccccdddd",
            "name": "missing_role",
            "status": "requested",
            "evidence": ["role:nonexistent_role"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested"
        assert result["evidence_results"]["all_met"] is False

    def test_module_present(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-eeeeffff",
            "name": "module_feature",
            "status": "requested",
            "evidence": ["module:gludd_facts"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified"

    def test_module_absent(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-00001234",
            "name": "missing_module",
            "status": "requested",
            "evidence": ["module:nonexistent_module"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested"

    def test_molecule_scenario_present(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-56789abc",
            "name": "molecule_feature",
            "status": "requested",
            "evidence": ["molecule:test_gludd_facts"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified"

    def test_file_symbol_present(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        # The scaffolded file contains 'FeatureVerifier'
        rel_path = "src/general_ludd/quality/feature_verifier.py"
        feature = {
            "id": "FEAT-abcdef12",
            "name": "symbol_feature",
            "status": "requested",
            "evidence": [f"file:{rel_path}::FeatureVerifier"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified"

    def test_file_symbol_absent(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        rel_path = "src/general_ludd/quality/feature_verifier.py"
        feature = {
            "id": "FEAT-abcdef34",
            "name": "symbol_feature_absent",
            "status": "requested",
            "evidence": [f"file:{rel_path}::NonExistentSymbol"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested"


class TestEmptyNamePresenceChecks:
    """Empty-name presence refs must be REJECTED, not match a container dir.

    ``Path(root) / "" == Path(root)`` (pathlib drops an empty component), so a
    bare ``role:`` / ``module:`` / ``molecule:`` ref would otherwise resolve to
    the existing container directory and falsely verify with no real proof. These
    guard against that false-positive hole (live on Python 3.12+ for role:).
    """

    def test_empty_role_rejected(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        met, detail = v._check_role("")
        assert met is False, detail
        assert "empty role" in detail

    def test_empty_module_rejected(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        met, detail = v._check_module("")
        assert met is False, detail
        assert "empty module" in detail

    def test_empty_molecule_rejected(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        met, detail = v._check_molecule("")
        assert met is False, detail
        assert "empty molecule" in detail

    def test_module_dir_not_accepted_as_file(self, tmp_path: Path) -> None:
        """A name matching a DIRECTORY (not a module file) must not verify."""
        # _make_verifier scaffolds plugins/modules; create the subdir AFTER so its
        # own mkdir(parents=True) does not collide. A name resolving to a
        # subdirectory must be rejected now that _check_module requires is_file().
        v = _make_verifier(tmp_path)
        (tmp_path / "plugins" / "modules" / "subpkg").mkdir()
        met, _detail = v._check_module("subpkg")
        assert met is False

    def test_empty_role_ref_keeps_feature_requested(self, tmp_path: Path) -> None:
        """End-to-end through verify_feature: a bare ``role:`` ref must NOT verify."""
        v = _make_verifier(tmp_path)
        feature = {
            "id": "FEAT-eeee0000",
            "name": "empty_role_ref",
            "status": "requested",
            "evidence": ["role:"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested", result
        assert result["evidence_results"]["all_met"] is False


class TestVerifyAll:
    """verify_all processes a list of features and returns a summary."""

    def test_verify_all_returns_summary(self, tmp_path: Path) -> None:
        v = _make_verifier(tmp_path)
        features = [
            {
                "id": "FEAT-11111111",
                "name": "feat_a",
                "status": "requested",
                "evidence": ["role:security_gate"],
            },
            {
                "id": "FEAT-22222222",
                "name": "feat_b",
                "status": "requested",
                "evidence": [],  # empty → requested, never verified
            },
        ]
        summary = v.verify_all(features)
        assert "results" in summary
        assert "total" in summary
        assert summary["total"] == 2
        # feat_a should be verified, feat_b forced to requested
        statuses = {r["name"]: r["status"] for r in summary["results"]}
        assert statuses["feat_a"] == "verified"
        assert statuses["feat_b"] == "requested"
