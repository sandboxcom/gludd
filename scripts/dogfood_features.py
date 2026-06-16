#!/usr/bin/env python3
"""make dogfood-features — gludd verifies its OWN feature database.

Runs FeatureVerifier over FEATURE_SEED and asserts that every feature with
status 'verified' or 'implemented' has at least one evidence reference that
resolves to a real artifact in this repo.  Fail-closed: empty evidence NEVER
passes.

This is evidence-gated self-verification: the script checks ACTUAL files,
roles, modules, and molecule scenarios on disk — it never self-asserts.

Exit 0 if all implemented/verified features have at least one met evidence
reference; non-zero otherwise (fail closed).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    print(f"[dogfood-features] {msg}", flush=True)


def _check_evidence_ref(ref: str, repo_root: Path) -> tuple[bool, str]:
    """Dispatch a single evidence reference.  No subprocess — presence checks only."""
    if ref.startswith("module:"):
        name = ref[len("module:"):]
        modules_dir = (
            repo_root
            / "collections" / "ansible_collections" / "general_ludd"
            / "agent" / "plugins" / "modules"
        )
        for ext in ("", ".py"):
            if (modules_dir / f"{name}{ext}").exists():
                return True, f"module file found: {name}{ext}"
        return False, f"module not found: {name}"

    if ref.startswith("role:"):
        name = ref[len("role:"):]
        roles_root = repo_root / "collections"
        for candidate in roles_root.rglob(f"roles/{name}"):
            if candidate.is_dir():
                return True, f"role dir found: {candidate.relative_to(repo_root)}"
        return False, f"role dir not found: {name}"

    if ref.startswith("molecule:"):
        scenario = ref[len("molecule:"):]
        scenario_dir = repo_root / "molecule" / "playbooks" / scenario
        if scenario_dir.is_dir():
            return True, f"molecule scenario dir found: {scenario_dir.relative_to(repo_root)}"
        return False, f"molecule scenario not found: {scenario}"

    if ref.startswith("file:"):
        spec = ref[len("file:"):]
        if "::" not in spec:
            return False, f"file: evidence malformed (missing ::): {spec}"
        path_part, symbol = spec.split("::", 1)
        target = repo_root / path_part
        if not target.exists():
            return False, f"file not found: {path_part}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            if symbol in text:
                return True, f"symbol '{symbol}' found in {path_part}"
            return False, f"symbol '{symbol}' NOT found in {path_part}"
        except Exception as exc:
            return False, f"file read error: {exc}"

    if ref.startswith("test:"):
        # For the dogfood script we do NOT run pytest (that would be slow and
        # require uv run); instead we assert the test FILE exists on disk.
        # The real FeatureVerifier.verify_all() runs the tests; this self-check
        # only validates that evidence references resolve to actual artifacts.
        node_id = ref[len("test:"):]
        # node_id may be "tests/path.py" or "tests/path.py::Class::method"
        file_part = node_id.split("::")[0]
        test_file = repo_root / file_part
        if test_file.exists():
            return True, f"test file found: {file_part}"
        return False, f"test file not found: {file_part}"

    return False, f"unknown evidence prefix: {ref}"


def run_dogfood_features() -> int:
    from general_ludd.db.feature_seed import FEATURE_SEED

    _log(f"verifying {len(FEATURE_SEED)} features in FEATURE_SEED against {REPO_ROOT}")

    # Features we must verify (those claiming to be implemented or verified
    # must have at least one met evidence reference).
    must_have_evidence = {"verified", "implemented"}

    failures: list[str] = []
    warnings: list[str] = []
    report: list[dict[str, object]] = []

    for feat in FEATURE_SEED:
        name = feat.get("name", "?")
        status = str(feat.get("status", "requested")).lower()
        evidence: list[str] = feat.get("evidence") or []

        per_ref: list[dict[str, object]] = []
        met_count = 0

        # Fail-closed: if status is implemented/verified but evidence is empty
        if status in must_have_evidence and not evidence:
            failures.append(
                f"  FAIL [{name}] status={status} but evidence is empty (fail-closed: "
                "empty evidence cannot support implemented/verified)"
            )
            report.append({"name": name, "status": status, "outcome": "FAIL_EMPTY_EVIDENCE"})
            continue

        for ref in evidence:
            met, detail = _check_evidence_ref(ref, REPO_ROOT)
            per_ref.append({"ref": ref, "met": met, "detail": detail})
            if met:
                met_count += 1

        # For implemented/verified features: at least one evidence ref must be met
        if status in must_have_evidence:
            if met_count == 0:
                failures.append(
                    f"  FAIL [{name}] status={status} but 0/{len(evidence)} evidence refs met"
                )
                for r in per_ref:
                    failures.append(f"      {r['ref']}: {r['detail']}")
                report.append({"name": name, "status": status, "outcome": "FAIL", "met": 0, "total": len(evidence)})
            else:
                outcome = "PASS_ALL" if met_count == len(evidence) else "PASS_PARTIAL"
                report.append({
                    "name": name, "status": status, "outcome": outcome,
                    "met": met_count, "total": len(evidence),
                })
                _log(f"  OK [{name}] status={status} {met_count}/{len(evidence)} evidence refs met")
        else:
            # requested/regressed — just informational
            outcome = "SKIP_REQUESTED"
            report.append({
                "name": name, "status": status, "outcome": outcome,
                "met": met_count, "total": len(evidence),
            })
            if evidence and met_count > 0:
                warnings.append(
                    f"  WARN [{name}] status={status} but {met_count}/{len(evidence)} evidence refs met "
                    "(consider upgrading status to implemented)"
                )

    # Summary
    _log("=== DOGFOOD-FEATURES SUMMARY ===")
    pass_count = sum(1 for r in report if str(r["outcome"]).startswith("PASS"))
    fail_count = sum(1 for r in report if str(r["outcome"]).startswith("FAIL"))
    skip_count = sum(1 for r in report if str(r["outcome"]).startswith("SKIP"))
    _log(f"  Total features: {len(FEATURE_SEED)}")
    _log(f"  PASS: {pass_count}  FAIL: {fail_count}  SKIP(requested): {skip_count}")

    for w in warnings:
        _log(w)

    if failures:
        _log("FAILURES:")
        for f in failures:
            _log(f)
        _log("=== DOGFOOD-FEATURES: FAILED ===")
        return 1

    _log("=== DOGFOOD-FEATURES: PASSED — all implemented/verified features have met evidence ===")
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
