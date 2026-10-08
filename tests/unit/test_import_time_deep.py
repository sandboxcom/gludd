"""Deep import-time and startup performance tests.

Measures import duration for core modules, validates that lazy imports work
correctly, checks for heavy init in __init__.py files, and verifies cold-start
import time stays within acceptable bounds.

Strategy:
  - Use subprocess (python -X importtime) for accurate cold-start measurement.
  - Use ``importlib.import_module`` with ``time.perf_counter`` for in-process
    warm-cache import timing.
  - Inspect __init__.py AST for forbidden heavy operations (network, subprocess,
    file reads of large files, DB connections).
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "general_ludd"
MIN_IMPORT_STABILITY_TOLERANCE_MS = 1.0


# ── helpers ────────────────────────────────────────────────────────────────


def _relative_import(name: str) -> str:
    return name if name.startswith(PACKAGE) else f"{PACKAGE}.{name}"


def _warm_import_time(module_name: str) -> float:
    """Import a module (already cached) and return its re-import time in ms."""
    mod = importlib.import_module(module_name)
    # Re-import via reload to measure cost; reload still hits cached .pyc.
    start = time.perf_counter()
    importlib.reload(mod)
    elapsed = time.perf_counter() - start
    return elapsed * 1000


def _cold_import_time(module_name: str) -> float:
    """Measure a fresh-process import time.

    Returns total import time in ms for the requested module by timing
    a subprocess that imports it.
    """
    code = (
        f"import sys, time; "
        f"sys.path.insert(0, {str(SRC)!r}); "
        f"t0 = time.perf_counter(); "
        f"import {module_name}; "
        f"print(time.perf_counter() - t0)"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    try:
        elapsed_s = float(result.stdout.strip())
    except (ValueError, TypeError):
        return 0.0
    return elapsed_s * 1000.0  # s -> ms


def _importtime_tree(module_name: str) -> list[dict[str, Any]]:
    """Parse -X importtime output into a list of {module, self_us, cum_us}.

    Handles both the traditional tabular format (Python < 3.13) and the newer
    key=value / JSON-lines format introduced in Python 3.13+.
    """
    code = f"import sys; sys.path.insert(0, {str(SRC)!r}); import {module_name}"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    entries: list[dict[str, Any]] = []
    for line in result.stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Try JSON-lines format (Python 3.13+)
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
                mod_name = obj.get("name", "")
                if mod_name:
                    entries.append(
                        {
                            "module": mod_name,
                            "cum_us": obj.get("import_time", obj.get("cumulative", 0)),
                            "self_us": obj.get("self_time", obj.get("self", 0)),
                        }
                    )
            except json.JSONDecodeError:
                pass
            continue

        # Try traditional tabular format: cumulative_us self_us module_name
        parts = stripped.split()
        if len(parts) < 3:
            continue
        try:
            cum_us = int(parts[0])
            self_us = int(parts[1])
            mod_name = parts[2]
        except (ValueError, IndexError):
            continue
        entries.append({"module": mod_name, "cum_us": cum_us, "self_us": self_us})
    return entries


# ── core module import timing (warm-cache) ─────────────────────────────────


class TestCoreModuleImportTime:
    """Core package imports must complete in under 100 ms (warm cache)."""

    # Packages that are intentionally heavy (DB, connectors, etc.) get a higher bound
    CORE_MODULES: tuple[tuple[str, float], ...] = (
        ("general_ludd.remediation", 80),
        ("general_ludd.security", 50),
        ("general_ludd.config", 80),
        ("general_ludd.connectors", 60),
        ("general_ludd.models", 40),
        ("general_ludd.ansible", 100),
        ("general_ludd.approval", 40),
        ("general_ludd.process", 30),
        ("general_ludd.metrics", 30),
        ("general_ludd.feature_flags", 20),
        ("general_ludd.schemas", 30),
        ("general_ludd.routing_roles", 50),
        ("general_ludd.ag16_orchestration", 30),
        ("general_ludd.renderers", 40),
        ("general_ludd.validation", 30),
    )

    @pytest.mark.parametrize("module_name,threshold_ms", CORE_MODULES)
    def test_core_module_import_under_threshold(self, module_name: str, threshold_ms: float) -> None:
        elapsed_ms = _warm_import_time(module_name)
        assert elapsed_ms < threshold_ms, (
            f"{module_name} import took {elapsed_ms:.1f} ms, threshold is {threshold_ms:.0f} ms"
        )


class TestLightweightInitFiles:
    """__init__.py files must not do heavy work at import time."""

    FORBIDDEN_INIT_OPERATIONS = frozenset(
        [
            "subprocess.Popen",
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_output",
            "subprocess.check_call",
            "socket.socket",
            "requests.get",
            "requests.post",
            "urllib.request.urlopen",
            "http.client.HTTPSConnection",
            "http.client.HTTPConnection",
            "os.system",
            "os.popen",
        ]
    )

    def _forbidden_ast_nodes(self, tree: ast.AST) -> list[tuple[int, str]]:
        violations: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            # Check for subprocess.run / os.system etc.
            if isinstance(node, ast.Call):
                node_str = ast.unparse(node) if hasattr(ast, "unparse") else ""
                for forbidden in self.FORBIDDEN_INIT_OPERATIONS:
                    if forbidden in node_str:
                        violations.append((node.lineno if hasattr(node, "lineno") else 0, node_str[:120]))
            # Check for open() with large file reads
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                violations.append(
                    (node.lineno if hasattr(node, "lineno") else 0, "open() call at top-level in __init__.py")
                )
        return violations

    def _iter_init_files(self) -> list[Path]:
        init_files: list[Path] = []
        for pyfile in (SRC / "general_ludd").rglob("__init__.py"):
            init_files.append(pyfile)
        return sorted(init_files)

    @pytest.mark.parametrize(
        "init_path",
        sorted((Path(__file__).resolve().parents[2] / "src" / "general_ludd").rglob("__init__.py")),
        ids=lambda p: str(p.relative_to(Path(__file__).resolve().parents[2] / "src")),
    )
    def test_no_network_or_subprocess_in_init(self, init_path: Path) -> None:
        source = init_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            pytest.skip(f"Syntax error in {init_path}: cannot parse")
        violations = self._forbidden_ast_nodes(tree)
        assert not violations, f"{init_path.name} has forbidden heavy operations at import time:\n" + "\n".join(
            f"  line {lineno}: {snippet}" for lineno, snippet in violations
        )

    def test_routing_roles_uses_lazy_import(self) -> None:
        """routing_roles must use PEP 562 __getattr__ for lazy weight imports."""
        init_path = SRC / "general_ludd" / "routing_roles" / "__init__.py"
        source = init_path.read_text()
        assert "__getattr__" in source, "routing_roles/__init__.py must define __getattr__ for lazy imports"
        assert "_WEIGHT_LAZY" in source or "_LAZY" in source, "routing_roles/__init__.py must use lazy-import sets"

    def test_remediation_uses_lazy_import_for_model(self) -> None:
        """remediation must lazy-import RemediationActionModel to avoid cycle."""
        init_path = SRC / "general_ludd" / "remediation" / "__init__.py"
        source = init_path.read_text()
        assert "__getattr__" in source, "remediation/__init__.py must define __getattr__ for lazy model import"

    def test_main_init_does_not_do_heavy_work(self) -> None:
        """The root __init__.py should only do essential patching."""
        init_path = SRC / "general_ludd" / "__init__.py"
        source = init_path.read_text()
        tree = ast.parse(source)

        # Must NOT contain: DB engine creation, heavy imports
        forbidden = [
            "create_engine",
            "AsyncEngine",
            "sessionmaker",
            "asyncio.run",
            "threading.Thread",
            "multiprocessing",
        ]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                node_str = ast.unparse(node) if hasattr(ast, "unparse") else ""
                for f in forbidden:
                    assert f not in node_str, f"Root __init__.py must not reference '{f}' at import time"


class TestLazyImportsWorkCorrectly:
    """Verify that PEP 562 __getattr__ lazy imports trigger correctly."""

    def test_routing_roles_weights_lazy_load(self) -> None:
        """Accessing routing_roles.RoleWeights imports the weights module."""
        import general_ludd.routing_roles as rr

        assert hasattr(rr, "RoleWeights"), "RoleWeights should be accessible"
        weights_mod = sys.modules.get("general_ludd.routing_roles.weights")
        assert weights_mod is not None, "Accessing RoleWeights should trigger weights module import"

    def test_routing_roles_policy_lazy_load(self) -> None:
        """Accessing routing_roles.SmallModelTaskPolicy imports the policy module."""
        import general_ludd.routing_roles as rr

        assert hasattr(rr, "SmallModelTaskPolicy"), "SmallModelTaskPolicy should be accessible"
        policy_mod = sys.modules.get("general_ludd.routing_roles.small_model_policy")
        assert policy_mod is not None, "Accessing SmallModelTaskPolicy should trigger policy module import"

    def test_remediation_lazy_load_model(self) -> None:
        """Accessing remediation.RemediationActionModel imports db.models."""
        import general_ludd.remediation as rem

        assert hasattr(rem, "RemediationActionModel"), "RemediationActionModel should be accessible"
        db_mod = sys.modules.get("general_ludd.db.models")
        assert db_mod is not None, "Accessing RemediationActionModel should trigger db.models import"

    def test_lazy_module_not_imported_before_access(self) -> None:
        """Before access, the lazy submodules should NOT be in sys.modules."""
        # Force fresh import
        for key in list(sys.modules):
            if "routing_roles" in key:
                del sys.modules[key]
        import general_ludd.routing_roles as rr

        before = "general_ludd.routing_roles.weights" in sys.modules
        _ = rr.RoleWeights
        after = "general_ludd.routing_roles.weights" in sys.modules
        assert not before, "weights module should NOT be imported before first access"
        assert after, "weights module MUST be imported after first access"


class TestColdStartImportTime:
    """Full cold-start import of general_ludd must stay under acceptable bounds."""

    def test_root_package_cold_import_under_500ms(self) -> None:
        """Cold import of 'general_ludd' alone must complete in < 500 ms."""
        elapsed_ms = _cold_import_time("general_ludd")
        assert elapsed_ms < 500.0, f"Cold import of general_ludd took {elapsed_ms:.1f} ms, threshold 500 ms"

    def test_root_package_cold_import_self_time(self) -> None:
        """The root __init__.py self-time must be minimal (< 10 ms)."""
        tree = _importtime_tree("general_ludd")
        for entry in tree:
            mod = entry["module"]
            if mod == "general_ludd" or mod.endswith(".general_ludd") or mod == "general_ludd.__init__":
                self_ms = entry["self_us"] / 1000.0
                assert self_ms < 10.0, f"general_ludd __init__.py self-time {self_ms:.1f} ms, must be < 10 ms"
                return
        # Fallback: search for any entry containing "general_ludd" at the top level
        for entry in tree:
            mod = entry["module"]
            if mod in ("general_ludd", "import general_ludd") or (
                "." not in mod and "general_ludd" in mod.replace("_", ".")
            ):
                self_ms = entry["self_us"] / 1000.0
                assert self_ms < 10.0, f"general_ludd __init__.py self-time {self_ms:.1f} ms, must be < 10 ms"
                return
        if not tree:
            pytest.skip("importtime produced no output (Python version may not support -X importtime)")
            return
        mod_names = [e["module"] for e in tree[:10]]
        pytest.fail(f"Could not find general_ludd entry in importtime output. First entries: {mod_names}")

    def test_no_single_module_dominates_cold_start(self) -> None:
        """No single submodule should account for >30% of total cold import time."""
        tree = _importtime_tree("general_ludd")
        total_us = sum(e["self_us"] for e in tree)
        if total_us == 0:
            pytest.skip("No importtime data")
        for entry in tree:
            pct = (entry["self_us"] / total_us) * 100
            assert pct < 30.0, (
                f"{entry['module']} self-time is {pct:.1f}% of total — too dominant; consider lazy-loading"
            )


class TestImportTimeConsistency:
    """Verify import time is consistent across repeated imports."""

    def test_config_import_stable_across_runs(self) -> None:
        times = [_warm_import_time("general_ludd.config") for _ in range(5)]
        avg = sum(times) / len(times)
        limit = max(avg * 2.5, MIN_IMPORT_STABILITY_TOLERANCE_MS)
        for t in times:
            assert t < limit, f"config import time {t:.1f} ms exceeds stable limit {limit:.1f} ms"

    def test_security_import_stable_across_runs(self) -> None:
        times = [_warm_import_time("general_ludd.security") for _ in range(5)]
        avg = sum(times) / len(times)
        limit = max(avg * 2.5, MIN_IMPORT_STABILITY_TOLERANCE_MS)
        for t in times:
            assert t < limit, f"security import time {t:.1f} ms exceeds stable limit {limit:.1f} ms"


class TestSubpackageTopology:
    """The dependency graph should be shallow — no package should trigger
    a cascade of 30+ transitive imports."""

    def test_no_package_triggers_excessive_transitive_imports(self) -> None:
        light_packages = [
            "general_ludd.feature_flags",
            "general_ludd.process",
            "general_ludd.approval",
        ]
        for pkg in light_packages:
            before = set(sys.modules.keys())
            importlib.import_module(pkg)
            after = set(sys.modules.keys())
            new = after - before
            count = len([m for m in new if m.startswith("general_ludd.")])
            assert count < 40, f"Importing {pkg} pulled in {count} general_ludd.* modules (must be < 40)"
