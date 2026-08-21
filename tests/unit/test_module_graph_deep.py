"""Deep module dependency graph tests for src/general_ludd/.

Verifies architectural invariants:
  - No circular import cycles (excludes __init__.py re-exports)
  - Core/infra modules have no UI (tui/routers/daemon) dependencies
  - Utility modules have no business logic dependencies
  - Connectors stay within their allowed dependency surface
"""

from __future__ import annotations

import ast
import os
import sys
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC_PKG = ROOT / "src" / "general_ludd"
PKG_NAME = "general_ludd"

# Dependencies every environment must provide for the in-isolation import
# check; a ModuleNotFoundError for anything outside this set is an optional
# third-party package (rapidfuzz, scipy, pycryptodome, srptools, shamir,
# pywt, pyspx, argon2, ...) and the module is skipped, not failed.
_REQUIRED_STDLIB_AND_CORE_DEPS = frozenset(
    {
        "general_ludd",
        "ansible",
        "fastapi",
        "gunicorn",
        "httpx",
        "jinja2",
        "langchain",
        "langgraph",
        "langsmith",
        "numpy",
        "pydantic",
        "sqlalchemy",
        "starlette",
        "uvicorn",
        "yaml",
        "tenacity",
        "watchdog",
        "llama_cpp",
        "huggingface_hub",
    }
)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _collect_py_files() -> list[Path]:
    pf: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(SRC_PKG):
        dp = Path(dirpath)
        for fn in filenames:
            if fn.endswith(".py"):
                pf.append(dp / fn)
    return sorted(pf)


def _path_to_module(path: Path) -> str:
    rel = path.relative_to(SRC_PKG.parent)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _subpackage_of(module: str, parent: str) -> bool:
    return module == parent or module.startswith(parent + ".")


def _top_package(module: str) -> str:
    parts = module.split(".")
    if len(parts) <= 2:
        return module
    return ".".join(parts[:2])


_FILE_PATHS = _collect_py_files()
_MODULE_NAMES = {_path_to_module(p) for p in _FILE_PATHS}


def _static_imports(path: Path) -> set[str]:
    imports: set[str] = set()
    src = path.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _subpackage_of(alias.name, PKG_NAME):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            if _subpackage_of(node.module, PKG_NAME):
                imports.add(node.module)
    return imports


def _full_import_graph(*, exclude_init: bool = True) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for p in _FILE_PATHS:
        mod = _path_to_module(p)
        if exclude_init and p.name == "__init__.py":
            continue
        imports = _static_imports(p)
        imports.discard(mod)
        graph[mod] = imports
    return graph


_GRAPH = _full_import_graph(exclude_init=True)


# ═══════════════════════════════════════════════════════════════════
# Layer definitions
# ═══════════════════════════════════════════════════════════════════

CORE_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.__init__",
        "general_ludd.compat",
        "general_ludd.config",
        "general_ludd.db",
        "general_ludd.logging",
        "general_ludd.metrics",
        "general_ludd.networking",
        "general_ludd.process",
        "general_ludd.schemas",
        "general_ludd.system",
        "general_ludd.validation",
    }
)

SECURITY_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.secrets",
        "general_ludd.security",
        "general_ludd.auth",
        "general_ludd.permissions",
        "general_ludd.sts",
    }
)

UTILITY_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.language",
        "general_ludd.chemistry",
        "general_ludd.physics",
        "general_ludd.materials",
        "general_ludd.travel",
        "general_ludd.culinary",
        "general_ludd.electronics",
        "general_ludd.hardware",
        "general_ludd.benchmark",
        "general_ludd.quantization",
        "general_ludd.html",
        "general_ludd.templates",
        "general_ludd.xml_utils",
        "general_ludd.output_templates",
        "general_ludd.web_utils",
        "general_ludd.algorithms",
        "general_ludd.bitarray",
        "general_ludd.bloom_filter",
        "general_ludd.compression",
        "general_ludd.diff_engine",
        "general_ludd.distributed",
        "general_ludd.encoding_converter",
        "general_ludd.experiments",
        "general_ludd.fsm",
        "general_ludd.hash_table",
        "general_ludd.health",
        "general_ludd.load_balancer",
        "general_ludd.local_model",
        "general_ludd.messaging",
        "general_ludd.network",
        "general_ludd.probabilistic",
        "general_ludd.regex_engine",
        "general_ludd.resilience",
        "general_ludd.ring_buffer",
        "general_ludd.sagas",
        "general_ludd.skip_list",
        "general_ludd.storage",
        "general_ludd.supervision",
        "general_ludd.util",
        "general_ludd.web",
    }
)

QUALITY_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.quality",
    }
)

CONNECTOR_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.connectors",
    }
)

BUSINESS_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.agents",
        "general_ludd.ansible",
        "general_ludd.approval",
        "general_ludd.budget",
        "general_ludd.business",
        "general_ludd.code_intelligence",
        "general_ludd.collections",
        "general_ludd.commands",
        "general_ludd.compaction",
        "general_ludd.coordination",
        "general_ludd.controllers",
        "general_ludd.dependency",
        "general_ludd.dispatch",
        "general_ludd.dogfood",
        "general_ludd.entity",
        "general_ludd.eval",
        "general_ludd.event_loop",
        "general_ludd.events",
        "general_ludd.execution",
        "general_ludd.feature_flags",
        "general_ludd.filestore",
        "general_ludd.git_automation",
        "general_ludd.git_release",
        "general_ludd.governance",
        "general_ludd.history",
        "general_ludd.infra",
        "general_ludd.integration",
        "general_ludd.integrity",
        "general_ludd.ipc",
        "general_ludd.issue_sources",
        "general_ludd.langchain",
        "general_ludd.mcp",
        "general_ludd.memory",
        "general_ludd.models",
        "general_ludd.notifications",
        "general_ludd.observability",
        "general_ludd.observe",
        "general_ludd.onboard",
        "general_ludd.orchestration",
        "general_ludd.ornith",
        "general_ludd.pipeline",
        "general_ludd.planning",
        "general_ludd.pricing_intel",
        "general_ludd.project_runner",
        "general_ludd.projects",
        "general_ludd.prompts",
        "general_ludd.receiver",
        "general_ludd.reload",
        "general_ludd.remediation",
        "general_ludd.renderers",
        "general_ludd.replay",
        "general_ludd.retrieval",
        "general_ludd.review",
        "general_ludd.routing_roles",
        "general_ludd.rules",
        "general_ludd.runner",
        "general_ludd.runtime",
        "general_ludd.sandbox",
        "general_ludd.sandbox_exec",
        "general_ludd.scheduling",
        "general_ludd.scoring",
        "general_ludd.searx",
        "general_ludd.self_improve",
        "general_ludd.self_update",
        "general_ludd.service_discovery",
        "general_ludd.skills",
        "general_ludd.small_models",
        "general_ludd.ssl",
        "general_ludd.ssl_agent",
        "general_ludd.stream",
        "general_ludd.worker",
        "general_ludd.worktree",
        "general_ludd.writer",
    }
)

PRESENTATION_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.tui",
        "general_ludd.routers",
        "general_ludd.daemon",
        "general_ludd.daemon_wiring",
        "general_ludd.chat",
        "general_ludd.web_server_utils",
        "general_ludd.cli",
        "general_ludd.cli_account",
        "general_ludd.cli_audit_plugins",
        "general_ludd.cli_collection",
        "general_ludd.cli_core_changes",
        "general_ludd.cli_deploy_check",
        "general_ludd.cli_human_todos",
        "general_ludd.cli_model",
        "general_ludd.cli_ornith",
        "general_ludd.cli_payment",
        "general_ludd.cli_perm",
        "general_ludd.cli_physics",
        "general_ludd.cli_parser_cache",
        "general_ludd.cli_project_init",
        "general_ludd.cli_project_paths",
        "general_ludd.cli_remediation",
        "general_ludd.cli_self_improve",
        "general_ludd.cli_service_commands",
        "general_ludd.cli_spec_quality",
    }
)

OTHER_PACKAGES: frozenset[str] = frozenset(
    {
        "general_ludd.abtest",
        "general_ludd.account",
        "general_ludd.accounting",
        "general_ludd.ag13_dspy",
        "general_ludd.ag14_reflexion",
        "general_ludd.ag15_benchmarks",
        "general_ludd.ag16_orchestration",
        "general_ludd.ag2_lifecycle",
        "general_ludd.ag8_named_passes",
        "general_ludd.ag9_checkpoint",
        "general_ludd.ai_ml",
        "general_ludd.azure",
        "general_ludd.cloud",
        "general_ludd.log_analysis",
        "general_ludd.log_analyzer",
        "general_ludd.model_weights",
        "general_ludd.smoke",
        "general_ludd.budget_guard_check",
        "general_ludd.hardware_memory_policy",
        "general_ludd.peak_pricing",
        "general_ludd.game_gen",
    }
)

_EXPECTED_PACKAGES = (
    CORE_PACKAGES
    | SECURITY_PACKAGES
    | UTILITY_PACKAGES
    | QUALITY_PACKAGES
    | CONNECTOR_PACKAGES
    | BUSINESS_PACKAGES
    | PRESENTATION_PACKAGES
    | OTHER_PACKAGES
)


def _layer_of(module: str) -> int:
    for layer, pkgs in enumerate(
        [
            CORE_PACKAGES,
            SECURITY_PACKAGES,
            UTILITY_PACKAGES,
            QUALITY_PACKAGES,
            CONNECTOR_PACKAGES,
            BUSINESS_PACKAGES,
            PRESENTATION_PACKAGES,
        ]
    ):
        if any(_subpackage_of(module, p) for p in pkgs):
            return layer
    if any(_subpackage_of(module, p) for p in OTHER_PACKAGES):
        return -1
    return -1


# Known cross-layer imports that are by design.
_LAYER_VIOLATION_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("general_ludd.daemon", "general_ludd.routers"),
        ("general_ludd.daemon", "general_ludd.web_server_utils"),
        ("general_ludd.event_loop.loop", "general_ludd.routers.dispatch"),
        ("general_ludd.execution.tool_loop", "general_ludd.routers.dispatch"),
        ("general_ludd.worker.app", "general_ludd.daemon_wiring"),
        ("general_ludd.worker.app", "general_ludd.routers.dispatch"),
        ("general_ludd.config.deployment_optimization", "general_ludd.infra.deployment_optimizer"),
        ("general_ludd.config.model_routing", "general_ludd.models.router"),
        ("general_ludd.db.azure_cost_repository", "general_ludd.infra.azure_cost_reconciliation"),
        ("general_ludd.quality.preflight", "general_ludd.filestore.store"),
        ("general_ludd.quality.preflight", "general_ludd.collections.importer"),
        ("general_ludd.quality.project_gate", "general_ludd.project_runner"),
        ("general_ludd.validation.runner", "general_ludd.worktree.core"),
        ("general_ludd.security.sandboxes.vm.contracts", "general_ludd.sandbox.contracts"),
        ("general_ludd.sts.injector", "general_ludd.agents.dispatcher"),
        ("general_ludd.sts.injector", "general_ludd.agents.types"),
        ("general_ludd.benchmark.langgraph_bench", "general_ludd.review.langgraph_reviewer"),
        ("general_ludd.benchmark.langgraph_bench", "general_ludd.review.langgraph_consensus"),
        ("general_ludd.benchmark.langgraph_bench", "general_ludd.review.consensus"),
        ("general_ludd.benchmark.langgraph_bench", "general_ludd.review.reviewer"),
        ("general_ludd.benchmark.langgraph_bench", "general_ludd.execution.tool_loop"),
        ("general_ludd.benchmark.langgraph_bench", "general_ludd.execution.langgraph_agent"),
        ("general_ludd.small_models.benchmark_report", "general_ludd.routing_roles.small_model_policy"),
        ("general_ludd.small_models.eval_harness", "general_ludd.routing_roles.small_model_policy"),
        ("general_ludd.small_models.evidence_store", "general_ludd.routing_roles.small_model_policy"),
        ("general_ludd.small_models.lm_eval_runner", "general_ludd.routing_roles.small_model_policy"),
        ("general_ludd.small_models.radar_profile", "general_ludd.routing_roles.small_model_policy"),
        ("general_ludd.small_models.zdd_rollout", "general_ludd.routing_roles.small_model_policy"),
        ("general_ludd.small_models.cost", "general_ludd.infra.pricing"),
        ("general_ludd.hardware.model_fit", "general_ludd.pricing_intel"),
        ("general_ludd.hardware.model_fit", "general_ludd.small_models"),
        ("general_ludd.validation.backlog_auditor", "general_ludd.security.sanitize"),
        ("general_ludd.security.security_backlog", "general_ludd.ansible.runner"),
        ("general_ludd.security.security_backlog", "general_ludd.ansible.core_runner"),
        ("general_ludd.security.security_backlog", "general_ludd.ansible.templating"),
        ("general_ludd.security.security_backlog", "general_ludd.ansible.network_policy"),
        ("general_ludd.security.security_backlog", "general_ludd.ansible.unsafe"),
        ("general_ludd.security.security_backlog", "general_ludd.project_runner.runner"),
        ("general_ludd.security.security_backlog", "general_ludd.git_automation.worktree_lease"),
        ("general_ludd.security.security_backlog", "general_ludd.git_automation.repo"),
        ("general_ludd.security.security_backlog", "general_ludd.models.gateway"),
        ("general_ludd.security.security_backlog", "general_ludd.ornith.mcp_server"),
        ("general_ludd.security.security_backlog", "general_ludd.mcp.transport"),
        ("general_ludd.security.security_backlog", "general_ludd.receiver.router"),
        ("general_ludd.security.security_backlog", "general_ludd.projects.manager"),
        ("general_ludd.security.security_backlog", "general_ludd.event_loop.loop"),
        ("general_ludd.security.security_backlog", "general_ludd.execution.langgraph_agent"),
    }
)

# Known circular import pairs — __init__.py re-exports are excluded by the
# graph builder. These are real cycles in the codebase, documented here.
_KNOWN_CIRCULAR_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"general_ludd.mcp.transport", "general_ludd.mcp.secrets"}),
        frozenset({"general_ludd.project_runner.profile", "general_ludd.project_runner.detect"}),
    }
)


def _is_allowlisted_layer_violation(src: str, dep: str) -> bool:
    if (src, dep) in _LAYER_VIOLATION_ALLOWLIST:
        return True
    dep_parts = dep.split(".")
    return any((src, ".".join(dep_parts[:i])) in _LAYER_VIOLATION_ALLOWLIST for i in range(1, len(dep_parts)))


# ═══════════════════════════════════════════════════════════════════
# Test 1 — All subpackages are classified
# ═══════════════════════════════════════════════════════════════════


def test_all_subpackages_classified() -> None:
    top_packages: set[str] = set()
    for mod in _MODULE_NAMES:
        if mod == PKG_NAME:
            continue
        top_packages.add(_top_package(mod))
    unclassified = top_packages - _EXPECTED_PACKAGES
    assert not unclassified, (
        f"Unclassified packages: {sorted(unclassified)}. Add them to the appropriate layer set in this test file."
    )


# ═══════════════════════════════════════════════════════════════════
# Test 2 — No circular imports (excludes __init__.py re-exports)
# ═══════════════════════════════════════════════════════════════════


def test_no_circular_imports_in_graph() -> None:
    graph = _GRAPH
    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in graph:
                continue
            if neighbor not in visited:
                dfs(neighbor, path)
            elif neighbor in rec_stack:
                cycle_start = path.index(neighbor)
                cycles.append([*path[cycle_start:], neighbor])
        path.pop()
        rec_stack.discard(node)

    for node in sorted(graph):
        if node not in visited:
            dfs(node, [])

    new_cycles = [c for c in cycles if frozenset(c) not in _KNOWN_CIRCULAR_PAIRS]
    assert not new_cycles, (
        f"Found {len(new_cycles)} NEW circular import cycle(s):\n"
        + "\n".join(" -> ".join(c) for c in new_cycles)
        + (
            f"\n{len(cycles) - len(new_cycles)} known cycle(s) filtered by allowlist."
            if len(cycles) > len(new_cycles)
            else ""
        )
    )


# ═══════════════════════════════════════════════════════════════════
# Test 3 — Every module importable in isolation
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("mod_name", sorted(_MODULE_NAMES), ids=str)
def test_each_module_importable_in_isolation(mod_name: str) -> None:
    saved = {k: v for k, v in sys.modules.items() if _subpackage_of(k, PKG_NAME)}
    for k in saved:
        if saved[k] is not None:
            del sys.modules[k]
    try:
        import importlib

        importlib.invalidate_caches()
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            if missing and missing.split(".")[0] not in _REQUIRED_STDLIB_AND_CORE_DEPS:
                pytest.skip(
                    f"{mod_name} requires optional dependency {missing!r} that is not installed in this environment"
                )
            raise
        assert mod is not None
    finally:
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v


# ═══════════════════════════════════════════════════════════════════
# Test 4 — No upward layer imports
# ═══════════════════════════════════════════════════════════════════


def test_no_upward_layer_imports() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        mod_layer = _layer_of(mod)
        if mod_layer < 0:
            continue
        for dep in deps:
            dep_layer = _layer_of(dep)
            if dep_layer < 0:
                continue
            if dep_layer <= mod_layer:
                continue
            if _is_allowlisted_layer_violation(mod, dep):
                continue
            violations.append(f"{mod} (L{mod_layer}) imports {dep} (L{dep_layer})")
    assert not violations, (
        f"Found {len(violations)} upward-import violation(s):\n"
        + "\n".join(violations[:30])
        + (f"\n...and {len(violations) - 30} more" if len(violations) > 30 else "")
    )


# ═══════════════════════════════════════════════════════════════════
# Test 5 — Core has no UI dependencies
# ═══════════════════════════════════════════════════════════════════


def test_core_has_no_ui_dependencies() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not any(_subpackage_of(mod, p) for p in CORE_PACKAGES):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in PRESENTATION_PACKAGES):
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} core->UI import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 6 — Utility has no business dependencies
# ═══════════════════════════════════════════════════════════════════


def test_utility_has_no_business_dependencies() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not any(_subpackage_of(mod, p) for p in UTILITY_PACKAGES):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in BUSINESS_PACKAGES):
                if _is_allowlisted_layer_violation(mod, dep):
                    continue
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} utility->business import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 7 — Security has no UI dependencies
# ═══════════════════════════════════════════════════════════════════


def test_security_has_no_ui_dependencies() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not any(_subpackage_of(mod, p) for p in SECURITY_PACKAGES):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in PRESENTATION_PACKAGES):
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} security->UI import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 8 — Core has no business dependencies
# ═══════════════════════════════════════════════════════════════════


def test_core_has_no_business_dependencies() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not any(_subpackage_of(mod, p) for p in CORE_PACKAGES):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in BUSINESS_PACKAGES):
                if _is_allowlisted_layer_violation(mod, dep):
                    continue
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} core->business import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 9 — Core has no connector dependencies
# ═══════════════════════════════════════════════════════════════════


def test_core_has_no_connector_dependencies() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not any(_subpackage_of(mod, p) for p in CORE_PACKAGES):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in CONNECTOR_PACKAGES):
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} core->connector import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 10 — Utility has no UI dependencies
# ═══════════════════════════════════════════════════════════════════


def test_utility_has_no_ui_dependencies() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not any(_subpackage_of(mod, p) for p in UTILITY_PACKAGES):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in PRESENTATION_PACKAGES):
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} utility->UI import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 11 — DB has no presentation imports
# ═══════════════════════════════════════════════════════════════════


def test_db_has_no_presentation_imports() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not _subpackage_of(mod, "general_ludd.db"):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in PRESENTATION_PACKAGES):
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} db->presentation import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 12 — Models have no UI imports
# ═══════════════════════════════════════════════════════════════════


def test_models_has_no_ui_imports() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not _subpackage_of(mod, "general_ludd.models"):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in PRESENTATION_PACKAGES):
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} models->presentation import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 13 — Connectors have no UI imports
# ═══════════════════════════════════════════════════════════════════


def test_connectors_have_no_ui_imports() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if not any(_subpackage_of(mod, p) for p in CONNECTOR_PACKAGES):
            continue
        for dep in deps:
            if any(_subpackage_of(dep, p) for p in PRESENTATION_PACKAGES):
                violations.append(f"{mod} imports {dep}")
    assert not violations, f"Found {len(violations)} connector->presentation import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 14 — Core is imported by many modules (fan-in > 0)
# ═══════════════════════════════════════════════════════════════════


def test_core_is_depended_upon() -> None:
    fan_in: dict[str, int] = {}
    for _mod, deps in _GRAPH.items():
        for dep in deps:
            top = _top_package(dep)
            fan_in[top] = fan_in.get(top, 0) + 1

    core_fan_in = sum(fan_in.get(pkg, 0) for pkg in CORE_PACKAGES)
    assert core_fan_in > 0, "No modules import from core packages"


# ═══════════════════════════════════════════════════════════════════
# Test 15 — CLI sub-modules don't cross-import each other
# ═══════════════════════════════════════════════════════════════════


def test_cli_submodules_no_cross_imports() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        if mod == "general_ludd.cli":
            continue
        if not mod.startswith("general_ludd.cli"):
            continue
        for dep in deps:
            if dep.startswith("general_ludd.cli") and dep != mod:
                violations.append(f"{mod} imports sibling {dep}")
    assert not violations, f"Found {len(violations)} CLI-cross-import(s):\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# Test 16 — Core transitive closure has no UI
# ═══════════════════════════════════════════════════════════════════


def _transitive_closure(graph: dict[str, set[str]], start: str) -> set[str]:
    closure: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in graph.get(node, set()):
            if neighbor not in closure:
                closure.add(neighbor)
                queue.append(neighbor)
    return closure


def test_core_transitive_closure_no_ui() -> None:
    for core_pkg in sorted(CORE_PACKAGES):
        if core_pkg == "general_ludd.__init__":
            continue
        closure = _transitive_closure(_GRAPH, core_pkg)
        ui_reached = {dep for dep in closure if any(_subpackage_of(dep, p) for p in PRESENTATION_PACKAGES)}
        assert not ui_reached, f"{core_pkg} transitively reaches UI: {sorted(ui_reached)}"


# ═══════════════════════════════════════════════════════════════════
# Test 17 — Utility transitive closure has no business
# ═══════════════════════════════════════════════════════════════════


def test_utility_transitive_closure_no_business() -> None:
    for util_pkg in sorted(UTILITY_PACKAGES):
        closure = _transitive_closure(_GRAPH, util_pkg)
        biz_reached = {
            dep
            for dep in closure
            if any(_subpackage_of(dep, p) for p in BUSINESS_PACKAGES)
            and not _is_allowlisted_layer_violation(util_pkg, dep)
        }
        assert not biz_reached, f"{util_pkg} transitively reaches business: {sorted(biz_reached)}"


# ═══════════════════════════════════════════════════════════════════
# Test 18 — All imports reference known modules
# ═══════════════════════════════════════════════════════════════════


def test_all_imports_reference_known_modules() -> None:
    violations: list[str] = []
    for mod, deps in sorted(_GRAPH.items()):
        for dep in deps:
            if dep not in _MODULE_NAMES:
                violations.append(f"{mod} imports unknown {dep}")
    assert not violations, (
        f"Found {len(violations)} unknown-import(s):\n"
        + "\n".join(violations[:30])
        + (f"\n...and {len(violations) - 30} more" if len(violations) > 30 else "")
    )
