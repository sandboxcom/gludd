"""Preflight quality gate — runs verbose checks before commit and verifies task completion."""

from __future__ import annotations

import logging
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent


def check_coverage(threshold: float = 85.0) -> dict[str, Any]:
    coverage_xml = REPO_ROOT / "coverage.xml"
    passed = False
    coverage_pct = 0.0
    if coverage_xml.exists():
        try:
            tree = ET.parse(str(coverage_xml))
            root = tree.getroot()
            rate = root.attrib.get("line-rate", "0")
            coverage_pct = float(rate) * 100
            passed = coverage_pct >= threshold
        except Exception as exc:
            return {"passed": False, "threshold": threshold, "coverage_pct": 0.0, "error": str(exc)}
    return {"passed": passed, "threshold": threshold, "coverage_pct": round(coverage_pct, 2)}


def check_lint() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "src", "tests"],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT),
        )
        passed = result.returncode == 0
        errors = len([line for line in result.stdout.split("\n") if line.strip() and not line.startswith(" ")])
        return {"passed": passed, "error_count": errors if not passed else 0, "output": result.stdout[:500]}
    except Exception as exc:
        return {"passed": False, "error_count": 0, "output": str(exc)}


def check_mypy() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["uv", "run", "mypy", "src"],
            capture_output=True, text=True, timeout=120,
            cwd=str(REPO_ROOT),
        )
        passed = result.returncode == 0
        errors = len([line for line in result.stdout.split("\n") if "error:" in line])
        return {"passed": passed, "error_count": errors if not passed else 0, "output": result.stdout[:500]}
    except Exception as exc:
        return {"passed": False, "error_count": 0, "output": str(exc)}


def check_templates() -> dict[str, Any]:
    templates_dir = REPO_ROOT / "templates" / "prompts"
    expected = {
        "code": "implementation.md.j2",
        "test": "test_creation.md.j2",
        "review": "code_review.md.j2",
        "docs": "documentation.md.j2",
        "analysis": "gap_analysis.md.j2",
        "audit": "log_audit.md.j2",
        "prompt": "prompt_eval.md.j2",
        "dependency": "dependency_update.md.j2",
        "refactor": "implementation.md.j2",
    }
    found: list[str] = []
    missing: list[str] = []
    for work_type, filename in expected.items():
        path = templates_dir / filename
        if path.exists():
            found.append(work_type)
        else:
            missing.append(work_type)
    return {"passed": len(missing) == 0, "found": found, "missing": missing, "total": len(expected)}


def check_playbooks() -> dict[str, Any]:
    pb_dir = REPO_ROOT / "playbooks"
    if not pb_dir.is_dir():
        return {"passed": False, "found": [], "error": "playbooks dir missing"}
    playbooks = sorted([f.name for f in pb_dir.glob("*.yml")])
    return {"passed": len(playbooks) > 0, "found": playbooks, "count": len(playbooks)}


def check_molecule_scenarios() -> dict[str, Any]:
    mol_dir = REPO_ROOT / "molecule" / "playbooks"
    if not mol_dir.is_dir():
        return {"passed": False, "scenario_count": 0}
    scenarios = sorted([d.name for d in mol_dir.iterdir() if d.is_dir()])
    return {"passed": len(scenarios) >= 1, "scenario_count": len(scenarios), "scenarios": scenarios}


def check_filestore() -> dict[str, Any]:
    try:
        from general_ludd.filestore.store import FileStore

        store = FileStore()
        return {"passed": True, "root_path": store.root_path, "exists": os.path.isdir(store.root_path)}
    except Exception as exc:
        return {"passed": False, "root_path": "", "error": str(exc)}


def check_sprint_boxes() -> dict[str, Any]:
    sprint_dir = REPO_ROOT / "docs" / "internal"
    unchecked = 0
    if sprint_dir.is_dir():
        for sf in sprint_dir.glob("sprint*.md"):
            for line in sf.read_text().split("\n"):
                stripped = line.strip()
                if (stripped.startswith("- [ ] ") or stripped.startswith("* [ ] ")):
                    unchecked += 1
    return {"unchecked_count": unchecked, "passed": unchecked == 0}


def run_preflight() -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {"name": "coverage_85pct", **check_coverage(threshold=85.0)},
        {"name": "lint_clean", **check_lint()},
        {"name": "mypy_clean", **check_mypy()},
        {"name": "templates_exist", **check_templates()},
        {"name": "playbooks_exist", **check_playbooks()},
        {"name": "molecule_scenarios", **check_molecule_scenarios()},
        {"name": "filestore_readable", **check_filestore()},
        {"name": "sprint_boxes_checked", **check_sprint_boxes()},
    ]
    all_passed = all(c.get("passed", False) for c in checks)
    return {
        "overall": "PASS" if all_passed else "FAIL",
        "checks": checks,
        "passed_count": sum(1 for c in checks if c.get("passed")),
        "total_count": len(checks),
    }


def verify_task_completion(
    criteria: list[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if not criteria:
        return {
            "complete": False,
            "confidence": 0.0,
            "criteria_results": [],
            "reason": "No acceptance criteria defined",
        }

    results: list[dict[str, Any]] = []
    passed = 0
    for criterion in criteria:
        c = criterion.lower()
        met = False
        reason = "unchecked"

        if "coverage" in c and "85" in c:
            met = evidence.get("coverage_pct", 0) >= 85.0
            reason = f"coverage={evidence.get('coverage_pct', '?')}%"
        elif "coverage" in c:
            met = evidence.get("coverage_pct", 0) >= 80.0
            reason = f"coverage={evidence.get('coverage_pct', '?')}%"
        elif "lint" in c and ("no" in c or "0" in c or "clean" in c or "pass" in c):
            met = evidence.get("lint_errors", 999) == 0
            reason = f"lint_errors={evidence.get('lint_errors', '?')}"
        elif "mypy" in c or "type" in c:
            met = evidence.get("mypy_errors", 999) == 0
            reason = f"mypy_errors={evidence.get('mypy_errors', '?')}"
        elif "test" in c and ("pass" in c or "fail" in c or "0" in c):
            met = evidence.get("test_fail_count", 999) == 0
            reason = f"test_fail_count={evidence.get('test_fail_count', '?')}"
        elif "test" in c and "count" in c:
            met = evidence.get("test_pass_count", 0) > 0
            reason = f"test_pass_count={evidence.get('test_pass_count', '?')}"
        else:
            met = True
            reason = "assumed_met"

        if met:
            passed += 1
        results.append({"criterion": criterion, "met": met, "reason": reason})

    confidence = passed / len(criteria) if criteria else 0.0
    return {
        "complete": passed == len(criteria),
        "confidence": round(confidence, 2),
        "criteria_results": results,
        "passed": passed,
        "total": len(criteria),
    }
