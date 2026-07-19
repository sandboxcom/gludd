"""Catalog of common E2E test patterns used by the ScenarioGenerator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class E2EScenario:
    name: str
    description: str
    steps: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


E2E_SCENARIOS: list[E2EScenario] = [
    E2EScenario(
        name="crud_lifecycle",
        description="Create, read, update, delete a resource through the API",
        steps=["POST create", "GET read", "PUT update", "DELETE remove", "GET not_found"],
        tags=["api", "lifecycle", "rest"],
    ),
    E2EScenario(
        name="auth_flow",
        description="Authenticate, access protected endpoints, token expiry",
        steps=["POST login", "GET protected", "wait expiry", "GET protected_401"],
        tags=["auth", "security", "tokens"],
    ),
    E2EScenario(
        name="timeout_handling",
        description="Request timeout, connection drop, graceful degradation",
        steps=["slow endpoint", "client timeout", "retry", "eventual success"],
        tags=["resilience", "network", "timeout"],
    ),
    E2EScenario(
        name="concurrent_edits",
        description="Multiple clients editing the same resource concurrently",
        steps=["client_a GET", "client_b GET", "client_a PUT", "client_b PUT conflict"],
        tags=["concurrency", "isolation", "conflict"],
    ),
    E2EScenario(
        name="daemon_restart",
        description="Daemon restart with state recovery",
        steps=["create resources", "kill daemon", "restart", "verify state intact"],
        tags=["recovery", "durability", "daemon"],
    ),
]
