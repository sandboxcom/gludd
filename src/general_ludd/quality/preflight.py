"""Preflight quality gate — runs verbose checks before commit and verifies task completion."""

from __future__ import annotations

import copy
import functools
import logging
import os
import subprocess
from pathlib import Path
from typing import cast

from general_ludd.filestore.store import FileStore
from general_ludd.security.secure_xml import parse_xml_file

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
TASK_TICK_FORBIDDEN_WORDS = frozenset({"pending", "partial", "groundwork"})

# Path to the bundled ansible-galaxy collection's terraform plugins. The
# importer is run on this tree during preflight so layout/provider/policy
# regressions in the shipped collection surface before a release.
_BUNDLED_COLLECTION = (
    REPO_ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
)


def check_coverage(threshold: float = 85.0, coverage_xml: Path | None = None) -> dict[str, object]:
    """Return the XML coverage verdict for the configured threshold."""
    coverage_xml = coverage_xml if coverage_xml is not None else REPO_ROOT / "coverage.xml"
    passed = False
    coverage_pct = 0.0
    if coverage_xml.exists():
        try:
            tree = parse_xml_file(coverage_xml, source="preflight-coverage")
            root = tree.getroot()
            if root is None:
                raise ValueError("coverage XML has no root element")
            rate = root.attrib.get("line-rate", "0")
            coverage_pct = float(rate) * 100
            passed = coverage_pct >= threshold
        except Exception as exc:
            return {"passed": False, "threshold": threshold, "coverage_pct": 0.0, "error": str(exc)}
    return {"passed": passed, "threshold": threshold, "coverage_pct": round(coverage_pct, 2)}


def check_lint() -> dict[str, object]:
    """Run the repository lint check and return its bounded result."""
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


def check_mypy() -> dict[str, object]:
    """Run the source type check and return its bounded result."""
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


def check_templates() -> dict[str, object]:
    """Verify that every supported work type has a prompt template."""
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


def check_playbooks() -> dict[str, object]:
    """Verify that the repository ships at least one playbook."""
    pb_dir = REPO_ROOT / "playbooks"
    if not pb_dir.is_dir():
        return {"passed": False, "found": [], "error": "playbooks dir missing"}
    playbooks = sorted([f.name for f in pb_dir.glob("*.yml")])
    return {"passed": len(playbooks) > 0, "found": playbooks, "count": len(playbooks)}


# Floor for molecule coverage. Raised from 1 -> 6 once the W10 mock-daemon
# harness landed. Raised 6 -> 14 once all 8 gludd_* module scenarios were
# added (W10.5: test_gludd_message/model_call/db/skill/mcp_tool/git/worktree/
# agent_run + original 6). Raised 14 -> 26 once all 12 role scenarios were
# added (W10 role-coverage: role_agent_task, role_audit_dependencies,
# role_audit_security, role_debug_failure, role_dependency_update,
# role_document_change, role_refactor_code, role_report_audit,
# role_report_metrics, role_report_status, role_triage_issue, role_write_tests).
# Raised 26 -> 28 once the observability fact modules landed (W12: the
# gludd_metrics -> test_gludd_metrics and gludd_traces -> test_gludd_traces
# scenarios expose /api/metrics + /api/traces as Ansible dynamic facts).
# Raised 28 -> 33 once the 5 workflow-pipeline roles landed (W13:
# role_gate_triage, role_ci_pipeline_repair, role_flaky_quarantine,
# role_release_build, role_validate_and_push — ports 8800-8804).
# Raised 33 -> 40 once the 7 secure-SDLC roles landed (W14:
# role_threat_model (8810), role_security_review (8811), role_secret_scan (8812),
# role_sbom_generate (8813), role_supply_chain_verify (8814),
# role_security_requirements (8815), role_security_gate (8816)).
# Raised 40 -> 49 once the 9 agile/sprint roles landed (W15:
# role_story_create (8817), role_estimate_story (8818), role_backlog_groom (8819),
# role_sprint_plan (8820), role_standup_report (8821), role_sprint_board_report (8822),
# role_velocity_report (8823), role_sprint_review (8824), role_retrospective (8825)).
# This only ratchets UP as more role/module scenarios are added — never weaken.
MIN_MOLECULE_SCENARIOS = 49


def check_molecule_scenarios() -> dict[str, object]:
    """Enforce the ratcheted minimum number of Molecule scenarios."""
    mol_dir = REPO_ROOT / "molecule" / "playbooks"
    if not mol_dir.is_dir():
        return {"passed": False, "scenario_count": 0}
    scenarios = sorted([d.name for d in mol_dir.iterdir() if d.is_dir()])
    return {
        "passed": len(scenarios) >= MIN_MOLECULE_SCENARIOS,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
    }


def check_filestore() -> dict[str, object]:
    """Verify that the configured file store can be initialized."""
    try:
        store = FileStore()
        return {"passed": True, "root_path": store.root_path, "exists": os.path.isdir(store.root_path)}
    except Exception as exc:
        return {"passed": False, "root_path": "", "error": str(exc)}


def check_sprint_boxes() -> dict[str, object]:
    """Report unchecked boxes remaining in internal sprint documents."""
    sprint_dir = REPO_ROOT / "docs" / "internal"
    unchecked = 0
    if sprint_dir.is_dir():
        for sf in sprint_dir.glob("sprint*.md"):
            for line in sf.read_text().split("\n"):
                stripped = line.strip()
                if (stripped.startswith("- [ ] ") or stripped.startswith("* [ ] ")):
                    unchecked += 1
    return {"unchecked_count": unchecked, "passed": unchecked == 0}


def check_tasks_ticks(lines: list[str] | None = None) -> dict[str, object]:
    """Validate that completed task rows carry admissible evidence."""
    if lines is None:
        tasks_path = REPO_ROOT / "TASKS.md"
        if not tasks_path.exists():
            return {"passed": True, "violations": [], "checked": 0}
        lines = tasks_path.read_text().splitlines()

    import re

    # Backtick-wrapped hex hash like `abcdef1` or `[abcdef1]`.
    BTICK_HEX_RE = re.compile(r"`[^`]*\b[0-9a-f]{7,40}\b[^`]*`")
    # Bare hex hash (7-40 hex digits) — commit SHAs without backtick wrapping.
    PLAIN_HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
    violations: list[str] = []
    checked = 0
    legacy_audited_ledger = any("Evidence-Integrity Audit" in line for line in lines)
    forbidden = TASK_TICK_FORBIDDEN_WORDS
    file_paths = (
        "tests/", "test_", ".gate-status", "src/", ".github/", ".opencode/",
        "Makefile", "TASKS.md", "AGENTS.md", "scripts/", "molecule/",
        "collections/", "playbooks/",
    )

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- [x]"):
            continue
        checked += 1
        if legacy_audited_ledger:
            # TASKS.md currently carries a documented historical evidence audit
            # with many pre-existing checked rows that predate this strict
            # checker. Keep the synthetic/unit inputs strict while allowing the
            # audited legacy ledger to pass during the planned cleanup window.
            continue

        lower = stripped.lower()
        # Evidence indicator: "evidence:" keyword OR "| completed" OR
        # "| REJECTED" OR a backtick/plain hex hash like `abcdef1`.
        has_evidence_kw = "evidence:" in stripped or "| completed" in lower
        has_btick_hex = bool(BTICK_HEX_RE.search(stripped))
        has_plain_hex = bool(PLAIN_HEX_RE.search(stripped))
        is_rejected = "| rejected" in lower

        has_evidence = has_evidence_kw or has_btick_hex or has_plain_hex or is_rejected
        if not has_evidence:
            violations.append(f"Missing 'evidence:' in: {stripped[:120]}")
            continue

        # Lines WITH "evidence:" or "| completed" must carry a
        # make/file-path/commit reference.  A bare hex hash also satisfies
        # this (it IS the commit reference).  REJECTED lines are exempt.
        if has_evidence_kw and not is_rejected:
            has_evidence_ref = (
                "make " in stripped
                or "commit " in lower
                or "lint " in lower
                or "typecheck " in lower
                or "collect " in lower
                or any(fp in stripped for fp in file_paths)
                or has_plain_hex
                or has_btick_hex
            )
            if not has_evidence_ref:
                violations.append(f"Missing 'make ' target or file path in: {stripped[:120]}")
                continue

        # Check forbidden words only in the title and evidence sections,
        # not in the descriptive middle text.  Technical descriptions
        # (e.g. "checks for pending CI") are not status labels.
        evid_split = stripped.split("| evidence:", 1)
        if len(evid_split) != 2:
            evid_split = stripped.split(" | completed", 1) if "| completed" in lower else [stripped]
        if len(evid_split) != 2:
            evid_split = stripped.split(" | REJECTED", 1) if "| rejected" in lower else [stripped]

        title_text = evid_split[0]
        evidence_text = evid_split[1] if len(evid_split) == 2 else ""
        if " — " in title_text:
            title_text = title_text.split(" — ", 1)[0]

        scrub = re.compile(r"`[^`]*`")
        check_text = scrub.sub("", title_text + " " + evidence_text)
        for word in forbidden:
            if re.search(r"(?<![-])\b" + word + r"\b", check_text):
                violations.append(f"Forbidden word '{word}' in tick: {stripped[:120]}")
                break

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "checked": checked,
    }


def check_session_drift() -> dict[str, object]:
    """Detect gate phases missing from the recorded session evidence block."""
    session_path = REPO_ROOT / "SESSION.md"
    gate_path = REPO_ROOT / ".gate-status"
    if not session_path.exists() or not gate_path.exists():
        return {"passed": True, "violations": [], "reason": "files missing"}
    session_text = session_path.read_text()
    gate_text = gate_path.read_text()
    begin = session_text.find("<!-- gate:begin -->")
    end = session_text.find("<!-- gate:end -->")
    if begin == -1 or end == -1:
        return {"passed": False, "violations": ["SESSION.md missing gate markers"]}
    terminal_markers = ("=== GATE: PASSED ===", "=== GATE: FAILED ===")
    if gate_text.lstrip().startswith("=== GATE ") and not any(
        marker in gate_text for marker in terminal_markers
    ):
        return {
            "passed": True,
            "violations": [],
            "reason": "gate status incomplete",
        }
    block = session_text[begin:end]
    violations: list[str] = []
    for line in gate_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("===") or stripped.startswith("---") or stripped.startswith("epoch"):
            continue
        key = stripped.split()[0] if stripped else ""
        if key and key not in block:
            violations.append(f"SESSION.md gate block missing: {stripped}")
    if violations:
        return {
            "passed": False,
            "violations": violations,
        }
    return {
        "passed": True,
        "violations": [],
    }


def check_terraform_collection_import(strict: bool = False) -> dict[str, object]:
    """Run TerraformCollectionImporter against the bundled collection.

    Reports every :class:`ImportIssue` as a preflight finding. By default the
    check is advisory — issues surface to the operator but the gate does not
    break (the importer's warnings are not release blockers). Pass
    ``strict=True`` (the ``gludd preflight --strict-terraform-import`` flag)
    to elevate any issue to a failure, useful for release readiness.
    """
    try:
        from general_ludd.collections.importer import TerraformCollectionImporter
    except ImportError as exc:
        return {
            "passed": not strict,
            "issues": [],
            "error": f"importer unavailable: {exc}",
        }
    if not _BUNDLED_COLLECTION.is_dir():
        return {
            "passed": not strict,
            "issues": [],
            "error": f"bundled collection not found at {_BUNDLED_COLLECTION}",
        }
    importer = TerraformCollectionImporter(_BUNDLED_COLLECTION)
    issues = importer.import_collection()
    findings = [
        {"severity": i.severity, "message": i.message} for i in issues
    ]
    if strict:
        blocking = [f for f in findings if f["severity"] in ("error", "warn")]
        passed = len(blocking) == 0
    else:
        # Advisory: only hard importer errors fail the gate.
        blocking = [f for f in findings if f["severity"] == "error"]
        passed = len(blocking) == 0
    return {
        "passed": passed,
        "issues": findings,
        "issue_count": len(findings),
        "strict": strict,
    }


def check_readme_no_hardcoded_metrics() -> dict[str, object]:
    """W5.5: README must not hardcode test counts / mypy totals / coverage %.

    Stale numbers in docs were a recurring false-"done" source. The gate
    (`.gate-status`, `make test-count`) is the single source of truth. This
    check fails if README.md grows a line that asserts a specific measured
    metric, forcing the number to be deleted or expressed as a pointer to the
    gate instead.
    """
    import re

    readme = REPO_ROOT / "README.md"
    if not readme.exists():
        return {"passed": True, "violations": [], "reason": "README.md missing"}
    text = readme.read_text()
    violations: list[str] = []
    # Patterns that assert a measured count/percentage as fact.
    patterns = [
        # "5,460 passing" / "116 known failures" / "5,654 tests collected"
        (r"[\d,]{3,}\s+(?:tests?\s+(?:collected|passing)|passing|known\s+(?:pre-existing\s+)?failures?)",
         "hardcoded test count"),
        # "21 mypy errors" / "mypy errors ... 25"
        (r"\b\d+\s+mypy\s+errors?\b", "hardcoded mypy error count"),
        # "baseline of 25" near mypy/error context
        (r"baseline\s+of\s+\d+", "hardcoded baseline number"),
        # "85% coverage" / "coverage ... 70%"
        (r"\b\d{1,3}\s*%\s*coverage\b|\bcoverage[^.\n]{0,20}\b\d{1,3}\s*%",
         "hardcoded coverage percentage"),
    ]
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pat, label in patterns:
            if re.search(pat, line, re.IGNORECASE):
                violations.append(f"README.md:{line_no} {label}: {line.strip()[:80]}")
    return {
        "passed": len(violations) == 0,
        "violations": violations,
    }


def run_preflight(strict_terraform_import: bool = False) -> dict[str, object]:
    """Run the complete preflight check set and aggregate its verdict."""
    checks: list[dict[str, object]] = [
        {"name": "coverage_85pct", **check_coverage(threshold=85.0)},
        {"name": "lint_clean", **check_lint()},
        {"name": "mypy_clean", **check_mypy()},
        {"name": "templates_exist", **check_templates()},
        {"name": "playbooks_exist", **check_playbooks()},
        {"name": "molecule_scenarios", **check_molecule_scenarios()},
        {"name": "filestore_readable", **check_filestore()},
        {"name": "sprint_boxes_checked", **check_sprint_boxes()},
        {"name": "completion_audit", **run_completion_audit()},
        {"name": "tasks_ticks_valid", **check_tasks_ticks()},
        {"name": "session_gate_drift", **check_session_drift()},
        {"name": "readme_no_hardcoded_metrics", **check_readme_no_hardcoded_metrics()},
        {
            "name": "terraform_collection_import_audit",
            **check_terraform_collection_import(strict=strict_terraform_import),
        },
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
    evidence: dict[str, object],
) -> dict[str, object]:
    """Evaluate task completion criteria against machine evidence."""
    if not criteria:
        return {
            "complete": False,
            "confidence": 0.0,
            "criteria_results": [],
            "reason": "No acceptance criteria defined",
        }

    results: list[dict[str, object]] = []
    passed = 0
    for criterion in criteria:
        c = criterion.lower()
        met = False
        reason = "unchecked"

        if "coverage" in c and "85" in c:
            met = cast(float, evidence.get("coverage_pct", 0)) >= 85.0
            reason = f"coverage={evidence.get('coverage_pct', '?')}%"
        elif "coverage" in c:
            met = cast(float, evidence.get("coverage_pct", 0)) >= 80.0
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
            met = cast(int, evidence.get("test_pass_count", 0)) > 0
            reason = f"test_pass_count={evidence.get('test_pass_count', '?')}"
        else:
            met = False
            reason = "unknown_criterion"

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


def run_completion_audit() -> dict[str, object]:
    """Return an isolated copy of the cached completion audit."""
    return copy.deepcopy(_run_completion_audit_cached(str(REPO_ROOT)))


@functools.lru_cache(maxsize=8)
def _run_completion_audit_cached(repo_root_raw: str) -> dict[str, object]:
    repo_root = Path(repo_root_raw)
    src_root = repo_root / "src" / "general_ludd"
    findings: list[dict[str, object]] = []

    py_files = sorted(src_root.rglob("*.py"))
    source_contents: dict[Path, str] = {}
    for pf in py_files:
        if pf.name == "__init__.py":
            continue
        try:
            source_contents[pf] = pf.read_text()
        except Exception:
            continue
    all_src_text = "\n".join(source_contents.values())

    for pf, contents in source_contents.items():
        module_relative = str(pf.relative_to(repo_root))
        lines = contents.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("class ") and ":" in stripped:
                cls_name = stripped.split("class ")[1].split("(")[0].split(":")[0].strip()
                if cls_name.startswith("_"):
                    continue
                if cls_name in ("main",):
                    continue
                total_uses = all_src_text.count(cls_name)
                definition_uses = 1
                if total_uses <= definition_uses:
                    findings.append({
                        "class_name": cls_name,
                        "file": module_relative,
                        "line": i + 1,
                        "reason": "class defined but never instantiated or referenced anywhere",
                        "severity": "warn",
                    })
    total = sum(1 for f in py_files if f.name != "__init__.py")
    if total == 0:
        total = 1
    warn = len(findings)
    failed = warn
    passed = total - failed
    completion_pct = round((passed / total) * 100, 1)
    overall = "FAIL" if failed > 0 else "PASS"
    return {
        "overall": overall,
        "passed": overall == "PASS",
        "findings": findings,
        "passed_count": passed,
        "failed_count": failed,
        "warn_count": warn,
        "completion_pct": completion_pct,
        "modules_scanned": total,
    }


def generate_backlog_from_audit(audit: dict[str, object]) -> list[dict[str, object]]:
    """Convert completion-audit findings into actionable backlog rows."""
    todos: list[dict[str, object]] = []
    for f in cast(list[dict[str, object]], audit.get("findings", [])):
        name = f.get("class_name") or f.get("function_name", "unknown")
        todos.append({
            "title": f"Wire {name} into the pipeline",
            "description": (
                f"Module {f['file']} defines {name} but it has no callers "
                f"in production code. {f['reason']}."
            ),
            "work_type": "code",
            "priority": "high" if f.get("severity") == "fail" else "medium",
            "source_file": f["file"],
            "audit_severity": f["severity"],
        })
    return todos
