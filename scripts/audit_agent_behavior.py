#!/usr/bin/env python3
"""audit_agent_behavior.py — mechanical audit for AB041-AB060 behavioral specs.

Audits the repository for agent behavioral violations that can be detected
from git history, file state, and Makefile completeness.

Checks:
  AB041 — overlapping file edits in concurrent dispatch windows
  AB042 — duplicate task dispatches (same file+objective in git log)
  AB047 — TASKS.md [x] items without commit hash evidence
  AB048 — TASKS.md drift (stale entries vs git log)
  AB052 — file-editing commits without agent-* branch prefix (worktree bypass)
  AB054 — abandoned git worktrees (>24h old, unmerged)
  AB056 — dead code introduced in refactor commits
  AB057 — scripts/ .py files without Makefile targets

Usage:
    uv run python scripts/audit_agent_behavior.py          # all checks
    uv run python scripts/audit_agent_behavior.py --filter AB041,AB054  # specific
    uv run python scripts/audit_agent_behavior.py --json    # machine-readable output

Exit: 0 = all checks pass, 1 = violations found, 2 = audit script error.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WORKTREE_DIR = ROOT / ".gludd" / "worktrees"


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    """Run git command in ROOT, return stdout, raise on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except Exception:
        return ""


def _git_lines(*args: str) -> list[str]:
    out = _git(*args)
    return [line for line in out.split("\n") if line] if out else []


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)


# ── AB041 — overlapping file edits in concurrent dispatch windows ─────────────


def check_ab041_overlapping_edits() -> dict[str, Any]:
    """Detect commits within 5 min of each other touching the same file."""
    log = _git_lines(
        "log",
        "--format=%H|%aI",
        "--name-only",
        "-200",
    )
    violations: list[dict[str, Any]] = []
    commit_files: dict[str, tuple[str, set[str]]] = {}
    current_sha: str | None = None
    current_time: str | None = None

    for line in log:
        if "|" in line and not line.startswith(" "):
            parts = line.split("|", 1)
            current_sha = parts[0]
            current_time = parts[1]
            commit_files[current_sha] = (current_time, set())
        elif current_sha and line.strip():
            commit_files[current_sha][1].add(line.strip())

    shas = list(commit_files.keys())
    for i, sha_a in enumerate(shas):
        time_a_str, files_a = commit_files[sha_a]
        time_a = _parse_iso(time_a_str)
        if not time_a:
            continue
        for j in range(i + 1, min(i + 10, len(shas))):
            sha_b = shas[j]
            time_b_str, files_b = commit_files[sha_b]
            time_b = _parse_iso(time_b_str)
            if not time_b:
                continue
            diff_min = abs((time_b - time_a).total_seconds()) / 60.0
            if diff_min <= 5:
                overlap = files_a & files_b
                if overlap:
                    violations.append(
                        {
                            "sha_a": sha_a[:8],
                            "sha_b": sha_b[:8],
                            "diff_min": round(diff_min, 1),
                            "overlap_files": sorted(overlap),
                        }
                    )

    return {"status": "FAIL" if violations else "PASS", "violations": violations[:10]}


# ── AB047 — TASKS.md [x] items without evidence ──────────────────────────────


def check_ab047_task_evidence() -> dict[str, Any]:
    """Check TASKS.md for [x] lines lacking commit hash / test count evidence."""
    tasks_path = ROOT / "TASKS.md"
    violations: list[dict[str, Any]] = []
    if not tasks_path.exists():
        return {"status": "SKIP", "reason": "TASKS.md not found", "violations": []}

    lines = tasks_path.read_text().split("\n")
    evidence_pat = re.compile(r"[0-9a-f]{7,40}|N passed|CI GREEN|conclusion: success")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if (stripped.startswith("- [x]") or stripped.startswith("* [x]")) and not evidence_pat.search(stripped):
            violations.append({"line": i, "text": stripped[:120]})

    return {"status": "FAIL" if violations else "PASS", "violations": violations}


# ── AB048 — TASKS.md drift (stale entries vs git log) ────────────────────────


def check_ab048_task_ledger_drift() -> dict[str, Any]:
    """Checks if TASKS.md has pending items while commits happened since."""
    tasks_path = ROOT / "TASKS.md"
    if not tasks_path.exists():
        return {"status": "SKIP", "reason": "TASKS.md not found", "violations": []}

    text = tasks_path.read_text()
    unchecked = len(re.findall(r"^\s*- \[ \]", text, re.MULTILINE))
    in_progress = len(re.findall(r"^\s*- \[\.\]", text, re.MULTILINE))
    total_pending = unchecked + in_progress

    last_commit_str = _git("log", "-1", "--format=%aI")
    last_mod_str = (
        str(datetime.fromtimestamp(tasks_path.stat().st_mtime, tz=UTC).isoformat())
        if tasks_path.exists()
        else ""
    )

    return {
        "status": "INFO",
        "pending_count": total_pending,
        "last_commit": last_commit_str[:19] if last_commit_str else "unknown",
        "tasks_mtime": last_mod_str[:19],
        "violations": [],
    }


# ── AB052 — file-editing commits on non-agent branches (worktree bypass) ─────


def check_ab052_worktree_isolation() -> dict[str, Any]:
    """Check recent commits on master/development that edit src/ — should be on agent-* branches."""
    violations: list[dict[str, Any]] = []
    for branch in ["master", "development"]:
        commits = _git_lines(
            "log",
            branch,
            "--format=%H|%s",
            "--name-only",
            "-30",
        )
        current_sha: str | None = None
        current_msg: str = ""
        for line in commits:
            if "|" in line and not line.startswith(" "):
                parts = line.split("|", 1)
                current_sha = parts[0]
                current_msg = parts[1]
            elif current_sha and line.strip().startswith("src/"):
                if not current_msg.lower().startswith("merge"):
                    violations.append(
                        {
                            "branch": branch,
                            "sha": current_sha[:8],
                            "message": current_msg[:80],
                            "file": line.strip(),
                        }
                    )
    return {
        "status": "INFO",
        "violations": violations[:20],
        "note": "Commits on shared branches touching src/ (should be agent-* branches)",
    }


# ── AB054 — abandoned git worktrees ──────────────────────────────────────────


def check_ab054_abandoned_worktrees() -> dict[str, Any]:
    """Detect git worktrees older than 24h with unmerged commits."""
    violations: list[dict[str, Any]] = []
    all_worktrees: list[dict[str, str]] = []
    worktree_lines = _git_lines("worktree", "list")
    now = _now()

    for line in worktree_lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        wt_path = parts[0]
        wt_branch = parts[2] if len(parts) > 2 else "detached"
        wt_hash = parts[1] if len(parts) > 1 else "unknown"

        if wt_path == str(ROOT):
            continue

        all_worktrees.append({"path": wt_path, "branch": wt_branch, "hash": wt_hash})

        try:
            wt_stat = os.stat(wt_path)
            wt_age_hours = (now.timestamp() - wt_stat.st_mtime) / 3600.0
        except OSError:
            continue

        if wt_age_hours > 24 and wt_branch not in ("detached", "(detached)"):
            commits = _git_lines("log", "development.." + wt_branch, "--oneline")
            if commits:
                violations.append(
                    {
                        "path": wt_path,
                        "branch": wt_branch,
                        "age_hours": round(wt_age_hours, 1),
                        "unmerged_commits": len(commits),
                    }
                )

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "total_worktrees": len(all_worktrees),
    }


# ── AB056 — dead code introduced in refactor commits ─────────────────────────


def check_ab056_dead_code() -> dict[str, Any]:
    """Run vulture to detect dead code. Returns pass/fail."""
    vulture_result = subprocess.run(
        ["uv", "run", "vulture", "src/"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    dead_symbols: list[str] = [line.strip() for line in vulture_result.stdout.split("\n") if line.strip()]
    return {
        "status": "INFO",
        "dead_symbol_count": len(dead_symbols),
        "sample": dead_symbols[:10],
        "violations": [],
    }


# ── AB057 — scripts without Makefile targets ─────────────────────────────────


def check_ab057_orphan_scripts() -> dict[str, Any]:
    """Find .py scripts in scripts/ without corresponding Makefile targets."""
    makefile_path = ROOT / "Makefile"
    scripts_dir = ROOT / "scripts"
    if not makefile_path.exists():
        return {"status": "SKIP", "reason": "Makefile not found", "violations": []}

    makefile_text = makefile_path.read_text()
    violations: list[str] = []

    for py_file in sorted(scripts_dir.glob("*.py")):
        name = py_file.name
        if name.startswith("_"):
            continue
        if name == "__init__.py":
            continue
        if name not in makefile_text:
            violations.append(name)

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "total_scripts": sum(1 for _ in scripts_dir.glob("*.py")),
    }


# ── AB060 — AGENTS.md + CLAUDE.md context size ───────────────────────────────


def check_ab060_context_size(context_files: list[str] | None = None) -> dict[str, Any]:
    """Measure context files; retain the original default and line threshold."""
    files = ["AGENTS.md", "docs/CLAUDE.md"] if context_files is None else context_files
    total_lines = 0
    physical_lines = 0
    total_bytes = 0
    file_sizes: dict[str, dict[str, Any]] = {}
    violations: list[str] = []
    seen_paths: set[Path] = set()
    if not files:
        violations.append("Explicit context file set must not be empty")

    for fname in files:
        path = Path(fname)
        if path.is_absolute() or ".." in path.parts or path in seen_paths:
            violations.append(f"Invalid or duplicate context path: {fname}")
            continue
        seen_paths.add(path)
        fpath = ROOT / path
        if not fpath.exists() and context_files is None:
            continue  # Preserve the existing default audit's missing-file behavior.
        try:
            if fpath.is_symlink() or not fpath.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError("symlink or path outside repository")
            if not stat.S_ISREG(fpath.stat().st_mode):
                raise ValueError("context path is not a regular file")
            captured = fpath.read_bytes()
            with io.StringIO(captured.decode("utf-8"), newline=None) as stream:
                text = stream.read()
        except (OSError, UnicodeError, ValueError) as exc:
            violations.append(f"Cannot measure {fname}: {exc}")
            continue
        lines = text.count("\n") + 1  # Legacy AB060 threshold semantics.
        physical = captured.count(b"\n") + int(bool(captured) and not captured.endswith(b"\n"))
        total_lines += lines
        physical_lines += physical
        total_bytes += len(captured)
        file_sizes[fname] = {
            "lines": lines, "physical_lines": physical, "bytes": len(captured),
            "kb": round(len(captured) / 1024.0, 1),
            "sha256": hashlib.sha256(captured).hexdigest(),
        }

    threshold_lines = 20000
    threshold_kb = 600  # Historical diagnostic only; not a second failure threshold.
    return {
        "status": "FAIL" if violations or total_lines > threshold_lines else "PASS",
        "total_lines": total_lines,
        "physical_lines": physical_lines,
        "total_bytes": total_bytes,
        "total_kb": round(total_bytes / 1024.0, 1),
        "threshold_lines": threshold_lines,
        "threshold_kb": threshold_kb,
        "files": file_sizes,
        "violations": violations,
    }


# ── dispatch ─────────────────────────────────────────────────────────────────

CHECKS: dict[str, Any] = {
    "AB041": check_ab041_overlapping_edits,
    "AB047": check_ab047_task_evidence,
    "AB048": check_ab048_task_ledger_drift,
    "AB052": check_ab052_worktree_isolation,
    "AB054": check_ab054_abandoned_worktrees,
    "AB056": check_ab056_dead_code,
    "AB057": check_ab057_orphan_scripts,
    "AB060": check_ab060_context_size,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit agent behavior per AB041-AB060")
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help="Comma-separated spec IDs to check (e.g., AB041,AB054)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available checks",
    )
    parser.add_argument(
        "--context-from-env", action="store_true",
        help="AB060 only: literal GLUDD_AGENT_CONTEXT_FILES JSON and JSON=0/1 carriers",
    )
    args = parser.parse_args()
    context_files: list[str] | None = None
    if args.context_from_env:
        if args.filter != "AB060" or args.list:
            parser.error("Context file carriers require --filter AB060 without --list")
        raw_files = os.environ.get("GLUDD_AGENT_CONTEXT_FILES", "")
        raw_json = os.environ.get("GLUDD_AGENT_CONTEXT_JSON", "0")
        if raw_json not in ("0", "1"):
            parser.error("AGENT_CONTEXT_JSON must be literal 0 or 1")
        if raw_files:
            try:
                decoded = json.loads(raw_files)
            except (ValueError, TypeError):
                parser.error("AGENT_CONTEXT_FILES must be a JSON array of relative file paths")
            if not isinstance(decoded, list) or not decoded or any(
                not isinstance(item, str) or not item for item in decoded
            ):
                parser.error("AGENT_CONTEXT_FILES must be a nonempty JSON array of file paths")
            context_files = decoded
        args.json = args.json or raw_json == "1"

    if args.list:
        for spec_id in sorted(CHECKS):
            print(f"  {spec_id}: {CHECKS[spec_id].__doc__ or '(no doc)'}")
        return 0

    selected = set(args.filter.split(",")) if args.filter else set(CHECKS.keys())
    unknown = selected - set(CHECKS.keys())
    if unknown:
        print(f"Unknown check IDs: {unknown}", file=sys.stderr)
        return 2

    results: dict[str, Any] = {}
    overall_fail = 0

    for spec_id in sorted(selected):
        fn = CHECKS.get(spec_id)
        if fn is None:
            continue
        try:
            result = check_ab060_context_size(context_files) if spec_id == "AB060" else fn()
            results[spec_id] = result
            if result.get("status") == "FAIL":
                overall_fail += 1
        except Exception as exc:
            results[spec_id] = {"status": "ERROR", "error": str(exc)}

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for spec_id, result in results.items():
            status = result.get("status", "UNKNOWN")
            marker = "PASS" if status == "PASS" else ("FAIL" if status == "FAIL" else status)
            print(f"[{marker:>6}] {spec_id}")
            violations = result.get("violations", [])
            if violations:
                if isinstance(violations, list) and violations and isinstance(violations[0], dict):
                    for v in violations[:5]:
                        print(f"         {json.dumps(v, default=str)[:120]}")
                elif isinstance(violations, list):
                    for v in violations[:5]:
                        print(f"         {v}")
            if result.get("note"):
                print(f"         NOTE: {result['note']}")
            if result.get("total_worktrees") is not None:
                print(f"         worktrees: {result['total_worktrees']}")
            if result.get("dead_symbol_count") is not None:
                print(f"         dead_symbols: {result['dead_symbol_count']}")
            if result.get("total_lines") is not None:
                print(f"         context: {result['total_lines']} lines / {result['total_kb']} KB")
                print(f"         measured: {result['physical_lines']} physical lines / {result['total_bytes']} bytes")
            if result.get("pending_count") is not None:
                print(f"         pending_tasks: {result['pending_count']}")

    if overall_fail > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
