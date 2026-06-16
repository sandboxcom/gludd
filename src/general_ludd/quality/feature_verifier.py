"""Evidence-gated feature verifier for the feature database.

Dispatches evidence references by prefix, exactly like the make audit-evidence
pattern.  The runner is injectable so tests remain hermetic and fast.

Evidence-string grammar (dispatch on prefix):
  test:<pytest-node-id>   → run via runner(node_id); met iff rc == 0
  role:<name>             → dir exists under collections/.../agent/roles/
  module:<name>           → file exists under plugins/modules/
  molecule:<scenario>     → dir exists under molecule/playbooks/
  file:<path>::<symbol>   → symbol present in file at <path>

Status derivation:
  all met     → VERIFIED (verified_at set)
  some met    → IMPLEMENTED
  none met    → REGRESSED if prior status in {VERIFIED, IMPLEMENTED} else REQUESTED
  empty/None  evidence → NEVER VERIFIED (forced to REQUESTED, fail-closed)
"""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Prior statuses that degrade to REGRESSED when evidence fails
_DEGRADING_STATUSES = {"verified", "implemented"}


def _default_runner(node_id: str) -> int:
    """Default pytest runner: uv run pytest <node> -q.  Returns rc."""
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", node_id, "-q", "--no-header", "--tb=no"],
            capture_output=True,
            timeout=120,
        )
        return result.returncode
    except Exception as exc:
        logger.warning("feature_verifier: runner error for %s: %s", node_id, exc)
        return 1


class FeatureVerifier:
    """Verify a feature's evidence refs and return a status + per-ref report.

    Parameters
    ----------
    repo_root:
        Absolute path to the repository root (used for presence checks).
    runner:
        Callable(node_id: str) -> int.  Defaults to subprocess uv run pytest.
        Inject a fake for unit tests.
    """

    def __init__(
        self,
        repo_root: str,
        runner: Callable[[str], int] | None = None,
    ) -> None:
        self._root = Path(repo_root)
        self._runner: Callable[[str], int] = runner if runner is not None else _default_runner

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_feature(self, feature: dict[str, Any]) -> dict[str, Any]:
        """Verify a single feature dict and return a result dict.

        The result dict includes:
          status        : new FeatureStatus string
          verified_at   : ISO datetime string if status==verified else None
          evidence_results : per-ref detail + aggregate counters
        """
        evidence_raw: list[str] | None = feature.get("evidence")
        prior_status: str = str(feature.get("status", "requested")).lower()

        # Fail-closed: empty or None evidence MUST NEVER reach VERIFIED
        if not evidence_raw:
            return self._build_result(
                status="requested",
                verified_at=None,
                per_ref=[],
                met_count=0,
                total_count=0,
            )

        per_ref: list[dict[str, Any]] = []
        met_count = 0

        for ref in evidence_raw:
            met, detail = self._check_ref(ref)
            per_ref.append({"ref": ref, "met": met, "detail": detail})
            if met:
                met_count += 1

        total_count = len(per_ref)
        all_met = met_count == total_count and total_count > 0

        # Derive new status
        if all_met:
            new_status = "verified"
            verified_at: datetime | None = datetime.now(UTC)
        elif met_count > 0:
            new_status = "implemented"
            verified_at = None
        else:
            # No evidence met
            new_status = "regressed" if prior_status in _DEGRADING_STATUSES else "requested"
            verified_at = None

        return self._build_result(
            status=new_status,
            verified_at=verified_at,
            per_ref=per_ref,
            met_count=met_count,
            total_count=total_count,
        )

    def verify_all(self, features: list[dict[str, Any]]) -> dict[str, Any]:
        """Verify a list of features and return a summary dict.

        Summary keys:
          total    : int
          results  : list of per-feature result dicts (each includes name, status, …)
        """
        results: list[dict[str, Any]] = []
        for feat in features:
            r = self.verify_feature(feat)
            r["id"] = feat.get("id")
            r["name"] = feat.get("name")
            results.append(r)
        return {
            "total": len(results),
            "verified_count": sum(1 for r in results if r["status"] == "verified"),
            "implemented_count": sum(1 for r in results if r["status"] == "implemented"),
            "requested_count": sum(1 for r in results if r["status"] == "requested"),
            "regressed_count": sum(1 for r in results if r["status"] == "regressed"),
            "results": results,
        }

    # ------------------------------------------------------------------
    # Evidence dispatch
    # ------------------------------------------------------------------

    def _check_ref(self, ref: str) -> tuple[bool, str]:
        """Dispatch on ref prefix. Returns (met, detail_string)."""
        if ref.startswith("test:"):
            return self._check_test(ref[len("test:"):])
        if ref.startswith("role:"):
            return self._check_role(ref[len("role:"):])
        if ref.startswith("module:"):
            return self._check_module(ref[len("module:"):])
        if ref.startswith("molecule:"):
            return self._check_molecule(ref[len("molecule:"):])
        if ref.startswith("file:"):
            return self._check_file_symbol(ref[len("file:"):])
        logger.warning("feature_verifier: unknown evidence prefix in %r", ref)
        return False, f"unknown prefix: {ref}"

    def _check_test(self, node_id: str) -> tuple[bool, str]:
        rc = self._runner(node_id)
        met = rc == 0
        return met, f"pytest rc={rc}"

    def _check_role(self, name: str) -> tuple[bool, str]:
        # roles live under collections/.../agent/roles/<name>
        roles_root = self._root / "collections"
        # Walk collections looking for any roles/<name> dir
        for candidate in roles_root.rglob(f"roles/{name}"):
            if candidate.is_dir():
                return True, f"role dir found: {candidate.relative_to(self._root)}"
        return False, f"role dir not found: {name}"

    def _check_module(self, name: str) -> tuple[bool, str]:
        # gludd_* modules live under the agent collection, not a top-level
        # plugins/ dir.  Check the canonical collection path first, then fall
        # back to a top-level plugins/modules/ (some layouts) and finally an
        # rglob so a module is found wherever a real plugins/modules/ holds it.
        canonical = (
            self._root
            / "collections" / "ansible_collections" / "general_ludd"
            / "agent" / "plugins" / "modules"
        )
        for plugins_dir in (canonical, self._root / "plugins" / "modules"):
            for ext in ("", ".py"):
                candidate = plugins_dir / f"{name}{ext}"
                if candidate.exists():
                    return True, f"module found: {candidate.relative_to(self._root)}"
        # Last resort: any plugins/modules/<name>[.py] anywhere under the repo.
        for ext in ("", ".py"):
            for candidate in self._root.rglob(f"plugins/modules/{name}{ext}"):
                if candidate.exists():
                    return True, f"module found: {candidate.relative_to(self._root)}"
        return False, f"module not found: plugins/modules/{name}[.py]"

    def _check_molecule(self, scenario: str) -> tuple[bool, str]:
        scenario_dir = self._root / "molecule" / "playbooks" / scenario
        if scenario_dir.is_dir():
            return True, f"molecule scenario dir found: {scenario_dir.relative_to(self._root)}"
        return False, f"molecule scenario dir not found: molecule/playbooks/{scenario}"

    def _check_file_symbol(self, spec: str) -> tuple[bool, str]:
        """file:<relative-path>::<symbol>"""
        if "::" not in spec:
            return False, f"file: evidence malformed (missing ::): {spec}"
        path_part, symbol = spec.split("::", 1)
        target = self._root / path_part
        if not target.exists():
            return False, f"file not found: {path_part}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            if symbol in text:
                return True, f"symbol '{symbol}' found in {path_part}"
            return False, f"symbol '{symbol}' NOT found in {path_part}"
        except Exception as exc:
            return False, f"file read error: {exc}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_result(
        status: str,
        verified_at: datetime | None,
        per_ref: list[dict[str, Any]],
        met_count: int,
        total_count: int,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "verified_at": verified_at.isoformat() if verified_at is not None else None,
            "evidence_results": {
                "all_met": met_count == total_count and total_count > 0,
                "met_count": met_count,
                "total_count": total_count,
                "per_ref": per_ref,
            },
        }
