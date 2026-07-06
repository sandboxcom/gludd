"""Dogfood self-verification tests for the feature database.

These tests pin the STRICT, fail-closed self-verification contract:

  * The production ``FeatureVerifier`` resolves ``module:`` evidence at the
    canonical collection path where gludd_* modules actually live (regression
    guard for the path bug that silently downgraded every module-backed feature).
  * ``scripts/dogfood_features.py`` runs the REAL ``FeatureVerifier`` over the
    real ``FEATURE_SEED`` and reports ``false_claims == 0`` — every feature that
    advertises status implemented/verified must fully resolve ALL of its
    advertised evidence (not merely one ref).  This is the "gludd proves which
    of its own advertised features are real" invariant.
  * A partial-evidence feature is surfaced as a false claim (strict bar).

Hermetic where it can be (scaffolded tmp repos); the seed-level test reads the
real repo on disk but performs only presence checks (no subprocess / no pytest).
"""

from __future__ import annotations

from pathlib import Path

from scripts.dogfood_features import (
    REPO_ROOT,
    _file_existence_runner,
    run_dogfood_features,
)

from general_ludd.db.feature_seed import FEATURE_SEED
from general_ludd.quality.feature_verifier import FeatureVerifier

# ---------------------------------------------------------------------------
# 1. module: evidence resolves at the canonical collection path
# ---------------------------------------------------------------------------

class TestModulePathResolution:
    """gludd_* modules live under collections/.../agent/plugins/modules/.

    The verifier MUST find them there (the location the feature seed and the
    dogfood script both target), not only under a top-level plugins/ dir.
    """

    def _scaffold_collection_module(self, tmp_path: Path, name: str) -> None:
        modules_dir = (
            tmp_path
            / "collections" / "ansible_collections" / "general_ludd"
            / "agent" / "plugins" / "modules"
        )
        modules_dir.mkdir(parents=True)
        (modules_dir / f"{name}.py").write_text("# module\n")

    def test_module_found_under_collection_path(self, tmp_path: Path) -> None:
        self._scaffold_collection_module(tmp_path, "gludd_facts")
        v = FeatureVerifier(repo_root=str(tmp_path), runner=lambda _n: 0)
        feature = {
            "id": "FEAT-c0111111",
            "name": "facts_api_mq",
            "status": "implemented",
            "evidence": ["module:gludd_facts"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified", result
        assert result["evidence_results"]["all_met"] is True

    def test_module_still_found_under_top_level_plugins(self, tmp_path: Path) -> None:
        """Backward-compat: a top-level plugins/modules/ layout still resolves."""
        plugins_dir = tmp_path / "plugins" / "modules"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "gludd_facts.py").write_text("# module\n")
        v = FeatureVerifier(repo_root=str(tmp_path), runner=lambda _n: 0)
        feature = {
            "id": "FEAT-c0222222",
            "name": "facts_api_mq",
            "status": "implemented",
            "evidence": ["module:gludd_facts"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "verified", result

    def test_absent_module_is_unmet(self, tmp_path: Path) -> None:
        v = FeatureVerifier(repo_root=str(tmp_path), runner=lambda _n: 0)
        feature = {
            "id": "FEAT-c0333333",
            "name": "ghost",
            "status": "implemented",
            "evidence": ["module:gludd_does_not_exist"],
        }
        result = v.verify_feature(feature)
        # Implemented + all evidence unmet -> regressed (fail-closed downgrade).
        assert result["status"] == "regressed", result
        assert result["evidence_results"]["all_met"] is False


# ---------------------------------------------------------------------------
# 2. The dogfood self-check over the REAL seed: false_claims must be 0
# ---------------------------------------------------------------------------

class TestDogfoodSelfCheck:
    """scripts/dogfood_features.py runs the real verifier over FEATURE_SEED."""

    def test_dogfood_features_passes_zero_false_claims(self) -> None:
        rc = run_dogfood_features()
        assert rc == 0, (
            "dogfood-features reported false_claims > 0: at least one feature "
            "advertises implemented/verified but cannot fully resolve its evidence"
        )

    def test_every_implemented_feature_fully_resolves_evidence(self) -> None:
        """Strict invariant, asserted directly against the production verifier.

        For EVERY seed feature whose status is implemented/verified, ALL of its
        evidence refs must resolve (met_count == total_count > 0).  A single
        unmet ref is a false claim and fails here with a precise diagnostic.
        """
        verifier = FeatureVerifier(repo_root=str(REPO_ROOT), runner=_file_existence_runner)
        summary = verifier.verify_all(FEATURE_SEED)
        by_name = {r["name"]: r for r in summary["results"]}

        false_claims: list[str] = []
        for feat in FEATURE_SEED:
            status = str(feat.get("status", "requested")).lower()
            if status not in {"implemented", "verified"}:
                continue
            ev = by_name[feat["name"]]["evidence_results"]
            if ev["met_count"] != ev["total_count"] or ev["total_count"] == 0:
                unmet = [r["ref"] for r in ev["per_ref"] if not r["met"]] or ["<empty evidence>"]
                false_claims.append(
                    f"{feat['name']} ({ev['met_count']}/{ev['total_count']}): unmet={unmet}"
                )

        assert not false_claims, "FALSE CLAIMS in FEATURE_SEED:\n" + "\n".join(false_claims)

    def test_seed_implemented_features_become_verified(self) -> None:
        """A fully-resolved implemented feature is upgraded to 'verified'.

        Guards the module-path bug: with the wrong path, module-backed features
        silently downgraded to 'implemented' instead of 'verified'.
        """
        verifier = FeatureVerifier(repo_root=str(REPO_ROOT), runner=_file_existence_runner)
        summary = verifier.verify_all(FEATURE_SEED)
        # No implemented/verified seed feature may end up 'regressed' or merely
        # 'implemented' — full evidence resolution means 'verified'.
        for feat in FEATURE_SEED:
            status = str(feat.get("status", "requested")).lower()
            if status not in {"implemented", "verified"}:
                continue
            result = next(r for r in summary["results"] if r["name"] == feat["name"])
            assert result["status"] == "verified", (
                f"{feat['name']} did not reach 'verified' (got {result['status']}); "
                "an advertised feature failed to fully prove itself"
            )


# ---------------------------------------------------------------------------
# 3. Strict bar: partial evidence is a false claim
# ---------------------------------------------------------------------------

class TestStrictPartialIsFalseClaim:
    """The dogfood bar is ALL evidence, not at-least-one."""

    def test_partial_evidence_downgrades_below_verified(self, tmp_path: Path) -> None:
        roles_dir = (
            tmp_path / "collections" / "ansible_collections" / "general_ludd"
            / "agent" / "roles"
        )
        roles_dir.mkdir(parents=True)
        (roles_dir / "present_role").mkdir()
        v = FeatureVerifier(repo_root=str(tmp_path), runner=lambda _n: 0)
        feature = {
            "id": "FEAT-c0444444",
            "name": "partial",
            "status": "implemented",
            "evidence": ["role:present_role", "role:absent_role"],
        }
        result = v.verify_feature(feature)
        # Partial -> implemented (NOT verified): under the strict dogfood bar this
        # counts as a false claim for an 'implemented'/'verified' feature.
        assert result["status"] == "implemented", result
        assert result["evidence_results"]["met_count"] == 1
        assert result["evidence_results"]["total_count"] == 2
        assert result["evidence_results"]["all_met"] is False
