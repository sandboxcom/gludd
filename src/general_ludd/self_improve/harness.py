"""Self-improvement harness — detects gaps, generates fix todos, enqueues them."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from pathlib import Path
from typing import Any, TypedDict, cast

from general_ludd.self_improve.private_policy import (
    PolicyAccess,
    SelfImprovePolicyError,
    SelfImprovePrivacyPolicy,
    load_self_improve_policy,
)

logger = logging.getLogger(__name__)


class SelfImprovePolicyDecision(TypedDict):
    """Persistable privacy evidence that cannot disclose repository paths."""

    policy_digest: str | None
    allowed_count: int
    blocked_count: int
    path_hashes: tuple[str, ...]


def _strip_json_fences(text: str) -> str:
    """Strip ```json ... ``` fences from model output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop the opening fence line and closing fence line
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return text


def _rec_get(rec: Any, key: str, default: Any) -> Any:
    """Read *key* from a recurring-failure mapping or object.

    Objects include ``ChronicBlocker`` instances returned by BlockerDetector.
    """
    if isinstance(rec, dict):
        return rec.get(key, default)
    return getattr(rec, key, default)


class _HarnessPolicySupport:
    """Own repository binding and privacy-policy decisions for the harness."""

    # Profile used for the model-driven gap-analysis call against the real
    # ModelGateway.call_model(profile_id, messages) interface. Overridable per
    # instance so an operator can point gap-analysis at a cheaper/analysis model.
    DEFAULT_MODEL_PROFILE: str = "default"
    MAX_GAP_SOURCE_BYTES: int = 262_144
    _PATH_FIELDS: tuple[str, ...] = ("file", "path", "source_file")
    _PATH_LIST_FIELDS: tuple[str, ...] = (
        "files",
        "paths",
        "affected_files",
        "affected_paths",
    )

    def __init__(
        self,
        repo_root: str | None = None,
        model_gateway: Any | None = None,
        model_profile_id: str | None = None,
        project_id: str | None = None,
        project_base_dir: str | None = None,
    ) -> None:
        """Bind the harness to one repository and optional model gateway."""
        if repo_root is not None:
            self.repo_root = repo_root
        elif project_id is not None:
            from general_ludd.projects.workspace import ProjectWorkspace

            ws = ProjectWorkspace(project_id=project_id, base_dir=project_base_dir)
            self.repo_root = str(ws.repo_dir)
        else:
            self.repo_root = os.getcwd()
        self._todos: list[dict[str, Any]] = []
        self._model_gateway = model_gateway
        self._model_profile_id = model_profile_id or self.DEFAULT_MODEL_PROFILE
        self._decision_digest: str | None = None
        self._decision_allowed_count = 0
        self._decision_blocked_count = 0
        self._decision_path_hashes: set[str] = set()

    @property
    def last_policy_decision(self) -> SelfImprovePolicyDecision:
        """Return redacted evidence for the most recent privacy decision.

        Repository paths and policy errors are deliberately excluded.  A caller
        may persist this mapping because it contains only a canonical policy
        digest, counters, and one-way path hashes.
        """
        return {
            "policy_digest": self._decision_digest,
            "allowed_count": self._decision_allowed_count,
            "blocked_count": self._decision_blocked_count,
            "path_hashes": tuple(sorted(self._decision_path_hashes)),
        }

    def _reset_policy_decision(self, digest: str | None) -> None:
        self._decision_digest = digest
        self._decision_allowed_count = 0
        self._decision_blocked_count = 0
        self._decision_path_hashes.clear()

    def _record_path_decision(self, path: str | None, *, allowed: bool) -> None:
        if allowed:
            self._decision_allowed_count += 1
            return
        self._decision_blocked_count += 1
        if path is not None:
            self._decision_path_hashes.add(
                hashlib.sha256(path.encode("utf-8")).hexdigest()
            )

    def _load_policy(self, *, reset: bool) -> SelfImprovePrivacyPolicy | None:
        try:
            policy = load_self_improve_policy(Path(self.repo_root))
        except Exception:
            if reset:
                self._reset_policy_decision(None)
            else:
                self._decision_digest = None
            self._record_path_decision(None, allowed=False)
            logger.warning("project privacy policy is invalid; self-improvement disabled")
            return None
        if reset:
            self._reset_policy_decision(policy.digest)
        return policy

    def _repository_path(self, value: object) -> str | None:
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if not isinstance(value, str) or not value:
            return None
        if os.path.isabs(value):
            root = os.path.abspath(self.repo_root)
            candidate = os.path.abspath(value)
            try:
                if os.path.commonpath((root, candidate)) != root:
                    return None
            except ValueError:
                return None
            value = os.path.relpath(candidate, root)
        return Path(value).as_posix()

    def _path_is_public(
        self,
        value: object,
        policy: SelfImprovePrivacyPolicy,
    ) -> bool:
        path = self._repository_path(value)
        if path is None:
            self._record_path_decision(None, allowed=False)
            return False
        try:
            allowed = policy.access_for(path) is PolicyAccess.PUBLIC
        except SelfImprovePolicyError:
            self._record_path_decision(None, allowed=False)
            return False
        self._record_path_decision(path, allowed=allowed)
        return allowed

    def _record_paths(self, record: Any) -> tuple[object, ...]:
        paths: list[object] = []
        missing = object()
        for field_name in self._PATH_FIELDS:
            value = _rec_get(record, field_name, missing)
            if value is not missing and value not in (None, ""):
                paths.append(value)
        for field_name in self._PATH_LIST_FIELDS:
            values = _rec_get(record, field_name, missing)
            if values is missing or values in (None, ""):
                continue
            if isinstance(values, (list, tuple, set, frozenset)):
                paths.extend(values)
            else:
                paths.append(values)
        return tuple(paths)

    def _records_are_public(
        self,
        records: list[Any] | None,
        policy: SelfImprovePrivacyPolicy,
    ) -> bool:
        if not records:
            return True
        try:
            return all(
                self._path_is_public(path, policy)
                for record in records
                for path in self._record_paths(record)
            )
        except Exception:
            self._record_path_decision(None, allowed=False)
            return False

    def _filter_public_records(
        self,
        records: list[dict[str, Any]],
        policy: SelfImprovePrivacyPolicy,
    ) -> list[dict[str, Any]]:
        public: list[dict[str, Any]] = []
        for record in records:
            paths = self._record_paths(record)
            if not paths or all(self._path_is_public(path, policy) for path in paths):
                public.append(record)
        return public

    def _policy_is_stable(self, original: SelfImprovePrivacyPolicy) -> bool:
        current = self._load_policy(reset=False)
        if current is None:
            return False
        if current.digest == original.digest:
            return True
        self._decision_digest = current.digest
        self._record_path_decision(None, allowed=False)
        logger.warning("project privacy policy changed; self-improvement disabled")
        return False

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
                work_type="gap_analysis",
            )
        else:
            # Adapter / test-fake path.
            response = gw.complete(prompt)
        return str(response.content)

class SelfImprovementHarness(_HarnessPolicySupport):
    """Discover project gaps and turn policy-approved findings into todos."""

    def run_gap_analysis(
        self, recurring_failures: list[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Analyse for self-improvement gaps.

        Sources:
          1. A model-driven codebase gap scan (when a gateway is wired), else a
             static scan (missing tests, low coverage).
          2. ``recurring_failures`` — records of shortcomings gludd hit while
             doing REAL work (e.g. ``BlockerDetector.chronic_blockers()``).
             These are ALWAYS folded in, regardless of the model/static branch,
             because they reflect what actually broke — not a static scan. The
             records are injected (dict or ``ChronicBlocker`` object) so the
             harness stays decoupled from the DB and unit-testable.
        """
        policy = self._load_policy(reset=True)
        if policy is None or not self._records_are_public(recurring_failures, policy):
            return []

        findings: list[dict[str, Any]] = []
        used_model = False
        if self._model_gateway is not None:
            try:
                import json

                public_source = self._read_all_src(policy)
                if self._decision_blocked_count and not public_source:
                    return []
                if not self._policy_is_stable(policy):
                    return []
                prompt = self._GAP_PROMPT
                if public_source:
                    prompt = (
                        f"{prompt}\n\nPolicy-approved project source follows. "
                        "Analyze only this snapshot:\n"
                        f"{public_source}"
                    )
                content = self._invoke_gateway(prompt)
                parsed = json.loads(_strip_json_fences(content))
                if isinstance(parsed, list):
                    # Return raw model findings unchanged (callers/tests rely on
                    # the model's shape); normalization happens in
                    # generate_fix_todos so both schemas produce valid todos.
                    # Filter to dict items only: a JSON list may contain non-object
                    # elements (str/int/None) that would crash the downstream
                    # generate_fix_todos/_normalize_finding .get() calls.
                    findings = self._filter_public_records(
                        [f for f in parsed if isinstance(f, dict)], policy
                    )
                    used_model = True
                else:
                    logger.warning(
                        "model gateway returned non-list JSON; falling back to static analysis"
                    )
            except Exception:
                logger.warning(
                    "model gateway gap-analysis failed; falling back to static analysis"
                )

        if not used_model:
            # Static fallback (or when model_gateway is None)
            self._check_missing_tests(findings, policy)
            self._check_completion_audit(findings)
            self._check_coverage_gaps(findings, policy)

        # Real-execution signal — folded in for BOTH branches.
        self._check_recurring_failures(findings, recurring_failures)

        return self._filter_public_records(findings, policy)

    def _check_recurring_failures(
        self, findings: list[dict[str, Any]], records: list[Any] | None
    ) -> None:
        """Turn recurring-failure records from real task execution into findings.

        Each record (a ``ChronicBlocker`` from ``BlockerDetector.chronic_blockers``
        or an equivalent dict) becomes a ``recurring_failure`` finding: gludd
        repeatedly failed this class of work, so it needs to improve itself.
        """
        if not records:
            return
        for rec in records:
            task_type = _rec_get(rec, "task_type", "") or ""
            blocker_kind = _rec_get(rec, "blocker_kind", "unknown") or "unknown"
            incident_count = _rec_get(rec, "incident_count", 0) or 0
            recent = _rec_get(rec, "recent_todo_ids", []) or []
            findings.append({
                "type": "recurring_failure",
                "severity": "high",
                "task_type": task_type,
                "blocker_kind": blocker_kind,
                "incident_count": incident_count,
                "recent_todo_ids": list(recent),
                "message": (
                    f"Recurring '{blocker_kind}' blocker in '{task_type}' work "
                    f"({incident_count} incidents) — gludd repeatedly failed this "
                    f"class of task; self-improvement needed."
                ),
            })

    def _normalize_finding(self, finding: dict[str, Any]) -> dict[str, Any]:
        """Normalize a model-shaped finding to the internal finding schema.

        The model gap prompt emits ``{title, description, priority, tier}`` while
        the static scanners and recurring-failure ingest emit
        ``{type, severity, message, file, ...}``. Without this, model findings
        degraded to a ``"Fix: unknown gap"`` title with an empty description.
        Internal-shaped findings pass through unchanged.
        """
        if "type" in finding or "message" in finding:
            return finding
        tier = str(finding.get("tier", "code")).lower()
        tier_to_type = {"test": "missing_tests", "config": "config_gap"}
        return {
            "type": tier_to_type.get(tier, "code_gap"),
            "severity": str(finding.get("priority", "medium")).lower(),
            "message": finding.get("description") or finding.get("title") or "",
            "file": finding.get("file", ""),
            "title": finding.get("title", ""),
            "tier": tier,
        }

    def _looks_like_python_project(self) -> bool:
        """True when ``repo_root`` looks like a Python/pytest project.

        The static scanners below (``_check_missing_tests`` / ``_check_coverage_gaps``)
        are pytest/gludd-specific: they assume a ``src/general_ludd`` package, a
        ``tests/`` tree and a Cobertura ``coverage.xml``. When the harness targets
        an EXTERNAL project's checkout (a JS/Go/Rust/etc. repo), those markers are
        absent, so running the scanners would emit false ``missing_tests`` /
        ``low_coverage`` findings against a codebase they cannot reason about.
        Gate them on a Python-project marker; the model-driven gap scan and the
        recurring-failure ingest stay project-neutral and always run.

        Detection requires a GENUINE Python marker: gludd's own ``src/general_ludd``
        package, or a ``pyproject.toml`` / ``setup.cfg`` / ``setup.py`` at the repo
        root. A bare Cobertura ``coverage.xml`` is deliberately NOT a Python signal
        — the Cobertura schema is language-agnostic (JS/Ruby/PHP tooling emits the
        same XML), so a non-Python target carrying a root ``coverage.xml`` must not
        be misclassified as python-shaped. ``_check_coverage_gaps`` still reads that
        ``coverage.xml`` once a real marker has confirmed the repo is Python.
        """
        if os.path.isdir(os.path.join(self.repo_root, "src", "general_ludd")):
            return True
        return any(
            os.path.isfile(os.path.join(self.repo_root, marker))
            for marker in ("pyproject.toml", "setup.cfg", "setup.py")
        )

    def _check_missing_tests(
        self,
        findings: list[dict[str, Any]],
        policy: SelfImprovePrivacyPolicy | None = None,
    ) -> None:
        if not self._looks_like_python_project():
            return
        if policy is None:
            policy = self._load_policy(reset=True)
            if policy is None:
                return
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
                    source_path = os.path.join(root, f)
                    if not self._path_is_public(source_path, policy):
                        continue
                    test_name = f"test_{f}"
                    if test_name not in test_files:
                        findings.append({
                            "type": "missing_tests",
                            "file": source_path,
                            "severity": "high",
                            "message": f"{os.path.relpath(os.path.join(root, f), self.repo_root)} has no test file",
                        })

    def _check_completion_audit(self, findings: list[dict[str, Any]]) -> None:
        # Dead-code heuristic disabled: too many false positives on a large
        # codebase where classes are referenced by string (Ansible, config,
        # dynamic dispatch). Re-enable with a proper AST-based caller graph.
        pass

    def _check_coverage_gaps(
        self,
        findings: list[dict[str, Any]],
        policy: SelfImprovePrivacyPolicy | None = None,
    ) -> None:
        if not self._looks_like_python_project():
            return
        if policy is None:
            policy = self._load_policy(reset=True)
            if policy is None:
                return
        coverage_xml = os.path.join(self.repo_root, "coverage.xml")
        if not os.path.isfile(coverage_xml):
            return
        if not self._path_is_public(coverage_xml, policy):
            return

        try:
            from general_ludd.security.secure_xml import parse_xml_file

            tree = parse_xml_file(coverage_xml, source="self-improve-coverage")
            for pkg in tree.findall(".//package"):
                for cls in pkg.findall("classes/class"):
                    filename = cls.get("filename", "")
                    rate = float(cls.get("line-rate", "1.0"))
                    if (
                        rate < 0.85
                        and filename
                        and self._path_is_public(filename, policy)
                    ):
                        findings.append({
                            "type": "low_coverage",
                            "file": filename,
                            "severity": "medium",
                            "coverage_pct": round(rate * 100, 1),
                            "message": f"{filename} at {round(rate * 100, 1)}% coverage (below 85%)",
                        })
        except Exception:
            pass

    def _read_all_src(
        self,
        policy: SelfImprovePrivacyPolicy | None = None,
    ) -> str:
        if policy is None:
            policy = self._load_policy(reset=True)
            if policy is None:
                return ""
        src_dir = os.path.join(self.repo_root, "src")
        source_bytes = bytearray()
        for root, dirs, files in os.walk(src_dir):
            safe_directories: list[str] = []
            for directory in sorted(dirs):
                directory_path = os.path.join(root, directory)
                if os.path.islink(directory_path):
                    relative = self._repository_path(directory_path)
                    self._record_path_decision(relative, allowed=False)
                else:
                    safe_directories.append(directory)
            dirs[:] = safe_directories
            for f in sorted(files):
                if f.endswith(".py"):
                    source_path = os.path.join(root, f)
                    if os.path.islink(source_path):
                        relative = self._repository_path(source_path)
                        self._record_path_decision(relative, allowed=False)
                        continue
                    if not self._path_is_public(source_path, policy):
                        continue
                    try:
                        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                        descriptor = os.open(source_path, flags)
                        try:
                            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                                raise OSError
                            with os.fdopen(
                                descriptor,
                                "r",
                                encoding="utf-8",
                                closefd=False,
                            ) as fh:
                                text = fh.read(self.MAX_GAP_SOURCE_BYTES + 1)
                        finally:
                            os.close(descriptor)
                    except (OSError, UnicodeDecodeError):
                        relative = self._repository_path(source_path)
                        self._record_path_decision(relative, allowed=False)
                        continue
                    relative = self._repository_path(source_path)
                    header = f"## {relative}\n".encode()
                    remaining = self.MAX_GAP_SOURCE_BYTES - len(source_bytes)
                    if remaining <= len(header):
                        return source_bytes.decode("utf-8", errors="ignore")
                    source_bytes.extend(header)
                    remaining = self.MAX_GAP_SOURCE_BYTES - len(source_bytes)
                    encoded = text.encode("utf-8")
                    source_bytes.extend(encoded[:remaining])
                    if len(encoded) >= remaining:
                        return source_bytes.decode("utf-8", errors="ignore")
                    source_bytes.extend(b"\n")
        return source_bytes.decode("utf-8", errors="strict")

    def generate_fix_todos(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert policy-approved gap findings into actionable todo mappings."""
        policy = self._load_policy(reset=True)
        if policy is None:
            return []
        todos: list[dict[str, Any]] = []
        for raw in self._filter_public_records(findings, policy):
            finding = self._normalize_finding(raw)
            ftype = finding.get("type", "")
            title = self._build_title(finding)
            description = finding.get("message", "")
            priority = finding.get("severity", "medium")
            work_type = "test" if ftype == "missing_tests" else "code"
            if ftype == "low_coverage":
                work_type = "test"
                priority = "medium"

            todo: dict[str, Any] = {
                "title": title,
                "description": description,
                "work_type": work_type,
                "priority": priority,
                "source": "self_improve_harness",
                "gap_type": ftype,
                "source_file": finding.get("file", ""),
            }
            if ftype == "recurring_failure":
                # Carry the real-failure context so the worker knows exactly
                # which class of task gludd keeps failing.
                todo["task_type"] = finding.get("task_type", "")
                todo["blocker_kind"] = finding.get("blocker_kind", "")
                todo["incident_count"] = finding.get("incident_count", 0)
                todo["recent_todo_ids"] = finding.get("recent_todo_ids", [])
            todos.append(todo)
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
        if ftype == "recurring_failure":
            tt = finding.get("task_type") or "unknown"
            bk = finding.get("blocker_kind") or "unknown"
            return f"Investigate recurring {bk} failures in {tt} tasks"
        return f"Fix: {finding.get('message', 'unknown gap')}"

    def enqueue_todos(self, todos: list[dict[str, Any]]) -> int:
        """Retain policy-approved todos and return the accepted count."""
        policy = self._load_policy(reset=True)
        if policy is None:
            return 0
        public_todos = self._filter_public_records(todos, policy)
        self._todos.extend(public_todos)
        return len(public_todos)

    def run_full_cycle(self, daemon_url: str = "http://localhost:8000") -> dict[str, Any]:
        """Run discovery, todo generation, and the in-memory enqueue boundary."""
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

    def apply_self_improvement(
        self,
        workspace_repo_dir: str,
        message: str,
        reloader: Any,
        health_check: Any = None,
        role: str | None = None,
        event_bus: Any = None,
    ) -> dict[str, Any]:
        """Commit a self-improvement change in the project workspace and hot-reload.

        Delegates to ``SelfApply.apply`` — kept for backward compatibility.
        """
        from general_ludd.self_improve.apply import SelfApply

        applier = SelfApply()
        return applier.apply(
            workspace_repo_dir=workspace_repo_dir,
            message=message,
            reloader=reloader,
            health_check=health_check,
            role=role,
            event_bus=event_bus,
        )

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
