"""ScenarioGenerator — maps code-path symbols to realistic E2E test scenarios.

For each public function/class in a module, matches against a catalog of common
E2E patterns (CRUD lifecycle, auth flow, timeout handling, concurrent edits,
daemon restart).  Produces ``GeneratedScenario`` records with step sequences.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .code_path_analyzer import ModuleSymbols, Symbol
from .knowledge.test_scenarios import E2E_SCENARIOS, E2EScenario

logger = logging.getLogger(__name__)


PATTERN_KEYWORDS: dict[str, list[str]] = {
    "crud_lifecycle": ["create", "update", "delete", "remove", "add", "insert"],
    "auth_flow": ["auth", "login", "token", "logout", "session", "credentials"],
    "timeout_handling": ["timeout", "retry", "backoff", "deadline", "circuit"],
    "concurrent_edits": ["lock", "mutex", "atomic", "concurrent", "race", "transaction"],
    "daemon_restart": ["init", "startup", "shutdown", "restart", "reload", "bootstrap"],
}


@dataclass
class ScenarioStep:
    action: str
    target: str
    expected_result: str
    assertions: list[str] = field(default_factory=list)


@dataclass
class GeneratedScenario:
    name: str
    description: str
    steps: list[ScenarioStep]
    coverage_targets: list[str] = field(default_factory=list)


class ScenarioGenerator:

    def __init__(self, scenario_catalog: list[E2EScenario] | None = None) -> None:
        self._catalog: list[E2EScenario] = scenario_catalog if scenario_catalog is not None else E2E_SCENARIOS

    def generate(self, module_symbols: ModuleSymbols) -> list[GeneratedScenario]:
        scenario_map = self._map_symbols_to_scenarios(module_symbols)
        results: list[GeneratedScenario] = []
        for scenario, symbols in scenario_map:
            steps = self._generate_steps(scenario, symbols)
            results.append(GeneratedScenario(
                name=scenario.name,
                description=scenario.description,
                steps=steps,
                coverage_targets=[s.name for s in symbols],
            ))
        return results

    def _map_symbols_to_scenarios(
        self, module_symbols: ModuleSymbols
    ) -> list[tuple[E2EScenario, list[Symbol]]]:
        public_symbols: list[Symbol] = [
            f for f in module_symbols.functions if f.is_public
        ]
        for cls in module_symbols.classes:
            if cls.is_public:
                public_symbols.append(Symbol(
                    name=cls.name, line_start=cls.line_start,
                    line_end=cls.line_end, is_public=True,
                ))

        scenario_index: dict[str, E2EScenario] = {s.name: s for s in self._catalog}

        matched: dict[str, list[Symbol]] = {}
        for sym in public_symbols:
            lower = sym.name.lower()
            for tag, keywords in PATTERN_KEYWORDS.items():
                if any(kw in lower for kw in keywords) and tag in scenario_index:
                    matched.setdefault(tag, []).append(sym)

        return [(scenario_index[tag], syms) for tag, syms in matched.items()]

    def _generate_steps(
        self, scenario: E2EScenario, symbols: list[Symbol]
    ) -> list[ScenarioStep]:
        handler = _STEP_HANDLERS.get(scenario.name, _default_steps)
        return handler(symbols)


def _crud_steps(symbols: list[Symbol]) -> list[ScenarioStep]:
    targets = [s.name for s in symbols]
    target = targets[0] if targets else "resource"
    return [
        ScenarioStep(
            action="Setup",
            target="fixtures",
            expected_result="test data created",
            assertions=["fixture loaded"],
        ),
        ScenarioStep(
            action="POST",
            target=f"/api/{target}",
            expected_result="201 Created",
            assertions=["response.status_code == 201", "response body contains id"],
        ),
        ScenarioStep(
            action="GET",
            target=f"/api/{target}/<id>",
            expected_result="200 OK with resource",
            assertions=["response.status_code == 200", "response matches created data"],
        ),
        ScenarioStep(
            action="PUT",
            target=f"/api/{target}/<id>",
            expected_result="200 OK with updated resource",
            assertions=["response.status_code == 200", "response body reflects update"],
        ),
        ScenarioStep(
            action="DELETE",
            target=f"/api/{target}/<id>",
            expected_result="204 No Content",
            assertions=["response.status_code == 204"],
        ),
        ScenarioStep(
            action="GET",
            target=f"/api/{target}/<id>",
            expected_result="404 Not Found",
            assertions=["response.status_code == 404"],
        ),
        ScenarioStep(
            action="Teardown",
            target="cleanup",
            expected_result="test data removed",
            assertions=["no residual state"],
        ),
    ]


def _auth_steps(symbols: list[Symbol]) -> list[ScenarioStep]:
    return [
        ScenarioStep(
            action="Setup",
            target="fixtures",
            expected_result="test user created",
            assertions=["user fixture loaded"],
        ),
        ScenarioStep(
            action="POST",
            target="/api/auth/login",
            expected_result="200 OK with token",
            assertions=["response.status_code == 200", "access_token in response body", "token_type == bearer"],
        ),
        ScenarioStep(
            action="GET",
            target="/api/protected/resource",
            expected_result="200 OK with protected data",
            assertions=["response.status_code == 200", "Authorization header accepted"],
        ),
        ScenarioStep(
            action="GET",
            target="/api/protected/resource",
            expected_result="401 Unauthorized (no token)",
            assertions=["response.status_code == 401", "detail indicates missing auth"],
        ),
        ScenarioStep(
            action="POST",
            target="/api/auth/refresh",
            expected_result="200 OK with new token",
            assertions=["response.status_code == 200", "new access_token returned"],
        ),
        ScenarioStep(
            action="Teardown",
            target="cleanup",
            expected_result="test user removed",
            assertions=["no residual auth state"],
        ),
    ]


def _timeout_steps(symbols: list[Symbol]) -> list[ScenarioStep]:
    targets = [s.name for s in symbols]
    target = targets[0] if targets else "endpoint"
    return [
        ScenarioStep(
            action="Setup",
            target="fixtures",
            expected_result="slow endpoint configured",
            assertions=["slow endpoint fixture active"],
        ),
        ScenarioStep(
            action="GET",
            target=f"/api/{target}/slow",
            expected_result="Request times out after configured deadline",
            assertions=["httpx.TimeoutException raised", "client timeout < server processing time"],
        ),
        ScenarioStep(
            action="GET",
            target=f"/api/{target}/retry",
            expected_result="Retries on transient failure, eventually succeeds",
            assertions=["first attempt fails", "subsequent attempt succeeds", "response.status_code == 200"],
        ),
        ScenarioStep(
            action="Teardown",
            target="cleanup",
            expected_result="slow endpoint removed",
            assertions=["no residual slow connections"],
        ),
    ]


def _concurrent_steps(symbols: list[Symbol]) -> list[ScenarioStep]:
    return [
        ScenarioStep(
            action="Setup",
            target="fixtures",
            expected_result="shared resource created",
            assertions=["resource fixture loaded"],
        ),
        ScenarioStep(
            action="Simulate",
            target="concurrent access",
            expected_result="Multiple clients access same resource",
            assertions=[">=2 concurrent clients started", "each client acquires lock before mutating"],
        ),
        ScenarioStep(
            action="Assert",
            target="state after concurrent writes",
            expected_result="No data corruption, no lost writes",
            assertions=["resource state is consistent", "no race condition artifacts", "all writes visible"],
        ),
        ScenarioStep(
            action="Teardown",
            target="cleanup",
            expected_result="shared resource removed",
            assertions=["no residual concurrent state"],
        ),
    ]


def _daemon_steps(symbols: list[Symbol]) -> list[ScenarioStep]:
    return [
        ScenarioStep(
            action="Setup",
            target="fixtures",
            expected_result="resources created before restart",
            assertions=["resources persisted to disk"],
        ),
        ScenarioStep(
            action="Stop",
            target="daemon process",
            expected_result="Daemon shut down gracefully",
            assertions=["daemon process exited", "no orphaned child processes"],
        ),
        ScenarioStep(
            action="Start",
            target="daemon process",
            expected_result="Daemon restarts and recovers state",
            assertions=["daemon process running", "healthcheck passes", "pre-restart resources accessible"],
        ),
        ScenarioStep(
            action="Verify",
            target="recovered state",
            expected_result="All pre-restart resources intact",
            assertions=["resource count matches pre-restart", "resource data unchanged"],
        ),
        ScenarioStep(
            action="Teardown",
            target="cleanup",
            expected_result="resources cleaned up",
            assertions=["no residual daemon state"],
        ),
    ]


def _default_steps(symbols: list[Symbol]) -> list[ScenarioStep]:
    targets = [s.name for s in symbols]
    target = targets[0] if targets else "module"
    return [
        ScenarioStep(
            action="Setup",
            target="fixtures",
            expected_result="test fixtures ready",
            assertions=["fixture data loaded"],
        ),
        ScenarioStep(
            action="Invoke",
            target=target,
            expected_result="function returns expected output",
            assertions=["output matches expected"],
        ),
        ScenarioStep(
            action="Teardown",
            target="cleanup",
            expected_result="test state cleaned",
            assertions=["no residual state"],
        ),
    ]




_STEP_HANDLERS: dict[str, Callable[[list[Symbol]], list[ScenarioStep]]] = {
    "crud_lifecycle": _crud_steps,
    "auth_flow": _auth_steps,
    "timeout_handling": _timeout_steps,
    "concurrent_edits": _concurrent_steps,
    "daemon_restart": _daemon_steps,
}
