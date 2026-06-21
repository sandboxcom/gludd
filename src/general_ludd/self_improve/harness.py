"""Self-improvement harness — detects gaps, generates fix todos, enqueues them."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _strip_json_fences(text: str) -> str:
    """Strip ```json...``` or ```...``` code fences from a string."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence line (e.g. ```json or ```)
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    return text.strip()


class SelfImprovementHarness:
    def __init__(self, repo_root: str | None = None, model_gateway: Any | None = None) -> None:
        self.repo_root = repo_root or os.getcwd()
        self._todos: list[dict[str, Any]] = []
        self._model_gateway = model_gateway

    def run_gap_analysis(self) -> list[dict[str, Any]]:
        if self._model_gateway is not None:
            prompt = (
                "Analyze this codebase for gaps and return a JSON array of findings.\n"
                "Each finding: {\"title\": str, \"description\": str,"
                " \"priority\": \"high|medium|low\", \"tier\": \"config|code|test\"}\n"
                "Return ONLY the JSON array, no other text."
            )
            try:
                response = self._model_gateway.complete(prompt)
                import json
                parsed = json.loads(_strip_json_fences(response.content))
                if isinstance(parsed, list):
                    return parsed
                logger.warning("model_gateway.complete returned non-list JSON; falling back to static analysis")
            except Exception as exc:
                logger.warning("model_gateway.complete failed (%s); falling back to static analysis", exc)

        # Static fallback (or when model_gateway is None)
        findings: list[dict[str, Any]] = []
        self._check_missing_tests(findings)
        self._check_completion_audit(findings)
        self._check_coverage_gaps(findings)
        return findings

    def _check_missing_tests(self, findings: list[dict[str, Any]]) -> None:
        src_dir = os.path.join(self.repo_root, "src", "general_ludd")
        tests_dir = os.path.join(self.repo_root, "tests")
        if not os.path.isdir(src_dir) or not os.path.isdir(tests_dir):
            return

        test_files: set[str] = set()
        for _root, _dirs, files in os.walk(tests_dir):
            for f in files:
                if f.startswith("test_") and f.endswith(".py"):
                    test_files.add(f)

        for root, _dirs, files in os.walk(src_dir):
            for f in files:
                if f.endswith(".py") and f != "__init__.py":
                    test_name = f"test_{f}"
                    if test_name not in test_files:
                        findings.append({
                            "type": "missing_tests",
                            "file": os.path.join(root, f),
                            "severity": "high",
                            "message": f"{os.path.relpath(os.path.join(root, f), self.repo_root)} has no test file",
                        })

    def _check_completion_audit(self, findings: list[dict[str, Any]]) -> None:
        """Dead-code heuristic — DISABLED.

        The static class-reference count was unreliable (false positives on
        every single-definition class) and caused runaway self-modification.
        Disabled: method is kept for API compatibility but produces no findings.
        """
        logger.debug("_check_completion_audit: dead_code heuristic disabled — no findings appended")

    def _check_coverage_gaps(self, findings: list[dict[str, Any]]) -> None:
        coverage_xml = os.path.join(self.repo_root, "coverage.xml")
        if not os.path.isfile(coverage_xml):
            return

        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(coverage_xml)
            for pkg in tree.findall(".//package"):
                for cls in pkg.findall("classes/class"):
                    filename = cls.get("filename", "")
                    rate = float(cls.get("line-rate", "1.0"))
                    if rate < 0.85 and filename:
                        findings.append({
                            "type": "low_coverage",
                            "file": filename,
                            "severity": "medium",
                            "coverage_pct": round(rate * 100, 1),
                            "message": f"{filename} at {round(rate * 100, 1)}% coverage (below 85%)",
                        })
        except Exception:
            pass

    def _read_all_src(self) -> str:
        src_dir = os.path.join(self.repo_root, "src")
        all_text: list[str] = []
        for root, _dirs, files in os.walk(src_dir):
            for f in files:
                if f.endswith(".py"):
                    try:
                        with open(os.path.join(root, f)) as fh:
                            all_text.append(fh.read())
                    except (OSError, UnicodeDecodeError):
                        pass
        return "\n".join(all_text)

    def generate_fix_todos(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        todos: list[dict[str, Any]] = []
        for finding in findings:
            ftype = finding.get("type", "")
            title = self._build_title(finding)
            description = finding.get("message", "")
            priority = finding.get("severity", "medium")
            work_type = "test" if ftype == "missing_tests" else "code"
            if ftype == "low_coverage":
                work_type = "test"
                priority = "medium"

            todos.append({
                "title": title,
                "description": description,
                "work_type": work_type,
                "priority": priority,
                "source": "self_improve_harness",
                "gap_type": ftype,
                "source_file": finding.get("file", ""),
            })
        return todos

    def _build_title(self, finding: dict[str, Any]) -> str:
        ftype = finding.get("type", "")
        f = os.path.basename(finding.get("file", "unknown"))
        if ftype == "missing_tests":
            return f"Add tests for {f}"
        if ftype == "dead_code":
            cls = finding.get("class", "unknown")
            return f"Wire {cls} from {f} into pipeline"
        if ftype == "low_coverage":
            cov = finding.get("coverage_pct", 0)
            return f"Improve {f} coverage from {cov}% to 85%"
        return f"Fix: {finding.get('message', 'unknown gap')}"

    def enqueue_todos(self, todos: list[dict[str, Any]]) -> int:
        self._todos.extend(todos)
        return len(todos)

    def run_full_cycle(self, daemon_url: str = "http://localhost:8000") -> dict[str, Any]:
        findings = self.run_gap_analysis()
        todos = self.generate_fix_todos(findings)
        enqueued = self.enqueue_todos(todos)

        return {
            "findings_count": len(findings),
            "todos_generated": len(todos),
            "todos_enqueued": enqueued,
            "findings": findings,
            "todos": todos,
        }

    @staticmethod
    def _write_config_value(path: str, key: str, value: Any) -> None:
        """TODO: atomic YAML write + reload scaffold.

        Wire SafeWriter + HotReloader(config_dir=...).reload(ReloadScope.CONFIG) here.
        """
        raise NotImplementedError(
            "_write_config_value not yet implemented: "
            "wire SafeWriter + HotReloader(config_dir=...).reload(ReloadScope.CONFIG) here"
        )
