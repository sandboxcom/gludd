"""Seed data for the feature database.

Each entry describes a product feature requested this engagement, with real
evidence references so a later ``FeatureVerifier.verify_all()`` run reflects
reality rather than self-assertion.

Status legend (at seed time):
  implemented — feature landed and exercised in code/molecule
  requested   — design-only or not yet started

Evidence-string grammar mirrors make audit-evidence:
  test:<pytest-node-id>   role:<name>   module:<name>
  molecule:<scenario>     file:<path>::<symbol>
"""
from __future__ import annotations

from typing import Any

FEATURE_SEED: list[dict[str, Any]] = [
    {
        "name": "facts_api_mq",
        "category": "api",
        "description": (
            "gludd_facts Ansible module exposes /api/facts as dynamic variables "
            "so playbooks can branch on live harness state via the MQ."
        ),
        "acceptance_criteria": [
            "gludd_facts module exists under plugins/modules/",
            "/api/facts endpoint returns work, todos, messages, metrics, traces facets",
            "molecule scenario exercises the module",
        ],
        "evidence": [
            "module:gludd_facts",
            "molecule:test_gludd_facts",
            "file:src/general_ludd/routers/facts.py::def api_facts",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "message_queue",
        "category": "core",
        "description": (
            "Inter-agent message queue: AgentMessageModel + AgentMessageRepository "
            "+ /api/messages send/inbox/ack endpoints + gludd_message Ansible module."
        ),
        "acceptance_criteria": [
            "AgentMessageModel persisted in DB",
            "gludd_message module exists",
            "send/inbox/ack round-trip tested",
        ],
        "evidence": [
            "module:gludd_message",
            "molecule:test_gludd_message",
            "test:tests/unit/test_agent_message_repo.py",
            "file:src/general_ludd/db/models.py::AgentMessageModel",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "metrics_traces_facts",
        "category": "observability",
        "description": (
            "gludd_metrics and gludd_traces Ansible modules expose /api/metrics "
            "and /api/traces as Ansible dynamic facts."
        ),
        "acceptance_criteria": [
            "gludd_metrics module exists",
            "gludd_traces module exists",
            "molecule scenarios for both",
        ],
        "evidence": [
            "module:gludd_metrics",
            "module:gludd_traces",
            "molecule:test_gludd_metrics",
            "molecule:test_gludd_traces",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "molecule_coverage",
        "category": "ci",
        "description": (
            "Molecule test coverage for all gludd_* modules and core roles, "
            "enforced by a ratcheted floor (MIN_MOLECULE_SCENARIOS)."
        ),
        "acceptance_criteria": [
            "MIN_MOLECULE_SCENARIOS >= 49 in preflight.py",
            "molecule scenario count >= floor",
        ],
        "evidence": [
            "test:tests/integration/test_molecule_coverage.py",
            "file:src/general_ludd/quality/preflight.py::MIN_MOLECULE_SCENARIOS",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "secure_sdlc_roles",
        "category": "security",
        "description": (
            "7 secure-SDLC Ansible roles: threat_model, security_review, "
            "secret_scan, sbom_generate, supply_chain_verify, "
            "security_requirements, security_gate."
        ),
        "acceptance_criteria": [
            "All 7 roles exist under collections/.../agent/roles/",
            "molecule scenarios for each",
        ],
        "evidence": [
            "role:threat_model",
            "role:security_review",
            "role:secret_scan",
            "role:sbom_generate",
            "role:supply_chain_verify",
            "role:security_requirements",
            "role:security_gate",
            "molecule:role_threat_model",
            "molecule:role_security_gate",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "agile_roles",
        "category": "agile",
        "description": (
            "9 agile/sprint Ansible roles: story_create, estimate_story, "
            "backlog_groom, sprint_plan, standup_report, sprint_board_report, "
            "velocity_report, sprint_review, retrospective."
        ),
        "acceptance_criteria": [
            "All 9 roles exist under collections/.../agent/roles/",
        ],
        "evidence": [
            "role:story_create",
            "role:estimate_story",
            "role:backlog_groom",
            "role:sprint_plan",
            "role:standup_report",
            "role:sprint_board_report",
            "role:velocity_report",
            "role:sprint_review",
            "role:retrospective",
            "molecule:role_story_create",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "workflow_roles",
        "category": "ci",
        "description": (
            "5 workflow-pipeline Ansible roles: gate_triage, ci_pipeline_repair, "
            "flaky_quarantine, release_build, validate_and_push."
        ),
        "acceptance_criteria": [
            "All 5 roles exist under collections/.../agent/roles/",
        ],
        "evidence": [
            "role:gate_triage",
            "role:ci_pipeline_repair",
            "role:flaky_quarantine",
            "role:release_build",
            "role:validate_and_push",
            "molecule:role_gate_triage",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "ai_coding_roles",
        "category": "agent",
        "description": (
            "Core AI-coding Ansible roles: agent_task, audit_dependencies, "
            "audit_security, debug_failure, dependency_update, document_change, "
            "refactor_code, report_audit, report_metrics, report_status, "
            "triage_issue, write_tests."
        ),
        "acceptance_criteria": [
            "All 12 roles exist under collections/.../agent/roles/",
        ],
        "evidence": [
            "role:agent_task",
            "role:audit_dependencies",
            "role:audit_security",
            "role:debug_failure",
            "role:dependency_update",
            "role:document_change",
            "role:refactor_code",
            "role:report_audit",
            "role:report_metrics",
            "role:report_status",
            "role:triage_issue",
            "role:write_tests",
            "molecule:role_agent_task",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "self_molecule_harness",
        "category": "ci",
        "description": (
            "Mock-daemon molecule harness so gludd can test its own API paths "
            "without a live process (gludd_mock_daemon role + scenarios)."
        ),
        "acceptance_criteria": [
            "molecule_self_test scenario exists",
        ],
        "evidence": [
            # The molecule scenario dir is named role_molecule_self_test (the
            # mock-daemon harness scenario); the role itself is molecule_self_test.
            "molecule:role_molecule_self_test",
            "role:self_improve_propose",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "self_improve_ab",
        "category": "agent",
        "description": (
            "A/B self-improvement pipeline: gludd_introspect + gludd_abtest + "
            "gludd_reload modules; crash-isolation, promote, rollback scenarios."
        ),
        "acceptance_criteria": [
            "gludd_introspect module exists",
            "gludd_abtest module exists",
            "gludd_reload module exists",
            "crash-isolation scenario present",
        ],
        "evidence": [
            "module:gludd_introspect",
            "module:gludd_abtest",
            "module:gludd_reload",
            "molecule:role_self_improve_ab_test",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "auditor_analyst_roles",
        "category": "agent",
        "description": (
            "13 auditor/analyst Ansible roles covering security-audit, "
            "metrics-analysis, code-quality, compliance, and reporting."
        ),
        "acceptance_criteria": [
            "At least 13 auditor/analyst roles under collections/.../agent/roles/",
        ],
        "evidence": [
            "role:soc_analyst",
            "role:qa_analyst",
            "role:log_analyst",
            "role:dead_code_auditor",
            "role:cost_optimization_auditor",
            "role:enhancement_auditor",
            "role:feature_gap_auditor",
            "role:ui_ux_analyst",
            "role:code_reviewer",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "tla_formal_collection",
        "category": "formal",
        "description": (
            "TLA+ formal specifications under collections/general_ludd/formal "
            "for the harness state machine."
        ),
        "acceptance_criteria": [
            "general_ludd.formal Ansible collection exists",
        ],
        "evidence": [
            "file:collections/ansible_collections/general_ludd/formal/galaxy.yml::namespace",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "dogfood",
        "category": "self-verification",
        "description": (
            "scripts/dogfood_features.py (make dogfood-features) runs FeatureVerifier "
            "evidence checks against FEATURE_SEED so gludd verifies its own features. "
            "scripts/dogfood.py exercises the full product spine (event-loop, git, review)."
        ),
        "acceptance_criteria": [
            "scripts/dogfood_features.py exists",
            "make dogfood-features target defined",
        ],
        "evidence": [
            "file:scripts/dogfood_features.py::FeatureVerifier",
            "file:scripts/dogfood.py::run_dogfood",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "ci_green",
        "category": "ci",
        "description": (
            "The ci_green feature records the local gate artifact: lint 0, typecheck 0, "
            "collect 0, test 0, and smoke PASS. GitHub Actions verification is not yet connected."
        ),
        "acceptance_criteria": [
            "make gate passes in CI (not just locally)",
        ],
        # .gate-status is a build artifact (.gitignore line 18) — absent in a
        # fresh checkout and in CI, so it can NEVER serve as reproducible
        # evidence. Removed; the daemon-lifespan integration test is the real,
        # reproducible proof the gated paths boot. The "green in GitHub Actions"
        # claim itself remains unverified-in-CI, so status stays implemented (not
        # verified) honestly.
        "evidence": [
            "test:tests/integration/test_daemon_lifespan.py",
        ],
        "verifier_kind": "evidence",
        # Note: CI-green unverified-in-CI at seed time
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "dynamic_dispatch",
        "category": "agent",
        "description": (
            "Dynamic task dispatch: route playbooks to agents based on resource "
            "profiles and live queue depth via the AdaptiveRouter."
        ),
        "acceptance_criteria": [
            "AdaptiveRouter selects playbook per resource profile",
        ],
        "evidence": [],
        "verifier_kind": "evidence",
        "status": "requested",
        "requested_by": "engagement",
    },
    {
        "name": "spend_limiter",
        "category": "budget",
        "description": (
            "Per-task and global spend limiter: RunBudgetGuard + BudgetManager "
            "enforce daily_limit and per_task_limit from config."
        ),
        "acceptance_criteria": [
            "RunBudgetGuard rejects tasks when budget exceeded",
        ],
        "evidence": [],
        "verifier_kind": "evidence",
        "status": "requested",
        "requested_by": "engagement",
    },
    {
        "name": "accounting",
        "category": "budget",
        "description": (
            "Per-project cost accounting: BudgetManager tracks spend per project "
            "and exposes it via /api/metrics."
        ),
        "acceptance_criteria": [
            "cost_by_project exposed in /api/metrics",
        ],
        "evidence": [],
        "verifier_kind": "evidence",
        "status": "requested",
        "requested_by": "engagement",
    },
    {
        "name": "feature_db",
        "category": "self-verification",
        "description": (
            "Feature database: FeatureModel + FeatureRepository + FeatureVerifier "
            "(evidence-gated) + FEATURE_SEED + /api/features endpoints."
        ),
        "acceptance_criteria": [
            "FeatureModel in DB schema",
            "FeatureRepository CRUD",
            "FeatureVerifier evidence-gated (never self-assert)",
            "/api/features endpoints registered",
        ],
        "evidence": [
            "file:src/general_ludd/db/models.py::FeatureModel",
            "file:src/general_ludd/db/repository.py::FeatureRepository",
            "file:src/general_ludd/quality/feature_verifier.py::FeatureVerifier",
            "test:tests/unit/test_feature_verifier.py",
            "test:tests/unit/test_feature_repo.py",
        ],
        "verifier_kind": "evidence",
        # THIS feature — implemented by this commit
        "status": "implemented",
        "requested_by": "engagement",
    },
    {
        "name": "webmcp_tool_exposure",
        "category": "mcp",
        "description": (
            "Expose gludd tools via Model Context Protocol so external AI clients "
            "can call harness operations over the MCP wire protocol."
        ),
        "acceptance_criteria": [
            "MCP tool registry wired to /api endpoints",
        ],
        "evidence": [],
        "verifier_kind": "evidence",
        "status": "requested",
        "requested_by": "engagement",
    },
    {
        "name": "concurrency_scheduler",
        "category": "core",
        "description": (
            "Concurrency scheduler: hard_cap / soft_cap per queue enforced "
            "by the event loop; bucket leases prevent cross-agent contention."
        ),
        "acceptance_criteria": [
            "hard_cap / soft_cap enforced by event loop",
            "BucketLeaseModel prevents cross-agent contention",
        ],
        "evidence": [],
        "verifier_kind": "evidence",
        "status": "requested",
        "requested_by": "engagement",
    },
    {
        "name": "profile_docs",
        "category": "docs",
        "description": (
            "Auto-generated docs/profiles/ from model/prompt profiles for "
            "playbook authors to reference."
        ),
        "acceptance_criteria": [
            "docs/profiles/ directory exists",
            "At least one profile doc generated",
        ],
        "evidence": [],
        "verifier_kind": "evidence",
        # docs/profiles/ directory and generated profile docs do not exist;
        # docs/profiles.md is a hand-written how-to guide, not auto-generated docs.
        # Downgraded from false-implemented to requested (honest status).
        "status": "requested",
        "requested_by": "engagement",
    },
    {
        "name": "feature_db_integration",
        "category": "self-verification",
        "description": (
            "Feature database integration: gludd_features Ansible module + feature_audit role "
            "+ molecule scenarios (test_gludd_features + role_feature_audit) "
            "+ make dogfood-features self-check. Completes deferred items from feature/feature-db."
        ),
        "acceptance_criteria": [
            "gludd_features module exists under plugins/modules/",
            "feature_audit role exists under roles/",
            "test_gludd_features molecule scenario exists",
            "role_feature_audit molecule scenario exists",
            "scripts/dogfood_features.py exists",
        ],
        "evidence": [
            "module:gludd_features",
            "role:feature_audit",
            "molecule:test_gludd_features",
            "molecule:role_feature_audit",
            "file:scripts/dogfood_features.py::run_dogfood_features",
        ],
        "verifier_kind": "evidence",
        "status": "implemented",
        "requested_by": "engagement",
    },
]
