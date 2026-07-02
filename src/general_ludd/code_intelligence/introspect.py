"""Codebase introspection — composes genuinely-capturable self-knowledge signals.

``CodebaseIntrospector`` builds a single bounded snapshot of facts the agent can
honestly know about its own repo, for the self-improvement pipeline to pick a
high-value target (low coverage ∩ high churn ∩ debt). Every facet is
best-effort: a facet returns ``None`` when its source is unavailable (no git, no
coverage.xml, no .gate-status) rather than fabricating data.

Facets and their REAL sources:
  * churn          -> GitIntelligence.hot_files (git log)
  * complexity     -> stdlib ``ast`` over src modules (func count / max nesting /
                      branch count) — no external tool
  * coverage       -> parse coverage.xml if present
  * debt           -> parse .gate-status if present (lint / typecheck counts)
  * dead_code      -> SelfImprovementHarness completion-audit findings
  * missing_tests  -> SelfImprovementHarness missing-test findings
  * perf_cost      -> RecentTracesBuffer.snapshot()['by_phase'] (injected; the
                      live in-process trace buffer)
  * recent_failures-> TaskReturnRepository.history_summary (injected by the
                      async facts router, which already has DB access)

The buffer and history are injected because they are live/async app state; the
introspector stays synchronous and importable without a running daemon.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import os
import xml.etree.ElementTree as ET
from typing import Any

from general_ludd.code_intelligence.git_intel import GitIntelligence
from general_ludd.self_improve.harness import SelfImprovementHarness

logger = logging.getLogger(__name__)

# Bounds so the snapshot stays usable inside playbook when:/vars: conditions.
_MAX_COMPLEXITY_MODULES = 40
_MAX_HOT_FILES = 15
_MAX_LOW_COVERAGE = 25
_MAX_FINDINGS = 25
_LOW_COVERAGE_THRESHOLD = 0.85


class CodebaseIntrospector:
    """Builds a bounded, best-effort snapshot of codebase self-knowledge."""

    def __init__(
        self,
        repo_root: str,
        traces_buffer: Any = None,
        recent_failures: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> None:
        self.repo_root = repo_root
        self._traces_buffer = traces_buffer
        self._recent_failures = recent_failures
        # Tenant boundary (XT trace-leak fix): scope the perf_cost by-phase
        # aggregate to the caller's project so /api/facts?project_id=A cannot
        # surface cross-tenant trace cost/token totals. None = unscoped/global.
        self._project_id = project_id

    def snapshot(self) -> dict[str, Any]:
        # Compute harness findings once (the gap analysis walks the whole src
        # tree) and split into the dead_code / missing_tests facets.
        findings = self._harness_findings()
        dead = [f for f in findings if f.get("type") == "dead_code"][:_MAX_FINDINGS]
        missing = [f for f in findings if f.get("type") == "missing_tests"][
            :_MAX_FINDINGS
        ]
        return {
            "churn": self._churn(),
            "complexity": self._complexity(),
            "coverage": self._coverage(),
            "debt": self._debt(),
            "dead_code": dead or None,
            "missing_tests": missing or None,
            "perf_cost": self._perf_cost(),
            "recent_failures": self._recent_failures,
        }

    # --- churn -----------------------------------------------------------
    def _churn(self) -> dict[str, Any] | None:
        try:
            gi = GitIntelligence(self.repo_root)
            hot = gi.hot_files(limit=_MAX_HOT_FILES)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("churn unavailable: %s", exc)
            return None
        if not hot:
            return None
        return {"hot_files": hot}

    # --- complexity ------------------------------------------------------
    def _complexity(self) -> dict[str, Any] | None:
        src_dir = os.path.join(self.repo_root, "src", "general_ludd")
        if not os.path.isdir(src_dir):
            return None
        modules: list[dict[str, Any]] = []
        for root, _dirs, files in os.walk(src_dir):
            for f in files:
                if not f.endswith(".py") or f == "__init__.py":
                    continue
                path = os.path.join(root, f)
                metrics = self._module_metrics(path)
                if metrics is not None:
                    metrics["module"] = os.path.relpath(path, self.repo_root)
                    modules.append(metrics)
        if not modules:
            return None
        # Most complex first (branch_count is a decent proxy).
        modules.sort(key=lambda m: m["branch_count"], reverse=True)
        return {"modules": modules[:_MAX_COMPLEXITY_MODULES]}

    def _module_metrics(self, path: str) -> dict[str, Any] | None:
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, UnicodeDecodeError, SyntaxError):
            return None
        func_count = 0
        branch_count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                func_count += 1
            if isinstance(node, ast.If | ast.For | ast.While | ast.Try):
                branch_count += 1
        return {
            "func_count": func_count,
            "branch_count": branch_count,
            "max_nesting": self._max_nesting(tree),
        }

    def _max_nesting(self, tree: ast.AST) -> int:
        nesting_nodes = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)

        def depth(node: ast.AST, current: int) -> int:
            best = current
            for child in ast.iter_child_nodes(node):
                if isinstance(child, nesting_nodes):
                    best = max(best, depth(child, current + 1))
                else:
                    best = max(best, depth(child, current))
            return best

        return depth(tree, 0)

    # --- coverage --------------------------------------------------------
    def _coverage(self) -> dict[str, Any] | None:
        coverage_xml = os.path.join(self.repo_root, "coverage.xml")
        if not os.path.isfile(coverage_xml):
            return None
        try:
            tree = ET.parse(coverage_xml)
        except (ET.ParseError, OSError):
            return None
        root = tree.getroot()
        overall = root.get("line-rate")
        low: list[dict[str, Any]] = []
        for cls in root.findall(".//classes/class"):
            filename = cls.get("filename", "")
            try:
                rate = float(cls.get("line-rate", "1.0"))
            except ValueError:
                continue
            if filename and rate < _LOW_COVERAGE_THRESHOLD:
                low.append({"file": filename, "line_rate": round(rate, 4)})
        low.sort(key=lambda c: c["line_rate"])
        result: dict[str, Any] = {"low_coverage": low[:_MAX_LOW_COVERAGE]}
        if overall is not None:
            with contextlib.suppress(ValueError):
                result["overall_line_rate"] = round(float(overall), 4)
        return result

    # --- debt ------------------------------------------------------------
    def _debt(self) -> dict[str, Any] | None:
        gate_status = os.path.join(self.repo_root, ".gate-status")
        if not os.path.isfile(gate_status):
            return None
        try:
            with open(gate_status, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        debt: dict[str, Any] = {}
        for line in lines:
            parts = line.split()
            # e.g. "lint PASS 0" / "typecheck FAIL 7"
            if len(parts) >= 3 and parts[0] in ("lint", "typecheck"):
                try:
                    debt[parts[0]] = int(parts[2])
                except ValueError:
                    debt[parts[0]] = None
        return debt or None

    # --- dead_code / missing_tests --------------------------------------
    def _harness_findings(self) -> list[dict[str, Any]]:
        try:
            harness = SelfImprovementHarness(repo_root=self.repo_root)
            return harness.run_gap_analysis()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("harness findings unavailable: %s", exc)
            return []

    # --- perf_cost -------------------------------------------------------
    def _perf_cost(self) -> dict[str, Any] | None:
        buffer = self._traces_buffer
        if buffer is None or not hasattr(buffer, "snapshot"):
            return None
        try:
            snap = buffer.snapshot(project_id=self._project_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("perf_cost unavailable: %s", exc)
            return None
        by_phase = snap.get("by_phase") if isinstance(snap, dict) else None
        if not by_phase:
            return None
        return {"by_phase": by_phase}
