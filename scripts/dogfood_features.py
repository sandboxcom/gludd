#!/usr/bin/env python3
"""make dogfood-features — gludd verifies its OWN feature database.

Runs the REAL ``FeatureVerifier`` (the same evidence-gated engine the
``/api/features/verify`` endpoint uses) over ``FEATURE_SEED`` and asserts that
every feature claiming status ``verified`` or ``implemented`` has ALL of its
evidence references resolve to a real artifact in this repo.

Fail-closed, strict:
  * empty evidence NEVER supports an implemented/verified claim
  * ANY unmet evidence ref on an implemented/verified feature is a FALSE CLAIM
    (the feature advertises more than it can prove) — counted and fails the run

A ``false_claims`` counter is reported explicitly (0 expected).  This mirrors
``make audit-evidence``: the bar is "every advertised artifact actually exists",
not "at least one does".

To stay fast and hermetic the ``test:`` prefix is dispatched to a file-existence
runner instead of spawning pytest — running the full suite per node id would be
slow and is already covered by ``make gate``.  Every other prefix
(``role:``/``module:``/``molecule:``/``file:``) is a real on-disk presence check
performed by the production ``FeatureVerifier``.

Exit 0 iff false_claims == 0; non-zero otherwise (fail closed).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Statuses that ADVERTISE the feature as real and therefore must fully verify.
_MUST_FULLY_VERIFY = {"verified", "implemented"}


def _log(msg: str) -> None:
    print(f"[dogfood-features] {msg}", flush=True)


def _file_existence_runner(node_id: str) -> int:
    """Runner for ``test:`` evidence: rc 0 iff the test FILE exists on disk.

    The dogfood self-check is a fast presence audit, not a test run (the suite is
    exercised by ``make gate``).  A node id may be ``tests/path.py`` or
    ``tests/path.py::Class::method`` — only the file part is checked.
    """
    file_part = node_id.split("::")[0]
    return 0 if (REPO_ROOT / file_part).exists() else 1


def run_dogfood_features() -> int:
    from general_ludd.db.feature_seed import FEATURE_SEED
    from general_ludd.quality.feature_verifier import FeatureVerifier

    verifier = FeatureVerifier(repo_root=str(REPO_ROOT), runner=_file_existence_runner)

    _log(
        f"verifying {len(FEATURE_SEED)} features in FEATURE_SEED against {REPO_ROOT} "
        "via the production FeatureVerifier (strict: ALL evidence must resolve)"
    )

    summary = verifier.verify_all(FEATURE_SEED)
    results_by_name = {r["name"]: r for r in summary["results"]}

    false_claims: list[str] = []
    warnings: list[str] = []
    report: list[dict[str, object]] = []

    for feat in FEATURE_SEED:
        name = feat.get("name", "?")
        claimed_status = str(feat.get("status", "requested")).lower()
        evidence: list[str] = feat.get("evidence") or []
        result = results_by_name[name]
        ev = result["evidence_results"]
        met_count = int(ev["met_count"])
        total_count = int(ev["total_count"])
        verified_status = str(result["status"])

        if claimed_status in _MUST_FULLY_VERIFY:
            # Fail-closed: empty evidence cannot support an implemented/verified claim.
            if total_count == 0:
                false_claims.append(
                    f"  FALSE-CLAIM [{name}] status={claimed_status} but evidence is empty "
                    "(fail-closed: empty evidence cannot support implemented/verified)"
                )
                report.append({"name": name, "outcome": "FALSE_CLAIM_EMPTY"})
                continue

            # Strict: EVERY advertised evidence ref must resolve.
            if met_count < total_count:
                unmet = [r for r in ev["per_ref"] if not r["met"]]
                false_claims.append(
                    f"  FALSE-CLAIM [{name}] status={claimed_status} but only "
                    f"{met_count}/{total_count} evidence refs met "
                    f"(verifier downgrades to '{verified_status}')"
                )
                for r in unmet:
                    false_claims.append(f"      UNMET {r['ref']}: {r['detail']}")
                report.append(
                    {"name": name, "outcome": "FALSE_CLAIM", "met": met_count, "total": total_count}
                )
            else:
                report.append(
                    {"name": name, "outcome": "PASS", "met": met_count, "total": total_count}
                )
                _log(f"  OK [{name}] status={claimed_status} {met_count}/{total_count} evidence refs met")
        else:
            # requested / regressed — informational only.
            report.append(
                {"name": name, "outcome": "SKIP_REQUESTED", "met": met_count, "total": total_count}
            )
            if evidence and met_count > 0:
                warnings.append(
                    f"  WARN [{name}] status={claimed_status} but {met_count}/{total_count} "
                    "evidence refs met (consider upgrading status to implemented)"
                )

    # Summary
    _log("=== DOGFOOD-FEATURES SUMMARY ===")
    pass_count = sum(1 for r in report if r["outcome"] == "PASS")
    skip_count = sum(1 for r in report if str(r["outcome"]).startswith("SKIP"))
    false_claim_count = sum(1 for r in report if str(r["outcome"]).startswith("FALSE_CLAIM"))
    _log(f"  Total features: {len(FEATURE_SEED)}")
    _log(f"  PASS: {pass_count}  FALSE-CLAIMS: {false_claim_count}  SKIP(requested): {skip_count}")
    _log(
        "  verify_all: "
        f"verified={summary['verified_count']} implemented={summary['implemented_count']} "
        f"requested={summary['requested_count']} regressed={summary['regressed_count']}"
    )

    for w in warnings:
        _log(w)

    false_claim_features = [r for r in report if str(r["outcome"]).startswith("FALSE_CLAIM")]
    if false_claim_features:
        _log(f"FALSE CLAIMS DETECTED ({len(false_claim_features)} feature(s)):")
        for line in false_claims:
            _log(line)
        _log("=== DOGFOOD-FEATURES: FAILED (false_claims > 0) ===")
        return 1

    _log(
        "=== DOGFOOD-FEATURES: PASSED — false_claims=0; every implemented/verified "
        "feature fully resolves all advertised evidence ==="
    )
    return 0


def main() -> int:
    try:
        return run_dogfood_features()
    except Exception as exc:
        _log(f"DOGFOOD-FEATURES aborted: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
