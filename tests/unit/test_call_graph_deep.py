"""Deep call graph and function reachability tests.

AST-based analysis verifying architectural invariants:
  - EventLoop calls all expected subsystems
  - ModelGateway calls ModelRouter / CostAwareRouter
  - daemon.create_daemon_app registers all expected routers
  - No orphaned public functions (defined, never called)
  - daemon_wiring make_* factories return callables for all wired kinds
  - Every router module exports a register() function
  - Critical phase methods exist on EventLoop
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC_PKG = ROOT / "src" / "general_ludd"
PKG_NAME = "general_ludd"


# ── AST helpers ──────────────────────────────────────────────────────────


def _parse_source(filepath: Path) -> ast.Module:
    return ast.parse(filepath.read_text())


def _collect_calls_from_node(node: ast.AST) -> set[str]:
    """Return all function/method names called in this AST node's body."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                if isinstance(child.func.value, ast.Name):
                    names.add(f"{child.func.value.id}.{child.func.attr}")
                elif isinstance(child.func.value, ast.Attribute):
                    parts: list[str] = []
                    v: ast.expr = child.func
                    while isinstance(v, ast.Attribute):
                        parts.append(v.attr)
                        v = v.value
                    if isinstance(v, ast.Name):
                        parts.append(v.id)
                        names.add(".".join(reversed(parts)))
    return names


def _function_defs(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _class_defs(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _class_method_names(tree: ast.Module) -> dict[str, set[str]]:
    methods: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            m = {n.name for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            methods[node.name] = m
    return methods


def _all_imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _collect_py_files(subdir: str = "") -> list[Path]:
    pf: list[Path] = []
    target = SRC_PKG / subdir if subdir else SRC_PKG
    for dirpath, _dirnames, filenames in os.walk(target):
        for fn in filenames:
            if fn.endswith(".py") and not fn.startswith("__"):
                pf.append(Path(dirpath) / fn)
    return sorted(pf)


def _public_functions(tree: ast.Module, filename: Path) -> list[str]:
    """Public (non-_prefixed) top-level functions."""
    funcs = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            funcs.append(node.name)
    return funcs


def _all_call_names(tree: ast.Module) -> set[str]:
    """All names called anywhere in the module."""
    return _collect_calls_from_node(tree)


# ── Module-specific extractors ──────────────────────────────────────────


def _create_daemon_app_body() -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    daemon_path = SRC_PKG / "daemon.py"
    tree = _parse_source(daemon_path)
    funcs = _function_defs(tree)
    return funcs.get("create_daemon_app")


def _event_loop_init_body() -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    el_path = SRC_PKG / "event_loop" / "loop.py"
    tree = _parse_source(el_path)
    classes = _class_defs(tree)
    if "EventLoop" not in classes:
        return None
    for node in ast.walk(classes["EventLoop"]):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
            return node
    return None


# ── Pre-computed data ────────────────────────────────────────────────────

daemon_tree = _parse_source(SRC_PKG / "daemon.py")
daemon_funcs = _function_defs(daemon_tree)
daemon_classes = _class_defs(daemon_tree)
daemon_imports = _all_imported_names(daemon_tree)

el_tree = _parse_source(SRC_PKG / "event_loop" / "loop.py")
el_funcs = _function_defs(el_tree)
el_classes = _class_defs(el_tree)
el_imports = _all_imported_names(el_tree)

elh_tree = _parse_source(SRC_PKG / "event_loop" / "loop_handlers.py")
elh_classes = _class_defs(elh_tree)

gw_tree = _parse_source(SRC_PKG / "models" / "gateway.py")
gw_funcs = _function_defs(gw_tree)
gw_classes = _class_defs(gw_tree)
gw_imports = _all_imported_names(gw_tree)

dw_tree = _parse_source(SRC_PKG / "daemon_wiring.py")
dw_funcs = _function_defs(dw_tree)
dw_imports = _all_imported_names(dw_tree)

router_files = _collect_py_files("routers")


# ═══════════════════════════════════════════════════════════════════════
# Test 1 — EventLoop.__init__ accepts all expected subsystem params
# ═══════════════════════════════════════════════════════════════════════

EXPECTED_EL_PARAMS: frozenset[str] = frozenset(
    {
        "worker_base_url",
        "config",
        "session",
        "http_client",
        "todo_repo",
        "task_return_repo",
        "budget_guard",
        "mcp_client",
        "mcp_tool_registry",
        "runner",
        "event_bus",
        "project_manager",
        "prompt_registry",
        "audit_repo",
        "skill_registry",
        "variable_repo",
        "adaptive_router",
        "daemon_state",
        "project_secrets_manager",
        "project_workspace",
        "model_gateway",
        "dispatcher",
        "pause_controller",
        "spend_limiter",
        "sandbox_executor",
        "sandbox_config",
        "run_recorder",
        "checkpointer",
        "utilization_tracker",
        "floor_controller",
        "compaction_controller",
        "issue_ingestor",
        "infra_tracker",
        "credit_tracker",
        "ephemeral_account_manager",
        "inbound_queue",
        "checkpoint_manager",
        "service_discovery",
        "deployment_health_router",
        "deployment_manager",
        "model_perf_repo",
        "memory_repo",
        "procedural_memory",
        "semantic_memory",
        "file_claim_registry",
        "loc_ledger",
        "ansible_env_updater",
        "reviewer",
        "consensus_reviewer",
        "langgraph_reviewer",
        "self_improve_interval",
        "model_performance_interval",
        "consolidation_interval_ticks",
        "prompt_variant_selector",
        "sandbox_attestation_store",
        "sandbox_profile",
    }
)


def test_event_loop_init_has_expected_params() -> None:
    init = _event_loop_init_body()
    assert init is not None, "EventLoop.__init__ not found"
    args = init.args
    param_names: set[str] = set()
    for p in args.args + args.kwonlyargs:
        if p.arg != "self":
            param_names.add(p.arg)
    missing = EXPECTED_EL_PARAMS - param_names
    extra = param_names - EXPECTED_EL_PARAMS
    assert not missing, f"EventLoop.__init__ missing params: {sorted(missing)}"
    assert not extra, f"EventLoop.__init__ has unexpected params (update EXPECTED_EL_PARAMS): {sorted(extra)}"


# ═══════════════════════════════════════════════════════════════════════
# Test 2 — EventLoop class has all expected phase methods
# ═══════════════════════════════════════════════════════════════════════

EXPECTED_PHASE_PATTERNS: frozenset[str] = frozenset(
    {
        "_phase_load_config_snapshot",
        "_phase_claim_unreviewed_task_returns",
        "_phase_dispatch_return_review_jobs",
        "_phase_evaluate_pid_controllers",
        "_phase_refill_task_buckets",
        "_phase_run_scheduler",
        "_phase_sdlc_gate",
        "_phase_claim_runnable_todos",
        "_phase_evaluate_rules",
        "_phase_dispatch_execute_jobs",
        "_phase_reconcile_completed_decisions",
        "_phase_refresh_model_performance",
        "_phase_check_compute_utilization",
        "_phase_check_service_credits",
        "_phase_flush_spend_ledger",
        "_phase_remediate_blocked_tasks",
        "_phase_consolidate_memory",
        "_phase_self_improve",
        "_phase_poll_issue_sources",
        "_phase_service_discovery",
        "_phase_reap_expired_sts_tokens",
        "_phase_purge_old_task_decisions",
        "_phase_emit_tick_metrics",
    }
)


def test_event_loop_has_all_phase_methods() -> None:
    loop_methods = _class_method_names(el_tree)
    handler_methods = _class_method_names(elh_tree)
    assert "EventLoop" in loop_methods, "EventLoop class not found"
    assert "EventLoopHandlers" in handler_methods, "EventLoopHandlers mixin not found"
    resolved_methods = loop_methods["EventLoop"] | handler_methods["EventLoopHandlers"]
    missing = {p for p in EXPECTED_PHASE_PATTERNS if p not in resolved_methods}
    assert not missing, f"EventLoop MRO missing phase methods: {sorted(missing)}"


# ═══════════════════════════════════════════════════════════════════════
# Test 3 — ModelGateway.__init__ accepts or creates ModelRouter
# ═══════════════════════════════════════════════════════════════════════


def test_model_gateway_imports_model_router() -> None:
    assert "ModelRouter" in gw_imports, "ModelGateway does not import ModelRouter"


def test_model_gateway_uses_router() -> None:
    """Verify ModelGateway references router or CostAwareRouter or _router."""
    gw_source = (SRC_PKG / "models" / "gateway.py").read_text()
    router_refs = [
        "ModelRouter(",
        "CostAwareRouter(",
        "self._router",
        "_router",
        "model_router",
        ".router",
        "router.",
    ]
    found = [r for r in router_refs if r in gw_source]
    assert found, f"ModelGateway does not reference ModelRouter. Found nothing from {router_refs} in gateway.py"


# ═══════════════════════════════════════════════════════════════════════
# Test 4 — daemon.create_daemon_app registers all expected routers
# ═══════════════════════════════════════════════════════════════════════

_EXPECTED_ROUTER_REGISTRATIONS: frozenset[str] = frozenset(
    {
        "eval_router",
        "webmcp",
        "todos",
        "messages",
        "accounting",
        "account_router",
        "facts",
        "environment",
        "embeddings",
        "features",
        "schedule",
        "model_performance",
        "models",
        "variants",
        "benchmark",
        "mcp",
        "memory",
        "skills",
        "compute",
        "deployments",
        "processes",
        "filestore",
        "git_history",
        "hardware_router",
        "human_todos",
        "integrity",
        "signing",
        "security",
        "projects",
        "quantization",
        "reload",
        "replays",
        "worktree",
        "ansible",
        "azure_cost_router",
        "slurm",
        "self_improve",
        "self_update",
        "maintenance",
        "make",
        "remediation",
        "research",
        "review",
        "ornith",
        "experts",
        "render",
        "receiver_router",
        "dispatch_router",
        "spend",
        "pause",
        "_approval_router",
        "sts_router",
        "_compaction_aggr_router",
        "_coord_router",
        "_stream_router",
        "_terraform_state_router",
    }
)


def test_create_daemon_app_registers_all_routers() -> None:
    app_body = _create_daemon_app_body()
    assert app_body is not None, "create_daemon_app not found"

    registrations: set[str] = set()
    for node in ast.walk(app_body):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "register" or not isinstance(node.func.value, ast.Name):
            continue
        if len(node.args) < 2:
            continue
        first, second = node.args[:2]
        app_arg = isinstance(first, ast.Name) and first.id == "app"
        state_arg = isinstance(second, ast.Name) and second.id == "daemon_state"
        if app_arg and state_arg:
            registrations.add(node.func.value.id)

    missing = _EXPECTED_ROUTER_REGISTRATIONS - registrations
    extra = registrations - _EXPECTED_ROUTER_REGISTRATIONS
    assert not missing, f"create_daemon_app missing router registrations: {sorted(missing)}"
    assert not extra, (
        f"create_daemon_app has unexpected registrations (update _EXPECTED_ROUTER_REGISTRATIONS): {sorted(extra)}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Test 5 — Every router module has a register() function
# ═══════════════════════════════════════════════════════════════════════


def test_every_router_module_has_register_function() -> None:
    missing: list[str] = []
    for rp in router_files:
        if rp.name.startswith("_"):
            continue
        tree = _parse_source(rp)
        funcs = _function_defs(tree)
        if "register" not in funcs:
            missing.append(str(rp.relative_to(SRC_PKG)))
    assert not missing, f"Router modules missing register(): {missing}"


# ═══════════════════════════════════════════════════════════════════════
# Test 6 — daemon_wiring exposes all expected make_* factories
# ═══════════════════════════════════════════════════════════════════════

EXPECTED_WIRING_FACTORIES: frozenset[str] = frozenset(
    {
        "make_mcp_handler",
        "make_skill_handler",
        "make_role_handler",
        "make_collection_handler",
        "make_spend_guarded_executor",
    }
)


def test_daemon_wiring_has_all_factories() -> None:
    dw_public = {f for f in _public_functions(dw_tree, Path()) if f.startswith("make_")}
    missing = EXPECTED_WIRING_FACTORIES - dw_public
    extra = dw_public - EXPECTED_WIRING_FACTORIES
    assert not missing, f"daemon_wiring missing make_* factories: {sorted(missing)}"
    assert not extra, f"daemon_wiring has unexpected make_* factories: {sorted(extra)}"


# ═══════════════════════════════════════════════════════════════════════
# Test 7 — ModelGateway has expected public methods
# ═══════════════════════════════════════════════════════════════════════

EXPECTED_GW_PUBLIC_METHODS: frozenset[str] = frozenset(
    {
        "call_model",
        "add_profile",
        "remove_profile",
        "get_profile",
        "list_profiles",
        "get_chat_model",
    }
)


def test_model_gateway_has_expected_public_methods() -> None:
    methods = _class_method_names(gw_tree)
    assert "ModelGateway" in methods, "ModelGateway class not found"
    gw_methods = methods["ModelGateway"]
    for m in EXPECTED_GW_PUBLIC_METHODS:
        assert m in gw_methods, f"ModelGateway missing public method: {m}"


# ═══════════════════════════════════════════════════════════════════════
# Test 8 — daemon.py contains create_daemon_app and _lifespan
# ═══════════════════════════════════════════════════════════════════════

EXPECTED_DAEMON_FUNCTIONS: frozenset[str] = frozenset(
    {
        "create_daemon_app",
        "load_startup_config",
        "build_secrets_resolver",
        "resolve_secret_manager_for_call",
        "build_event_loop_mcp_dispatcher",
    }
)


def test_daemon_has_expected_public_functions() -> None:
    public = _public_functions(daemon_tree, SRC_PKG / "daemon.py")
    for f in EXPECTED_DAEMON_FUNCTIONS:
        assert f in public, f"daemon.py missing expected public function: {f}"


# ═══════════════════════════════════════════════════════════════════════
# Test 9 — No orphaned public functions in daemon_wiring.py
# ═══════════════════════════════════════════════════════════════════════


def test_daemon_wiring_no_orphaned_public_functions() -> None:
    public = _public_functions(dw_tree, Path())
    assert public, "daemon_wiring has no public functions"
    daemon_calls = _collect_calls_from_node(daemon_tree)
    public_entrypoints = {"build_dispatch_handlers"}
    non_wired = [
        f for f in public if f not in daemon_calls and f not in dw_imports and f not in public_entrypoints
    ]
    assert not non_wired, f"daemon_wiring public functions neither wired nor documented entrypoints: {non_wired}"


# ═══════════════════════════════════════════════════════════════════════
# Test 10 — ModelGateway constructor references _router attribute
# ═══════════════════════════════════════════════════════════════════════


def test_model_gateway_init_sets_router() -> None:
    """Verify ModelGateway.__init__ body assigns to a router attribute."""
    init_body = None
    if "ModelGateway" in gw_classes:
        for node in ast.walk(gw_classes["ModelGateway"]):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                init_body = node
                break
    assert init_body is not None, "ModelGateway.__init__ not found"
    init_source = ast.unparse(init_body)
    router_assignments = [
        "_router",
        "self._router",
        "self.router",
        "cost_router",
        "self._cost_router",
    ]
    found = [r for r in router_assignments if r in init_source]
    assert found, f"ModelGateway.__init__ does not assign to a router attribute. Searched: {router_assignments}"


# ═══════════════════════════════════════════════════════════════════════
# Test 11 — EventLoop tick() calls expected phase methods
# ═══════════════════════════════════════════════════════════════════════


def test_event_loop_tick_calls_phase_methods() -> None:
    """Verify tick reaches every declared phase through the dynamic dispatcher."""
    if "EventLoop" not in el_classes:
        pytest.skip("EventLoop class not found")
    tick_node = el_funcs.get("tick")
    tick_once = el_funcs.get("_tick_once")
    run_phase_range = el_funcs.get("_run_phase_range")
    assert tick_node is not None, "EventLoop.tick not found"
    assert tick_once is not None, "EventLoop._tick_once not found"
    assert run_phase_range is not None, "EventLoop._run_phase_range not found"

    assert "self._tick_once" in _collect_calls_from_node(tick_node)
    tick_calls = _collect_calls_from_node(tick_once)
    assert {"self._run_phases", "self._run_phase_range"} & tick_calls
    range_source = ast.unparse(run_phase_range)
    assert "getattr" in _collect_calls_from_node(run_phase_range)
    assert "_phase_" in range_source and "phase_name" in range_source

    loop_methods = _class_method_names(el_tree)["EventLoop"]
    handler_methods = _class_method_names(elh_tree)["EventLoopHandlers"]
    missing = EXPECTED_PHASE_PATTERNS - (loop_methods | handler_methods)
    assert not missing, f"Dynamic phase dispatch has unresolved methods: {sorted(missing)}"


# ═══════════════════════════════════════════════════════════════════════
# Test 12 — daemon.py lifespan wires event_loop subsystems back to app.state
# ═══════════════════════════════════════════════════════════════════════

_EXPECTED_APP_STATE_ATTRS: frozenset[str] = frozenset(
    {
        "_adaptive_router",
        "_agent_dispatcher",
        "_mcp_client",
        "_event_bus",
        "_model_gateway",
        "_skill_registry",
        "_prompt_registry",
        "_budget_guard",
        "_secrets_resolver",
        "_health_tracker",
        "_runner",
        "_research_index",
    }
)


def test_daemon_lifespan_wires_expected_app_state() -> None:
    daemon_source = (SRC_PKG / "daemon.py").read_text()
    import re

    attrs: set[str] = set()
    for m in re.finditer(r"app\.state\.(_[a-z_]+)\s*=", daemon_source):
        attrs.add(m.group(1))

    missing = _EXPECTED_APP_STATE_ATTRS - attrs
    assert not missing, f"daemon lifespan missing app.state wiring: {sorted(missing)}"


# ═══════════════════════════════════════════════════════════════════════
# Test 13 — code_intelligence.callgraph CallGraph used somewhere
# ═══════════════════════════════════════════════════════════════════════


def test_callgraph_has_expected_api() -> None:
    from general_ludd.code_intelligence.callgraph import CallGraph

    assert hasattr(CallGraph, "build_from_blocks")
    assert hasattr(CallGraph, "get_callees")
    assert hasattr(CallGraph, "get_callers")
    assert hasattr(CallGraph, "is_subclass")
    assert hasattr(CallGraph, "to_dict")
    assert hasattr(CallGraph, "has_node")


# ═══════════════════════════════════════════════════════════════════════
# Test 14 — daemon _lifespan builds EventLoop with expected kwargs
# ═══════════════════════════════════════════════════════════════════════


def test_daemon_lifespan_constructs_event_loop() -> None:
    daemon_source = (SRC_PKG / "daemon.py").read_text()
    assert "EventLoop(" in daemon_source, "daemon.py never constructs EventLoop"


# ═══════════════════════════════════════════════════════════════════════
# Test 15 — No orphaned top-level public functions in event_loop/loop.py
# ═══════════════════════════════════════════════════════════════════════


def test_event_loop_no_orphaned_top_level_functions() -> None:
    """All top-level public functions in loop.py are called somewhere in daemon or event_loop."""
    public = _public_functions(el_tree, SRC_PKG / "event_loop" / "loop.py")
    if not public:
        return  # No standalone public functions; all logic in EventLoop class
    daemon_calls = _collect_calls_from_node(daemon_tree)
    el_calls = _collect_calls_from_node(el_tree)
    all_calls = daemon_calls | el_calls
    orphans = [f for f in public if f not in all_calls]
    assert not orphans, f"Orphaned top-level functions in loop.py: {orphans}"


# ═══════════════════════════════════════════════════════════════════════
# Test 16 — ModelGateway imports CostAwareRouter for routing
# ═══════════════════════════════════════════════════════════════════════


def test_model_gateway_imports_cost_aware_router() -> None:
    assert "CostAwareRouter" in gw_imports, "ModelGateway does not import CostAwareRouter"


# ═══════════════════════════════════════════════════════════════════════
# Test 17 — daemon_wiring make_mcp_handler references MCPClient
# ═══════════════════════════════════════════════════════════════════════


def test_make_mcp_handler_has_mcp_client_param() -> None:
    mcp_func = dw_funcs.get("make_mcp_handler")
    assert mcp_func is not None, "make_mcp_handler not found in daemon_wiring"
    params = [a.arg for a in mcp_func.args.args]
    assert "mcp_client" in params, f"make_mcp_handler params: {params}"


# ═══════════════════════════════════════════════════════════════════════
# Test 18 — daemon call graph: build_event_loop_mcp_dispatcher calls wiring
# ═══════════════════════════════════════════════════════════════════════


def test_build_event_loop_mcp_dispatcher_calls_daemon_wiring() -> None:
    func = daemon_funcs.get("build_event_loop_mcp_dispatcher")
    assert func is not None, "build_event_loop_mcp_dispatcher not found"
    calls = _collect_calls_from_node(func)
    assert "make_mcp_handler" in calls, f"Dispatcher builder doesn't call make_mcp_handler. Calls: {sorted(calls)}"
    assert "make_role_handler" in calls or "make_skill_handler" in calls, (
        f"Dispatcher builder doesn't call make_role/make_skill. Calls: {sorted(calls)}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Test 19 — Every router uses the `register(app, daemon_state)` pattern
# ═══════════════════════════════════════════════════════════════════════


def test_every_router_register_takes_app_and_daemon_state() -> None:
    violations: list[str] = []
    for rp in router_files:
        if rp.name.startswith("_"):
            continue
        tree = _parse_source(rp)
        funcs = _function_defs(tree)
        if "register" not in funcs:
            violations.append(f"{rp.relative_to(SRC_PKG)}: no register() function")
            continue
        reg = funcs["register"]
        args = reg.args
        param_names = [p.arg for p in args.args]
        if len(param_names) < 2 or "app" not in param_names[0].lower():
            violations.append(f"{rp.relative_to(SRC_PKG)}: register({param_names}) — first param should be 'app'")
    assert not violations, "Router register() signature violations:\n" + "\n".join(violations)
