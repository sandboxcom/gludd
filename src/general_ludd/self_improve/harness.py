"""Self-improvement harness — detects gaps, generates fix todos, enqueues them."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

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
    # Profile used for the model-driven gap-analysis call against the real
    # ModelGateway.call_model(profile_id, messages) interface. Overridable per
    # instance so an operator can point gap-analysis at a cheaper/analysis model.
    DEFAULT_MODEL_PROFILE: str = "default"

    def __init__(
        self,
        repo_root: str | None = None,
        model_gateway: Any | None = None,
        model_profile_id: str | None = None,
    ) -> None:
        self.repo_root = repo_root or os.getcwd()
        self._todos: list[dict[str, Any]] = []
        self._model_gateway = model_gateway
        self._model_profile_id = model_profile_id or self.DEFAULT_MODEL_PROFILE

    _GAP_PROMPT: str = (
        "Analyze this codebase for gaps and return a JSON array of findings.\n"
        'Each finding: {"title": str, "description": str,'
        ' "priority": "high|medium|low", "tier": "config|code|test"}\n'
        "Return ONLY the JSON array, no other text."
    )

    def _invoke_gateway(self, prompt: str) -> str:
        """Call the model gateway and return its text content.

        Tolerant of two gateway shapes:
          * the real ``ModelGateway.call_model(profile_id, messages, ...)``
            (preferred — this is what production wires in); and
          * a simpler ``complete(prompt)`` adapter/fake.
        Both are expected to return an object exposing a ``.content`` string.
        """
        gw = self._model_gateway
        if gw is None:  # pragma: no cover - guarded by caller
            raise RuntimeError("no model gateway configured")
        if hasattr(gw, "call_model"):
            response = gw.call_model(
                self._model_profile_id,
                [{"role": "user", "content": prompt}],
            )
        else:
            # Adapter / test-fake path.
            response = gw.complete(prompt)
        return str(response.content)

    def run_gap_analysis(self) -> list[dict[str, Any]]:
        if self._model_gateway is not None:
            try:
                import json
                content = self._invoke_gateway(self._GAP_PROMPT)
                parsed = json.loads(_strip_json_fences(content))
                if isinstance(parsed, list):
                    return parsed
                logger.warning(
                    "model gateway returned non-list JSON; falling back to static analysis"
                )
            except Exception as exc:
                logger.warning(
                    "model gateway gap-analysis failed (%s); falling back to static analysis",
                    exc,
                )

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

    def write_config_value(
        self,
        path: str,
        key: str,
        value: Any,
        *,
        writer: Any = None,
        reloader: Any = None,
    ) -> str:
        """Atomically set a dot-notation key in a YAML config file and emit a reload.

        Reuses self_update.applier.SafeWriter semantics: validate-as-YAML then
        write. If no writer is injected, uses an internal atomic temp-file+os.replace
        writer. If a reloader (reload.hot_reloader.HotReloader) is injected, calls
        reloader.reload(ReloadScope.CONFIG) after a successful write.
        Fail-closed: a write or reload error raises (caller decides).
        """
        import contextlib
        import os
        import tempfile

        import yaml

        # Read existing YAML or start fresh
        if os.path.isfile(path):
            with open(path) as fh:
                data: dict[str, Any] = yaml.safe_load(fh.read()) or {}
        else:
            data = {}

        # Set dot-notation key (e.g. "self_improve.max_open")
        parts = key.split(".")
        node: Any = data
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                if isinstance(node, dict):
                    node[part] = {}
                node = node[part] if isinstance(node, dict) else {}
            else:
                node = node[part]
        if isinstance(node, dict):
            node[parts[-1]] = value

        content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)

        # Write — injected writer or internal atomic write
        if writer is not None:
            writer.write(path, content)
        else:
            dir_name = os.path.dirname(os.path.abspath(path))
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise

        # Reload if reloader provided
        if reloader is not None:
            try:
                from general_ludd.reload.hot_reloader import ReloadScope
            except ImportError:
                # Soft dep — skip reload if module unavailable
                pass
            else:
                reloader.reload(ReloadScope.CONFIG)

        return cast(str, content)
