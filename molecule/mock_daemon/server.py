#!/usr/bin/env python3
"""Reusable stdlib-only mock daemon for molecule scenarios.

This is the HONEST molecule harness: the REAL general_ludd.agent modules
execute unchanged and hit this server over HTTP, so the modules' own logic
(auth headers, status handling, payload shaping, ansible_facts injection) is
genuinely exercised. ONLY the daemon (and any external network it would reach)
is mocked — nothing shadows the modules themselves.

It implements every endpoint the gludd_* modules call, with canned JSON whose
shape matches what each module parses:

  GET  /healthz                       -> 200 {"status":"ok"}                (gludd_ping)
  GET  /api/facts                     -> 200 work/todos/models/history/...  (gludd_facts)
  GET  /api/metrics                   -> 200 agents/usage/cost/rankings     (gludd_metrics)
  GET  /api/traces                    -> 200 recent/by_phase/otel status    (gludd_traces)
  GET  /api/observe/sources           -> 200 registered source metadata     (gludd_observe discovery)
  POST /api/observe/query             -> 200 source records / isolated 503  (gludd_observe fan-out)
  GET  /api/messages                  -> 200 {"messages":[...]}             (gludd_message receive)
  POST /api/messages                  -> 201 created message                (gludd_message send)
  POST /api/messages/<id>/ack         -> 200 {"acked":true}                 (gludd_message ack)
  POST /admin/models/call             -> 200 {"text":..,"usage":..}
                                             (model-call agent modules)
  POST /admin/models/workflow         -> 200 {"content":..,"quality_score":..} (gludd_langgraph_workflow)
  GET  /api/todos/<id>                 -> 200 todo record                    (gludd_db todo_get)
  PATCH /api/todos/<id>               -> 200 {"status":..}                  (gludd_db todo_update_status)
  GET  /api/resource-preferences      -> 200 {"preference":..}              (gludd_db resource_preference)
  GET  /api/features                  -> 200 {"features":[...],"total":N}    (gludd_features list)
  POST /api/features/verify           -> 200 {"summary":{...},"results":[]}  (gludd_features verify)
  GET  /api/spend                     -> 200 spend snapshot                  (gludd_spend get)
  POST /api/spend/configure           -> 200 updated config                  (gludd_spend configure)
  GET  /api/accounting                -> 200 {"accounting":[...],"total":N}  (gludd_accounting all)
  GET  /api/accounting/<project_id>   -> 200 {ProjectAccounting snapshot}    (gludd_accounting project)
  POST /api/schedule                  -> 200 {"batches":[[id,...],...]]}      (gludd_schedule)
  POST /api/dispatch                  -> 200 {"result":{...}}                (gludd_dispatch dispatch)
  GET  /api/dispatch/available        -> 200 {"handlers":[...]}              (gludd_dispatch available)
  GET  /api/dispatch/recent           -> 200 {"records":[...]}               (gludd_dispatch recent)
  POST /admin/stream/dispatch         -> 200 {task_id, clone_path, accepted} (gludd_stream chunk dispatch)
  GET  /api/environment               -> 200 consolidated env brief          (gludd_environment snapshot)
  GET  /api/environment/advise        -> 200 per-work-type advice block      (gludd_environment advice merge)
  GET  /admin/processes               -> 200 {"processes":[...],"count":N}    (gludd_process list / gludd_proc_monitor)
  GET  /admin/processes/<pid>/stats   -> 200 psutil-shaped stats snapshot
                                             (gludd_process status / gludd_proc_monitor)
  POST /admin/processes/<pid>/signal  -> 200 {"ok":true,"pid":..,"signal":..} (gludd_process signal)
  POST /admin/abtest/run              -> 200 fail-closed A/B verdict       (gludd_abtest)
  POST /admin/git/operation           -> 200 bounded worktree/git result   (gludd_git)
  POST /admin/make                    -> 200 allowlisted make result        (gludd_make)
  POST /admin/skills/render           -> 200 rendered skill artifact       (gludd_skill)
  POST /admin/reload/code             -> 200 atomic reload/rollback result (gludd_reload)
  POST /api/observe/facade            -> 200 fan-out/timeline/correlation   (gludd_observe)
  POST /api/language/execute           -> 200 bounded language result        (language_operation)
  GET  /admin/ornith/pairs            -> 200 {"pairs":[...],"count":N}
                                              (gludd_ornith rejected pairs)
  GET  /process-audit                  -> 200 guardrail_health/plugin_footprint/.. (gludd_audit)
  POST /api/human-todos               -> 201 created human-todo               (gludd_human_todo present)
  POST /admin/sts/mint                -> 201 {"token":"sts-mock-..."}         (STS token mint)
  GET  /admin/sts/validate/<agent_id>  -> 200 {"valid":true,...}              (STS token validate)
  GET  /admin/sts/tokens/<agent_id>   -> 200 single token record              (STS token get)
  GET  /admin/sts/tokens              -> 200 {"tokens":[...]}                 (STS token list)
  POST /admin/sts/revoke/<agent_id>   -> 200 {"revoked":true}                 (STS token revoke)

Plus an in-memory request-log introspection seam (NOT a daemon endpoint — a
test affordance) so verify plays can prove, per role-invocation, which
endpoints fired and which did NOT:

  GET  /__requests                    -> 200 {"requests":["METHOD PATH",...]}
  POST /__requests/reset              -> 200 {"reset":true}

Usage:
    python3 server.py --port 8765 --pidfile /tmp/x.pid --logfile /tmp/x.log

Run it only for the play that consumes it.  ``--port 0`` delegates collision-free
loopback allocation to the kernel; ``--ready-file`` publishes the bound endpoint
atomically, and ``--lease-seconds`` bounds cleanup after controller cancellation.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# In-memory request log
# ---------------------------------------------------------------------------
# Records "METHOD PATH" for every served request so a verify play can prove,
# per-branch, exactly which endpoints fired (and which did NOT). The default
# BaseHTTPRequestHandler stderr log conflates every branch into one file (all
# branches share the same port), which cannot prove "branch X hit endpoint A
# but NOT endpoint B". This log is resettable (POST /__requests/reset) so the
# converge play can snapshot the calls of ONE role invocation in isolation.
# The introspection endpoints (/__requests*) are themselves excluded so reading
# the log never pollutes it.
_REQUEST_LOG: list[str] = []
_REQUEST_LOG_LOCK = threading.Lock()


def _record_request(method: str, path: str) -> None:
    if path.startswith("/__requests"):
        return
    with _REQUEST_LOG_LOCK:
        _REQUEST_LOG.append(f"{method} {path}")


def _snapshot_requests() -> list[str]:
    with _REQUEST_LOG_LOCK:
        return list(_REQUEST_LOG)


def _reset_requests() -> None:
    with _REQUEST_LOG_LOCK:
        _REQUEST_LOG.clear()


# ---------------------------------------------------------------------------
# Canned responses (shapes mirror what each module's main() parses)
# ---------------------------------------------------------------------------

# Metrics + traces sections shaped like the real /api/metrics + /api/traces
# (and embedded in /api/facts as gludd.metrics / gludd.traces).
METRICS_SNAPSHOT = {
    "agents": [
        {
            "agent_id": "agent-mock-1",
            "agent_name": "coder",
            "status": "running",
            "project": "mockproj",
            "total_tokens": 440,
            "total_cost_usd": 0.0012,
        },
    ],
    "total_agents": 1,
    "running_agents": 1,
    "global_model_usage": {
        "mock-profile": {
            "total_calls": 3,
            "success_rate": 0.6666666666666666,
            "total_cost_usd": 0.0012,
        },
    },
    "cost_by_project": {"mockproj": 0.0012},
    "benchmark_rankings": [
        {
            "model_profile_id": "mock-profile",
            "prompt_profile_id": "default",
            "task_type": "code",
            "composite_score": 0.81,
            "sample_count": 5,
        },
    ],
}

TRACES_SNAPSHOT = {
    "count": 1,
    "total_recorded": 1,
    "recent": [
        {
            "trace_id": "trace-mock0001",
            "todo_id": "TODO-001",
            "work_type": "code",
            "total_cost_usd": 0.0026,
            "total_tokens": 190,
            "success_rate": 1.0,
            "span_count": 2,
            "spans": [
                {"span_id": "span-a", "phase": "plan", "status": "success", "output_tokens": 40, "cost_usd": 0.0005},
                {
                    "span_id": "span-b",
                    "phase": "generate",
                    "status": "success",
                    "output_tokens": 150,
                    "cost_usd": 0.0021,
                },
            ],
        },
    ],
    "by_phase": {
        "plan": {"span_count": 1, "total_cost_usd": 0.0005, "total_tokens": 40, "success_count": 1},
        "generate": {"span_count": 1, "total_cost_usd": 0.0021, "total_tokens": 150, "success_count": 1},
    },
    "otel_exporter_status": "disabled",
}


# Registered observability sources + normalized records used by the direct
# gludd_observe scenario.  The mock intentionally ignores the query bounds so
# the real facade's defensive post-filtering is exercised.  ``broken-events``
# returns 503 in ``do_POST`` to prove one failing connector does not abort the
# successful fan-out.
OBSERVE_SOURCES: list[dict[str, str]] = [
    {"name": "prod-logs", "kind": "logs"},
    {"name": "prod-metrics", "kind": "metrics"},
    {"name": "prod-traces", "kind": "traces"},
    {"name": "broken-events", "kind": "events"},
]

OBSERVE_RECORDS: dict[str, list[dict[str, Any]]] = {
    "prod-logs": [
        {
            "ts": 20.0,
            "source": "prod-logs",
            "kind": "logs",
            "level_or_status": "error",
            "message": "checkout request timed out",
            "labels": {
                "trace_id": "incident-42",
                "service": "checkout",
                "host": "Web-01:8080",
            },
        },
        {
            "ts": 60.0,
            "source": "prod-logs",
            "kind": "logs",
            "level_or_status": "info",
            "message": "outside requested window",
            "labels": {
                "trace_id": "incident-42",
                "service": "checkout",
                "host": "Web-01:8080",
            },
        },
    ],
    "prod-metrics": [
        {
            "ts": 10.0,
            "source": "prod-metrics",
            "kind": "metrics",
            "level_or_status": "warn",
            "message": "latency elevated",
            "value": 1.25,
            "labels": {
                "trace_id": "incident-42",
                "service": "checkout",
                "host": "web-01",
            },
        }
    ],
    "prod-traces": [
        {
            "ts": 30.0,
            "source": "prod-traces",
            "kind": "traces",
            "level_or_status": "error",
            "message": "upstream timeout",
            "labels": {
                "trace_id": "incident-42",
                "service": "checkout",
                "host": "WEB-01",
            },
        }
    ],
}

FACTS_SNAPSHOT = {
    "work": {
        "active_jobs": 1,
        "queued_jobs": 2,
        "queues": {"core": 2, "intake": 0},
    },
    "todos": {
        "backlog_size": 3,
        "items": [
            {"id": "TODO-001", "title": "mock todo one", "status": "backlog"},
            {"id": "TODO-002", "title": "mock todo two", "status": "backlog"},
        ],
    },
    "models": {
        "default_profile": "mock-profile",
        "available": ["mock-profile"],
    },
    "history": {
        "success_rate": 0.92,
        "total_runs": 25,
        "failures": 2,
    },
    "messages": {
        "unread": 0,
        "inbox": [],
    },
    "metrics": METRICS_SNAPSHOT,
    "traces": TRACES_SNAPSHOT,
    # Codebase self-introspection block consumed by gludd_introspect
    # (resp.get("codebase", {})) and the self_improve_propose role, which picks
    # the lowest-line_rate file under codebase.coverage.low_coverage.
    "codebase": {
        "churn": {
            "hot_files": [
                {"file": "src/general_ludd/example/leaf.py", "commits": 12},
                {"file": "src/general_ludd/util/helpers.py", "commits": 7},
            ],
        },
        "complexity": {
            "worst": [
                {"file": "src/general_ludd/example/leaf.py", "cyclomatic": 18},
            ],
        },
        "coverage": {
            "overall_line_rate": 0.74,
            "low_coverage": [
                {"file": "src/general_ludd/util/helpers.py", "line_rate": 0.31},
                {"file": "src/general_ludd/example/leaf.py", "line_rate": 0.55},
            ],
        },
        "debt": {
            "todo_count": 4,
            "items": [
                {"file": "src/general_ludd/util/helpers.py", "marker": "TODO", "line": 22},
            ],
        },
        "dead_code": {"candidates": []},
        "missing_tests": {"modules": ["src/general_ludd/util/helpers.py"]},
        "perf_cost": {"slowest_phase": "generate", "total_cost_usd": 0.0026},
        "recent_failures": {"count": 2, "rate": 0.08},
    },
}


ACCOUNTING_SNAPSHOT = [
    {
        "project_id": "mock-project-alpha",
        "elapsed_seconds": 120.5,
        "tokens_used": 4400,
        "usd_spent": 0.0048,
        "quota_usd": 1.0,
        "pct_quota": 0.48,
        "loc_changed": 0,
        "role_stats": {"implement_change": 2, "code_reviewer": 1},
        "todo_summary": {"pending": 3, "in_progress": 1, "done": 5},
        "points_estimated": 18,
        "points_done": 12,
    },
    {
        "project_id": "mock-project-beta",
        "elapsed_seconds": 60.0,
        "tokens_used": 1800,
        "usd_spent": 0.0019,
        "quota_usd": 1.0,
        "pct_quota": 0.19,
        "loc_changed": 0,
        "role_stats": {"report_status": 1},
        "todo_summary": {"pending": 1, "done": 2},
        "points_estimated": 6,
        "points_done": 4,
    },
]


FEATURES_SNAPSHOT: list[dict[str, Any]] = [
    {
        "id": "FEAT-0001",
        "project_id": None,
        "name": "facts_api_mq",
        "description": "gludd_facts Ansible module exposes /api/facts as dynamic variables.",
        "category": "api",
        "status": "implemented",
        "acceptance_criteria": ["gludd_facts module exists"],
        "evidence": ["module:gludd_facts", "molecule:test_gludd_facts"],
        "verifier_kind": "evidence",
        "requested_by": "engagement",
        "requested_at": "2026-01-01T00:00:00",
        "verified_at": None,
        "last_verify_detail": {},
    },
    {
        "id": "FEAT-0002",
        "project_id": None,
        "name": "feature_db",
        "description": "Feature database with FeatureModel, FeatureRepository, FeatureVerifier.",
        "category": "self-verification",
        "status": "implemented",
        "acceptance_criteria": ["FeatureModel in DB schema"],
        "evidence": ["file:src/general_ludd/db/models.py::FeatureModel"],
        "verifier_kind": "evidence",
        "requested_by": "engagement",
        "requested_at": "2026-01-01T00:00:00",
        "verified_at": None,
        "last_verify_detail": {},
    },
]

SPEND_SNAPSHOT = {
    "limit_usd": 10.0,
    "used_usd": 3.75,
    "remaining_usd": 6.25,
    "window_seconds": 86400,
    "window_label": "24h",
    "period_start": "2026-06-15T00:00:00Z",
}


# ---------------------------------------------------------------------------
# OpenBao break-glass snapshot/restore canned responses
# ---------------------------------------------------------------------------
# The mock daemon stands in for an OpenBao server during molecule runs of the
# openbao_break_glass_backup role. The snapshot bytes are a fixed deterministic
# blob so the molecule verify play can assert that what the role wrote matches
# what the mock served, and that the GPG-encrypted tarball is well-formed.
OPENBAO_FAKE_SNAPSHOT = (
    b"OPENBAO-RAFT-SNAPSHOT-MOCK\nversion: 1\nnodes: 1\nindex: 42\n" + b"\x00\x01\x02\x03mock-raft-payload" * 8
)
OPENBAO_RESTORE_LAST_PAYLOAD: dict[str, Any] = {}

SPEND_CONFIGURE_RESPONSE = {
    "limit_usd": 10.0,
    "window_seconds": 86400,
    "updated": True,
}

VERIFY_SUMMARY = {
    "summary": {
        "total": 2,
        "verified_count": 0,
        "implemented_count": 2,
        "requested_count": 0,
        "regressed_count": 0,
    },
    "results": [
        {
            "id": "FEAT-0001",
            "name": "facts_api_mq",
            "status": "implemented",
            "verified_at": None,
            "evidence_results": {
                "all_met": False,
                "met_count": 1,
                "total_count": 2,
                "per_ref": [
                    {"ref": "module:gludd_facts", "met": True, "detail": "module found"},
                    {"ref": "molecule:test_gludd_facts", "met": False, "detail": "scenario not found in mock"},
                ],
            },
        },
        {
            "id": "FEAT-0002",
            "name": "feature_db",
            "status": "implemented",
            "verified_at": None,
            "evidence_results": {
                "all_met": False,
                "met_count": 1,
                "total_count": 1,
                "per_ref": [
                    {"ref": "file:src/general_ludd/db/models.py::FeatureModel", "met": True, "detail": "symbol found"},
                ],
            },
        },
    ],
}


# Consolidated environment brief shaped like routers/environment.py
# EnvironmentBrief. budget.run_remaining_usd is the field the agent_orchestrate
# role's budget-floor guard reads; keep it comfortably above the default floor so
# the role proceeds to act (the deferral path is exercised by overriding
# min_remaining_usd / work_type in the scenario).
ENVIRONMENT_SNAPSHOT = {
    "models": [
        {
            "profile_id": "mock-profile",
            "provider": "mock",
            "model": "glm-4.6",
            "enabled": True,
            "quality_class": "high",
            "context_window": 128000,
            "max_output_tokens": 4096,
            "api_metered": True,
        },
    ],
    "routing": {
        "default_profile": "mock-profile",
        "weak_model_profile": "mock-weak",
        "roles": {},
    },
    "budget": {
        "run_remaining_usd": 5.0,
        "run_limit_usd": 10.0,
        "run_spent_usd": 5.0,
        "elapsed_seconds": 42.0,
        "window": None,
    },
    "compute": {"providers": [], "gpu_types": [], "configured": None},
    "tools": [],
    "skills": [],
    "queues": [{"name": "todos", "depth": 3}],
    "system": {"cpu_count": 4, "python_version": "3.12.0"},
    "optimization": {"hints": [], "recommended_profile_for": {}},
}

# Work types the REAL advisor (controllers/environment_advisor.py
# _WORKFLOW_WORK_TYPES) routes through the multi-step LangGraph workflow. The
# mock mirrors that set so the role's advice.use_workflow branch is genuinely
# exercised for both true (feature/bugfix/refactor/review) and false (docs/chat/
# classify and anything else).
_WORKFLOW_WORK_TYPES = ("feature", "bugfix", "refactor", "review")


def _advice_response(work_type: str) -> dict[str, Any]:
    """Per-work-type advice block, shaped like routers/environment.py AdviceBrief.

    use_workflow follows the real advisor's workflow set so the agent_orchestrate
    role branches to the workflow module for feature/bugfix/refactor/review and
    to the single-shot router for everything else.
    """
    wt = (work_type or "").strip().lower()
    use_workflow = wt in _WORKFLOW_WORK_TYPES
    recommended_profile = "mock-weak" if wt == "bounded_small_model" else "mock-profile"
    return {
        "task_type": work_type,
        "recommendation": {
            "model_profile": recommended_profile,
            "reason": "mock_recommendation",
            "composite_score": 0.81,
            "fallback": False,
            "sample_count": 5,
            "latency_class": "standard",
            "quality_class": "high",
        },
        "route": {
            "selected_model_profile_id": recommended_profile,
            "selected_prompt_profile_id": "default",
        },
        "est_cost_usd": 0.0021,
        "use_workflow": use_workflow,
        "workflow_reason": (
            f"work_type '{wt}' benefits from gated multi-step workflow"
            if use_workflow
            else f"work_type '{wt}' is single-shot"
        ),
        "resource_hints": {
            "prefer_local": False,
            "budget_ok": True,
            "budget_warning": False,
            "context_fits": True,
        },
    }


def _schedule_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a concurrency-safe batched plan for the submitted work items.

    Implements a minimal real scheduler so the mock genuinely exercises the
    same response shape as the daemon:
      - Greenfield items and items with no conflicting resource neighbours
        may share a batch.
      - Dependency ordering is respected (topological sort).
    The mock keeps it simple: items with no depends_on go in batch 0 (grouped
    by resource to avoid conflicts), items that depend on batch-0 items go in
    batch 1, and so on.
    """
    items = payload.get("items", [])
    if not items:
        return {"batches": []}

    # Build index by id.
    by_id: dict[str, dict[str, Any]] = {it["id"]: it for it in items}

    # Topological depth: max depth among depends_on predecessors + 1.
    depths: dict[str, int] = {}

    def depth(item_id: str, seen: set[str] | None = None) -> int:
        if item_id in depths:
            return depths[item_id]
        if seen is None:
            seen = set()
        if item_id in seen:
            # cycle — return a large number to push it late
            return 999
        seen.add(item_id)
        item = by_id.get(item_id, {})
        deps = item.get("depends_on", []) or []
        d = max((depth(dep, seen) + 1 for dep in deps), default=0)
        depths[item_id] = d
        return d

    for it in items:
        depth(it["id"])

    max_depth = max(depths.values(), default=0)

    # Group items into waves by depth; within each wave avoid resource conflicts.
    # Greenfield items share no resources so they can always go in their wave bucket.
    batches: list[list[str]] = []
    for wave in range(max_depth + 1):
        wave_items = [it for it in items if depths.get(it["id"], 0) == wave]
        if not wave_items:
            continue
        # Split wave into sub-batches by resource conflict.
        used_resources: set[str] = set()
        current_batch: list[str] = []
        for it in wave_items:
            resources: list[str] = it.get("resources", []) or []
            is_greenfield: bool = it.get("is_greenfield", False)
            if is_greenfield or not resources:
                current_batch.append(it["id"])
            else:
                conflicts = used_resources & set(resources)
                if conflicts and current_batch:
                    batches.append(current_batch)
                    current_batch = [it["id"]]
                    used_resources = set(resources)
                else:
                    current_batch.append(it["id"])
                    used_resources |= set(resources)
        if current_batch:
            batches.append(current_batch)

    return {"batches": batches}


DISPATCH_HANDLERS = [
    {"kind": "tool", "name": "shell", "description": "Run a shell command on the agent host"},
    {"kind": "tool", "name": "read_file", "description": "Read a file from the agent host"},
    {"kind": "tool", "name": "write_file", "description": "Write a file on the agent host"},
]

DISPATCH_RECENT = [
    {
        "id": "dispatch-mock-0001",
        "kind": "tool",
        "name": "shell",
        "args": {"command": "echo hello"},
        "status": "success",
        "result": {"stdout": "hello", "returncode": 0},
        "dispatched_at": "2026-01-01T00:00:00",
    },
]


def _dispatch_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": {
            "id": "dispatch-mock-new",
            "kind": payload.get("kind", "tool"),
            "name": payload.get("name", "unknown"),
            "args": payload.get("args", {}),
            "status": "success",
            "output": "[mock-daemon] dispatch executed successfully.",
        },
    }


def _model_call_response(payload: dict[str, Any]) -> dict[str, Any]:
    # The langgraph/langchain decision module sends response_format="json" (and an
    # options list) and parses resp["text"] as JSON {"decision":..,"rationale":..}.
    # For those requests return a JSON-string text the decision module can parse
    # into a valid decision; for plain model_call/langchain_generate requests keep
    # the human-readable "[mock-daemon] ..." text (gludd_model_call asserts on it).
    if payload.get("response_format") == "json" or payload.get("options"):
        text = json.dumps({"decision": "proceed", "rationale": "looks correct"})
    else:
        text = "[mock-daemon] applied the requested change."
    return {
        "text": text,
        "model_profile_id": payload.get("model_profile") or "mock-profile",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _workflow_response(payload: dict[str, Any]) -> dict[str, Any]:
    # Mirrors POST /admin/models/workflow: the daemon runs a generate->review->retry
    # LangGraph loop server-side and returns the best content + quality metadata.
    # gludd_langgraph_workflow parses content/model/prompt_profile/quality_score/
    # retries/warnings out of this body.
    return {
        "content": "def solution(): return 42",
        "model": "glm-4.6",
        "prompt": "coder",
        "quality_score": 0.82,
        "retries": 1,
        "warnings": [],
    }


def _message_created(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "MSG-MOCK-0001",
        "sender": payload.get("sender"),
        "recipient": payload.get("recipient"),
        "topic": payload.get("topic", ""),
        "priority": payload.get("priority", "normal"),
        "status": "unread",
    }


def _todo_record(todo_id: str) -> dict[str, Any]:
    return {
        "id": todo_id,
        "title": "mock todo",
        "description": "fetched from mock daemon",
        "status": "backlog",
        "queue": "core",
        "work_type": "code",
    }


# ---------------------------------------------------------------------------
# gludd_stream dispatch (POST /admin/stream/dispatch)
# ---------------------------------------------------------------------------
# Mirrors the daemon-side stream-dispatch contract: the gludd_stream module
# posts each chunk + role-clone spec to /admin/stream/dispatch; the daemon
# spins up a sub-agent running the cloned role and returns the new task id +
# the on-disk path to the cloned role invocation. The mock returns canned
# values so the gludd_stream module's HTTP path + payload shaping is exercised
# end-to-end without real hardware or sub-agents.

_STREAM_DISPATCH_COUNTER = 0
_STREAM_DISPATCH_LOCK = threading.Lock()


def _stream_dispatch_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Canned stream-dispatch response (task_id + clone_path).

    Each call returns a DISTINCT task_id so the dual-dispatch path
    (input_key mode=both, which POSTs twice per key hit) can be told apart
    by callers and verify plays.
    """
    global _STREAM_DISPATCH_COUNTER
    role = (payload.get("dispatch_role_clone") or {}).get("role", "unknown")
    inject_as = (payload.get("dispatch_role_clone") or {}).get("inject_as", "stream_chunk")
    with _STREAM_DISPATCH_LOCK:
        _STREAM_DISPATCH_COUNTER += 1
        seq = _STREAM_DISPATCH_COUNTER
    extra = payload.get("extra_vars") or {}
    position = extra.get("stream_chunk_position", "single")
    return {
        "task_id": f"stream-task-mock-{seq:04d}",
        "clone_path": f"/tmp/gludd-stream-clone-{role}-{seq}.json",
        "accepted": True,
        "inject_as": inject_as,
        "stream_chunk_position": position,
        "stream_chunk_index": extra.get("stream_chunk_index", 0),
    }


# ---------------------------------------------------------------------------
# Model performance tracking canned responses
# ---------------------------------------------------------------------------
# GET /admin/models/performance returns per-profile performance metrics across
# all task types. GET /admin/models/ranking?task_type=X returns ranked profiles
# for a given task type, filtered by the query parameter.
MODEL_PERFORMANCE_SNAPSHOT = {
    "profiles": [
        {
            "model_profile_id": "mock-profile",
            "prompt_profile_id": "default",
            "task_type": "plan",
            "success_rate": 0.85,
            "avg_tokens": 450,
            "avg_cost_usd": 0.0012,
            "avg_latency_ms": 1200,
            "sample_count": 20,
            "last_evaluated": "2026-06-28T00:00:00Z",
        },
        {
            "model_profile_id": "mock-profile",
            "prompt_profile_id": "default",
            "task_type": "code",
            "success_rate": 0.92,
            "avg_tokens": 1200,
            "avg_cost_usd": 0.0035,
            "avg_latency_ms": 2800,
            "sample_count": 50,
            "last_evaluated": "2026-06-29T00:00:00Z",
        },
        {
            "model_profile_id": "mock-profile",
            "prompt_profile_id": "review",
            "task_type": "plan",
            "success_rate": 0.78,
            "avg_tokens": 300,
            "avg_cost_usd": 0.0009,
            "avg_latency_ms": 900,
            "sample_count": 15,
            "last_evaluated": "2026-06-27T00:00:00Z",
        },
    ],
}


def _ranking_response(task_type: str) -> dict[str, Any]:
    """Return rankings filtered by task_type, sorted by composite_score descending."""
    all_rankings: list[dict[str, Any]] = [
        {
            "model_profile_id": "mock-profile",
            "prompt_profile_id": "default",
            "task_type": "plan",
            "composite_score": 0.85,
            "success_rate": 0.85,
            "avg_cost_usd": 0.0012,
            "avg_latency_ms": 1200,
            "sample_count": 20,
        },
        {
            "model_profile_id": "mock-profile",
            "prompt_profile_id": "review",
            "task_type": "plan",
            "composite_score": 0.78,
            "success_rate": 0.78,
            "avg_cost_usd": 0.0009,
            "avg_latency_ms": 900,
            "sample_count": 15,
        },
        {
            "model_profile_id": "mock-profile",
            "prompt_profile_id": "default",
            "task_type": "code",
            "composite_score": 0.92,
            "success_rate": 0.92,
            "avg_cost_usd": 0.0035,
            "avg_latency_ms": 2800,
            "sample_count": 50,
        },
    ]
    q = (task_type or "").strip().lower()
    filtered = [r for r in all_rankings if r["task_type"] == q] if q else list(all_rankings)
    filtered.sort(key=lambda r: r["composite_score"], reverse=True)
    return {"rankings": filtered, "task_type": task_type}


# ---------------------------------------------------------------------------
# Ornith training-pair canned responses
# ---------------------------------------------------------------------------
# gludd_ornith state=pairs hits GET /admin/ornith/pairs?status=...&limit=N.
# The mock returns 3 rejected pairs that all target the SAME artifact so the
# ornith_self_improve role's rejection-count threshold (default 3) is met and
# the artifact is selected for improvement.
ORNITH_PAIRS_SNAPSHOT = [
    {
        "id": "ORN-MOCK-0001",
        "invoked_at": "2026-06-20T04:00:00",
        "task_description": "improve agent_orchestrate.yml",
        "target_files": ["playbooks/agent_orchestrate.yml"],
        "scaffold_kind": "playbook",
        "scaffold_content": "---\n- name: old\n  hosts: localhost\n",
        "scaffold_hash": "deadbeef",
        "iterations_used": 3,
        "tokens_consumed": 1500,
        "model_sha": "mock-sha",
        "outcome_status": "rejected_by_gate",
        "outcome_details": {"gate_output": "ansible-syntax failed"},
        "outcome_set_at": "2026-06-20T04:05:00",
        "project_id": None,
        "agent_id": "ornith_self_improve",
    },
    {
        "id": "ORN-MOCK-0002",
        "invoked_at": "2026-06-21T04:00:00",
        "task_description": "improve agent_orchestrate.yml",
        "target_files": ["playbooks/agent_orchestrate.yml"],
        "scaffold_kind": "playbook",
        "scaffold_content": "---\n- name: old\n  hosts: localhost\n",
        "scaffold_hash": "deadbeef",
        "iterations_used": 4,
        "tokens_consumed": 1800,
        "model_sha": "mock-sha",
        "outcome_status": "rejected_by_review",
        "outcome_details": {"reviewer": "human", "reason": "missing gludd_facts"},
        "outcome_set_at": "2026-06-21T04:10:00",
        "project_id": None,
        "agent_id": "ornith_self_improve",
    },
    {
        "id": "ORN-MOCK-0003",
        "invoked_at": "2026-06-22T04:00:00",
        "task_description": "improve agent_orchestrate.yml",
        "target_files": ["playbooks/agent_orchestrate.yml"],
        "scaffold_kind": "playbook",
        "scaffold_content": "---\n- name: old\n  hosts: localhost\n",
        "scaffold_hash": "deadbeef",
        "iterations_used": 2,
        "tokens_consumed": 1200,
        "model_sha": "mock-sha",
        "outcome_status": "reverted",
        "outcome_details": {"revert_reason": "regression in test_x"},
        "outcome_set_at": "2026-06-22T04:15:00",
        "project_id": None,
        "agent_id": "ornith_self_improve",
    },
]


def _ornith_pairs_response(status_csv: str, limit: int) -> dict[str, Any]:
    """Return Ornith training pairs filtered by the comma-separated statuses."""
    statuses = {s.strip() for s in (status_csv or "").split(",") if s.strip()}
    if not statuses:
        return {"pairs": [], "count": 0}
    filtered = [p for p in ORNITH_PAIRS_SNAPSHOT if p["outcome_status"] in statuses]
    return {"pairs": filtered[:limit], "count": len(filtered)}


def _human_todo_created(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape mirrors POST /api/human-todos response (HumanTodoModel dict)."""
    return {
        "id": "HTODO-MOCK-0001",
        "parent_agent_todo_id": payload.get("parent_agent_todo_id"),
        "agent_id": payload.get("agent_id", "ornith_self_improve"),
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
        "category": payload.get("category", "decision"),
        "priority": payload.get("priority", "medium"),
        "status": "open",
        "tags": payload.get("tags", []),
    }


# ---------------------------------------------------------------------------
# Managed-process registry (gludd_process / gludd_proc_monitor)
# ---------------------------------------------------------------------------
# Shape mirrors the daemon's managed-process API: GET /admin/processes returns a
# registry of processes the daemon launched; GET /admin/processes/<pid>/stats
# returns a live psutil snapshot; POST /admin/processes/<pid>/signal delivers a
# signal. _MANAGED_PID_OVERRIDE, when set via --managed-pid, replaces the first
# record's pid/pgid with a REAL process spawned by the scenario's prepare.yml so
# the listing references a pid that actually exists on the test host (the
# test_gludd_process scenario nohup-spawns a `sleep` and passes its pid).
_MANAGED_PID_OVERRIDE = 0

MANAGED_PROCESSES = [
    {
        "pid": 424242,
        "command": ["sleep", "300"],
        "pgid": 424242,
        "job_id": "job-mock-0001",
        "project_id": "mock-project-alpha",
        "origin": "managed",
        "registered_at": "2026-01-01T00:00:00",
        "create_time": 1735689600.0,
        "alive": True,
    },
]


def _managed_processes() -> list[dict[str, Any]]:
    procs = [dict(p) for p in MANAGED_PROCESSES]
    if _MANAGED_PID_OVERRIDE > 0 and procs:
        procs[0]["pid"] = _MANAGED_PID_OVERRIDE
        procs[0]["pgid"] = _MANAGED_PID_OVERRIDE
    return procs


def _process_stats(pid: int) -> dict[str, Any]:
    """psutil-shaped stats snapshot for one managed process (canned)."""
    return {
        "pid": pid,
        "cpu_percent": 0.5,
        "memory": {"rss": 1048576, "vms": 4194304},
        "io": {
            "read_bytes": 0,
            "write_bytes": 0,
            "read_count": 0,
            "write_count": 0,
        },
        "num_fds": 5,
        "num_threads": 1,
        "num_ctx_switches": {"voluntary": 2, "involuntary": 0},
        "status": "sleeping",
        "open_files": [],
        "locks": [],
    }


def _signal_response(pid: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Acknowledge a signal delivery (gludd_process action=signal)."""
    return {
        "ok": True,
        "pid": pid,
        "signal": payload.get("signal", "SIGTERM"),
        "group": bool(payload.get("group", False)),
    }


def _abtest_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic fail-closed A/B evidence for module scenarios."""
    candidate = str(payload.get("candidate_root", ""))
    crashed = "crash" in candidate.lower()
    baseline_result: dict[str, Any] = {
        "ok": True,
        "crashed": False,
        "timed_out": False,
        "duration_s": 0.01,
        "error": "",
    }
    candidate_result: dict[str, Any] = {
        "ok": not crashed,
        "crashed": crashed,
        "timed_out": False,
        "duration_s": 0.01,
        "error": "candidate crashed" if crashed else "",
    }
    promote = not crashed
    verdict = {
        "a": baseline_result,
        "b": candidate_result,
        "promote": promote,
        "reason": "candidate crashed" if crashed else "candidate passed",
    }
    return {"verdict": verdict, "promote": promote}


def _private_tmp_path(value: object) -> Path:
    """Resolve a mock mutation target and fail closed outside private temp space."""
    root = Path("/tmp").resolve()
    path = Path(str(value)).resolve()
    if path == root or root not in path.parents:
        raise ValueError(f"mock control-plane path is outside {root}: {path}")
    return path


def _git_operation_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Emulate daemon-owned GitAutomation results for isolated scenario paths."""
    operation = str(payload.get("op", ""))
    changed = operation not in {
        "current_branch",
        "branch_list",
        "worktree_list",
        "verify_remote",
        "state",
        "ci_verdict",
    }
    result: dict[str, Any] = {"success": True, "op": operation}
    if operation == "worktree_create":
        worktree = _private_tmp_path(payload.get("worktree_path"))
        worktree.mkdir(parents=True, exist_ok=True)
        result.update(
            {
                "branch": str(payload.get("branch", "")),
                "worktree_path": str(worktree),
            }
        )
    elif operation == "worktree_remove":
        worktree = _private_tmp_path(payload.get("worktree_path"))
        shutil.rmtree(worktree, ignore_errors=False)
        result.update({"removed": True, "worktree_path": str(worktree)})
    elif operation in {"commit", "gated_commit"}:
        result.update(
            {
                "sha": "0123456789abcdef",
                "message": str(payload.get("message", "molecule commit")),
            }
        )
    elif operation in {"branch", "current_branch"} or operation in {"push", "verify_remote"}:
        result["branch"] = str(payload.get("branch") or "main")
    return {"result": result, "changed": changed}


def _make_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the stable MakeRunner result contract used by module scenarios."""
    target = str(payload.get("target", ""))
    outputs = {
        "hello": "hello from molecule test_gludd_make\n",
        "versions": "make version: GNU Make 4.4\n",
    }
    success = target in outputs
    return {
        "target": target,
        "exit_code": 0 if success else 2,
        "success": success,
        "duration_s": 0.01,
        "stdout_tail": outputs.get(target, ""),
        "stderr_tail": "" if success else f"No rule to make target '{target}'\n",
        "timed_out": False,
        "oom_killed": False,
        "error": None,
        "phases": [],
    }


def _skill_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Render the scenario skill through the authenticated daemon seam."""
    variables = payload.get("variables")
    rendered_variables = variables if isinstance(variables, dict) else {}
    language = str(rendered_variables.get("language", "python"))
    project = str(rendered_variables.get("project_name", "gludd"))
    return {
        "skill_name": str(payload.get("name") or "mock-review"),
        "rendered_body": f"Review {language} changes for {project}.",
        "required_vars": ["language", "project_name"],
    }


def _language_operation_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic daemon-owned schema for one language operation."""
    operation = str(payload.get("operation") or "")
    operation_payload = payload.get("payload")
    if not isinstance(operation_payload, dict):
        raise TypeError("language operation payload must be a dictionary")
    input_text = str(operation_payload.get("input_text") or "")
    responses: dict[str, dict[str, Any]] = {
        "bom_detect": {
            "bom_detected": True,
            "encoding": "utf-8-sig",
            "bom_name": "UTF-8",
        },
        "encoding_detect": {
            "detected_encoding": "ascii",
            "confidence": 1.0,
            "confidence_level": "trusted",
        },
        "homoglyph_scan": {
            "input_length": len(input_text),
            "total_findings": 0,
            "findings": [],
        },
        "language_detect": {
            "language": "en",
            "confidence": 0.99,
        },
        "locale_format": {
            "locale": str(operation_payload.get("locale") or "en-US"),
            "formatted_value": "1.234,56",
            "is_rtl": False,
            "first_day_of_week": 1,
        },
        "phonetic_transcribe": {
            "method": str(operation_payload.get("method") or "arpabet"),
            "words": [{"word": "HELLO", "phonemes": ["HH", "AH0", "L", "OW1"]}],
        },
        "translate": {
            "translated_text": "Bonjour",
            "source_language": str(operation_payload.get("source_language") or "auto"),
            "target_language": str(operation_payload.get("target_language") or "fr"),
        },
        "transliterate": {
            "transliterated_text": input_text,
            "target_script": str(operation_payload.get("target_script") or "Latin"),
            "scheme": str(operation_payload.get("scheme") or "default"),
        },
        "unicode_analyze": {
            "input_length": len(input_text),
            "codepoints": [f"U+{ord(character):04X}" for character in input_text],
            "normalization": {
                "NFC": input_text,
                "NFD": input_text,
                "NFKC": input_text,
                "NFKD": input_text,
            },
        },
    }
    if operation not in responses:
        raise ValueError(f"unsupported language operation: {operation}")
    return responses[operation]


def _observe_facade_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Emulate daemon-side registered-source fan-out for the facade module."""
    operation = str(payload.get("operation", ""))
    if operation not in {"query_sources", "timeline", "correlate_incident", "topology"}:
        raise ValueError(f"unsupported observe operation: {operation}")
    kinds_value = payload.get("kinds")
    kinds = set(kinds_value) if isinstance(kinds_value, list) else set()
    selected_sources = [source for source in OBSERVE_SOURCES if source["kind"] in kinds]
    _record_request("GET", "/api/observe/sources")
    for _source in selected_sources:
        _record_request("POST", "/api/observe/query")

    start = payload.get("start")
    end = payload.get("end")
    if operation == "correlate_incident" and start is None and end is None:
        seed_value = payload.get("seed")
        if not isinstance(seed_value, dict) or "ts" not in seed_value:
            raise ValueError("correlate_incident requires a timestamped seed")
        seed_timestamp = float(seed_value["ts"])
        window_seconds = float(payload.get("window_s", 300.0))
        start = seed_timestamp - window_seconds
        end = seed_timestamp + window_seconds
    records = [
        dict(record)
        for source in selected_sources
        for record in OBSERVE_RECORDS.get(str(source["name"]), [])
        if (start is None or float(record["ts"]) >= float(start))
        and (end is None or float(record["ts"]) <= float(end))
    ]
    records.sort(key=lambda record: float(record["ts"]))
    errors: list[dict[str, str]] = []
    if any(source["name"] == "broken-events" for source in selected_sources):
        records.append(
            {
                "ts": 35.0,
                "source": "broken-events",
                "kind": "events",
                "level_or_status": "error",
                "message": "query failed",
                "labels": {"trace_id": "incident-42"},
            }
        )
        errors.append({"source": "broken-events", "message": "query failed"})

    result: dict[str, Any] = {
        "operation": operation,
        "role": str(payload.get("role") or "observe"),
        "errors": errors,
    }
    if operation in {"query_sources", "timeline"}:
        result["records"] = records
    elif operation == "correlate_incident":
        seed = payload.get("seed")
        if not isinstance(seed, dict) or not seed:
            raise ValueError("correlate_incident requires a seed")
        result["groups"] = {"incident-42": [dict(seed), *records]}
    else:
        result["topology"] = {
            "services": {"checkout": ["web-01"]},
            "hosts": {"web-01": ["checkout"]},
        }
    return {"result": result}


def _reload_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply a leaf candidate atomically or preserve the live bytes on rollback."""
    candidate = _private_tmp_path(payload.get("candidate_source_path"))
    module_name = str(payload.get("module_name", ""))
    if not module_name or not candidate.is_file():
        raise ValueError("reload requires an existing candidate and module_name")
    relative_module = Path(*module_name.split(".")).with_suffix(".py")
    live_candidates = (
        _private_tmp_path(candidate.parent / relative_module),
        _private_tmp_path(candidate.parent / "src" / relative_module),
    )
    matching_live_files = [path for path in live_candidates if path.is_file()]
    if len(matching_live_files) != 1:
        raise ValueError(
            "reload requires exactly one live module under the candidate root"
        )
    live = matching_live_files[0]
    health_url = str(payload.get("health_url") or "")
    if "degraded" in health_url:
        return {
            "success": False,
            "rolled_back": True,
            "error": "post-reload health gate degraded",
            "details": {"live_path": str(live)},
        }
    _atomic_write(str(live), candidate.read_text(encoding="utf-8"))
    return {
        "success": True,
        "rolled_back": False,
        "error": None,
        "details": {"live_path": str(live)},
    }


def _atomic_write(path: str, content: str) -> None:
    """Publish one private lifecycle record without exposing partial content."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temporary = f"{path}.tmp-{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


# ---------------------------------------------------------------------------
# STS token lifecycle canned responses
# ---------------------------------------------------------------------------
_STS_TOKENS: dict[str, dict[str, Any]] = {}
_STS_TOKEN_LOCK = threading.Lock()


def _agent_id_from_sts_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    # "/admin/sts/tokens/<agent_id>", "/admin/sts/validate/<agent_id>", "/admin/sts/revoke/<agent_id>"
    if len(parts) >= 4 and parts[0] == "admin" and parts[1] == "sts":
        return parts[-1]
    return None


def _mint_token(agent_id: str, parent_agent_id: str = "root") -> dict[str, Any]:
    token_id = f"tok-{agent_id}"
    role_name = f"agent-{agent_id}"
    record: dict[str, Any] = {
        "token_id": token_id,
        "agent_id": agent_id,
        "parent_agent_id": parent_agent_id,
        "role_name": role_name,
        "role_id": f"mock-role-{agent_id}",
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    with _STS_TOKEN_LOCK:
        _STS_TOKENS[agent_id] = record
    return record


def _validate_token(agent_id: str) -> dict[str, Any]:
    with _STS_TOKEN_LOCK:
        rec = _STS_TOKENS.get(agent_id)
    if rec is None:
        return {
            "valid": False,
            "token_id": "",
            "agent_id": None,
            "revoked": False,
            "revoked_at": None,
        }
    is_revoked = rec.get("revoked_at") is not None
    return {
        "valid": not is_revoked,
        "token_id": rec.get("token_id", ""),
        "agent_id": agent_id,
        "revoked": is_revoked,
        "revoked_at": rec.get("revoked_at"),
    }


def _get_token(agent_id: str) -> dict[str, Any] | None:
    with _STS_TOKEN_LOCK:
        record = _STS_TOKENS.get(agent_id)
        return dict(record) if record is not None else None


def _list_tokens() -> list[dict[str, Any]]:
    with _STS_TOKEN_LOCK:
        return [dict(v) for v in _STS_TOKENS.values()]


def _revoke_token(agent_id: str) -> dict[str, Any]:
    with _STS_TOKEN_LOCK:
        rec = _STS_TOKENS.get(agent_id)
        if rec is not None:
            rec["revoked_at"] = datetime.now(UTC).isoformat()
    return {"status": "revoked", "agent_id": agent_id}


def _pid_from_proc_path(path: str) -> int | None:
    """Extract <pid> from /admin/processes/<pid>/(stats|signal). None if unparseable."""
    parts = path.strip("/").split("/")
    # ["admin", "processes", "<pid>", "stats"|"signal"]
    if len(parts) != 4:
        return None
    try:
        return int(parts[2])
    except (TypeError, ValueError):
        return None


class MockDaemonHandler(BaseHTTPRequestHandler):
    # Silence default request logging to stderr noise; route to logfile if set.
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[mock-daemon] " + (fmt % args) + "\n")

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_octet(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _parse_json_body(self, raw: bytes) -> dict[str, Any]:
        try:
            decoded: object = json.loads(raw.decode("utf-8"))
            if isinstance(decoded, dict):
                return {str(key): value for key, value in decoded.items()}
            return {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _read_body(self) -> dict[str, Any]:
        return self._parse_json_body(self._read_raw_body())

    # ---- GET --------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        _record_request("GET", path)
        if path == "/__requests":
            # Per-branch request-log introspection: returns every "METHOD PATH"
            # served since the last reset so verify can assert endpoint hits AND
            # non-hits in isolation.
            self._send_json(200, {"requests": _snapshot_requests()})
        elif path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif path == "/readyz":
            # Healthy readiness gate for gludd_reload's health_url: 200 + not degraded.
            self._send_json(200, {"status": "ok", "degraded": False})
        elif path == "/readyz-degraded":
            # Degraded readiness gate: 200 but degraded=true -> gludd_reload treats it
            # as UNHEALTHY and rolls the hot-swapped module back (fail-closed gate).
            self._send_json(200, {"status": "degraded", "degraded": True})
        elif path == "/ci-status":
            self._send_json(
                200,
                {
                    "status": "completed",
                    "conclusion": "success",
                    "passed": True,
                    "run_id": "mock-run-001",
                    "commit_sha": "abc1234",
                    "failed_job_logs": [],
                },
            )
        elif path == "/api/facts":
            self._send_json(200, dict(FACTS_SNAPSHOT))
        elif path == "/api/metrics":
            self._send_json(200, dict(METRICS_SNAPSHOT))
        elif path == "/api/traces":
            self._send_json(200, dict(TRACES_SNAPSHOT))
        elif path == "/api/observe/sources":
            self._send_json(200, {"sources": list(OBSERVE_SOURCES)})
        elif path == "/api/messages":
            self._send_json(
                200,
                {
                    "messages": [
                        {"id": "MSG-MOCK-IN-1", "sender": "planner", "topic": "standup", "status": "unread"},
                    ]
                },
            )
        elif path.startswith("/api/todos/"):
            todo_id = path.rsplit("/", 1)[-1]
            self._send_json(200, _todo_record(todo_id))
        elif path == "/api/resource-preferences":
            self._send_json(200, {"preference": "mock-profile", "value": "mock-profile"})
        elif path == "/api/features":
            self._send_json(
                200,
                {
                    "features": list(FEATURES_SNAPSHOT),
                    "total": len(FEATURES_SNAPSHOT),
                    "filtered": False,
                },
            )
        elif path == "/api/spend":
            self._send_json(200, dict(SPEND_SNAPSHOT))
        elif path == "/api/accounting":
            self._send_json(200, {"accounting": list(ACCOUNTING_SNAPSHOT), "total": len(ACCOUNTING_SNAPSHOT)})
        elif path.startswith("/api/accounting/"):
            project_id = path[len("/api/accounting/") :]
            snap = next((s for s in ACCOUNTING_SNAPSHOT if s["project_id"] == project_id), None)
            if snap is None:
                self._send_json(404, {"detail": f"Project not found: {project_id}"})
            else:
                self._send_json(200, dict(snap))
        elif path == "/api/dispatch/available":
            self._send_json(200, {"handlers": list(DISPATCH_HANDLERS)})
        elif path == "/api/dispatch/recent":
            self._send_json(200, {"records": list(DISPATCH_RECENT)})
        elif path == "/api/environment":
            self._send_json(200, dict(ENVIRONMENT_SNAPSHOT))
        elif path == "/api/environment/advise":
            qs = parse_qs(urlparse(self.path).query)
            work_type = (qs.get("work_type", [""]) or [""])[0]
            self._send_json(200, _advice_response(work_type))
        elif path == "/admin/processes":
            procs = _managed_processes()
            self._send_json(200, {"processes": procs, "count": len(procs)})
        elif path.startswith("/admin/processes/") and path.endswith("/stats"):
            pid = _pid_from_proc_path(path)
            if pid is None:
                self._send_json(404, {"detail": f"bad process path {path}"})
            else:
                self._send_json(200, _process_stats(pid))
        elif path == "/v1/sys/storage/raft/snapshot":
            # OpenBao break-glass snapshot — serve canned octet-stream bytes.
            token = self.headers.get("X-Vault-Token", "")
            if not token:
                self._send_json(403, {"detail": "missing X-Vault-Token"})
            else:
                self._send_octet(200, OPENBAO_FAKE_SNAPSHOT)
        elif path == "/admin/models/performance":
            self._send_json(200, MODEL_PERFORMANCE_SNAPSHOT)
        elif path == "/admin/models/ranking":
            qs = parse_qs(urlparse(self.path).query)
            task_type = (qs.get("task_type", [""]) or [""])[0]
            self._send_json(200, _ranking_response(task_type))
        elif path == "/admin/ornith/pairs":
            qs = parse_qs(urlparse(self.path).query)
            status_csv = (qs.get("status", [""]) or [""])[0]
            limit = int((qs.get("limit", ["10"]) or ["10"])[0])
            self._send_json(200, _ornith_pairs_response(status_csv, limit))
        elif path == "/process-audit":
            self._send_json(
                200,
                {
                    "guardrail_health": {
                        "state_based_checks": 5,
                        "pattern_list_checks": 35,
                        "health_score": 0.125,
                        "overfitted": True,
                    },
                    "plugin_footprint": {
                        "total_lines": 4200,
                        "files": 8,
                    },
                    "recent_false_positive_blocks": 3,
                },
            )
        # ---- GitHub API mock routes (gha_usage role) ------------------------
        elif path == "/repos/mock-org/mock-repo/actions/runs":
            now = datetime.now(UTC)
            self._send_json(
                200,
                {
                    "total_count": 5,
                    "workflow_runs": [
                        {
                            "id": 1,
                            "name": "gate",
                            "conclusion": "success",
                            "status": "completed",
                            "created_at": now.isoformat(),
                        },
                        {
                            "id": 2,
                            "name": "pytest",
                            "conclusion": "success",
                            "status": "completed",
                            "created_at": now.isoformat(),
                        },
                        {
                            "id": 3,
                            "name": "lint",
                            "conclusion": "success",
                            "status": "completed",
                            "created_at": (now - timedelta(hours=2)).isoformat(),
                        },
                        {
                            "id": 4,
                            "name": "typecheck",
                            "conclusion": "failure",
                            "status": "completed",
                            "created_at": (now - timedelta(hours=6)).isoformat(),
                        },
                        {
                            "id": 5,
                            "name": "deploy",
                            "conclusion": "success",
                            "status": "completed",
                            "created_at": (now - timedelta(hours=22)).isoformat(),
                        },
                    ],
                },
            )
        elif path == "/repos/mock-org/mock-repo/actions/workflows":
            self._send_json(
                200,
                {
                    "total_count": 3,
                    "workflows": [
                        {"id": 1, "name": "gate", "path": ".github/workflows/gate.yml", "state": "active"},
                        {"id": 2, "name": "build", "path": ".github/workflows/build.yml", "state": "active"},
                        {"id": 3, "name": "release", "path": ".github/workflows/release.yml", "state": "active"},
                    ],
                },
            )
        elif path == "/orgs/mock-org/settings/billing/actions":
            self._send_json(
                200,
                {
                    "total_minutes_used": 320,
                    "total_paid_minutes_used": 0,
                    "included_minutes": 2000,
                    "usable_minutes": 1680,
                    "pending_cancellation_minutes": 0,
                },
            )
        # ---- STS token lifecycle ------------------------------------------
        elif path == "/admin/sts/tokens":
            self._send_json(200, {"tokens": _list_tokens()})
        elif path.startswith("/admin/sts/tokens/"):
            agent_id = _agent_id_from_sts_path(path)
            rec = _get_token(agent_id) if agent_id else None
            if rec is None:
                self._send_json(404, {"detail": f"no token for agent {agent_id}"})
            else:
                self._send_json(200, rec)
        elif path.startswith("/admin/sts/validate/"):
            agent_id = _agent_id_from_sts_path(path)
            if agent_id is None:
                self._send_json(404, {"detail": f"bad validate path {path}"})
            else:
                self._send_json(200, _validate_token(agent_id))
        else:
            self._send_json(404, {"detail": f"no mock route for GET {path}"})

    # ---- POST -------------------------------------------------------------
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        _record_request("POST", path)
        raw_body = self._read_raw_body()
        payload = self._parse_json_body(raw_body)
        if path == "/__requests/reset":
            # Clear the in-memory request log so the next role invocation's
            # endpoint hits can be snapshotted in isolation.
            _reset_requests()
            self._send_json(200, {"reset": True})
        elif path == "/admin/models/call":
            self._send_json(200, _model_call_response(payload))
        elif path == "/admin/models/workflow":
            self._send_json(200, _workflow_response(payload))
        elif path == "/api/messages":
            self._send_json(201, _message_created(payload))
        elif path.startswith("/api/messages/") and path.endswith("/ack"):
            self._send_json(200, {"acked": True})
        elif path == "/api/features/verify":
            self._send_json(200, dict(VERIFY_SUMMARY))
        elif path == "/api/spend/configure":
            resp = dict(SPEND_CONFIGURE_RESPONSE)
            if "limit_usd" in payload:
                resp["limit_usd"] = payload["limit_usd"]
            if "window_seconds" in payload:
                resp["window_seconds"] = payload["window_seconds"]
            self._send_json(200, resp)
        elif path == "/api/schedule":
            self._send_json(200, _schedule_response(payload))
        elif path == "/api/dispatch":
            self._send_json(200, _dispatch_response(payload))
        elif path == "/api/observe/facade":
            try:
                self._send_json(200, _observe_facade_response(payload))
            except ValueError as exc:
                self._send_json(422, {"detail": str(exc)})
        elif path == "/api/observe/query":
            source = payload.get("source")
            if source == "broken-events":
                self._send_json(503, {"detail": "mock connector unavailable"})
            elif source not in OBSERVE_RECORDS:
                self._send_json(404, {"detail": "unknown registered source"})
            else:
                self._send_json(200, {"records": list(OBSERVE_RECORDS[source])})
        elif path == "/admin/stream/dispatch":
            self._send_json(200, _stream_dispatch_response(payload))
        elif path == "/api/human-todos":
            self._send_json(201, _human_todo_created(payload))
        elif path.startswith("/admin/processes/") and path.endswith("/signal"):
            pid = _pid_from_proc_path(path)
            if pid is None:
                self._send_json(404, {"detail": f"bad process path {path}"})
            else:
                self._send_json(200, _signal_response(pid, payload))
        elif path == "/admin/abtest/run":
            self._send_json(200, _abtest_response(payload))
        elif path == "/admin/git/operation":
            try:
                self._send_json(200, _git_operation_response(payload))
            except (OSError, ValueError) as exc:
                self._send_json(422, {"detail": str(exc)})
        elif path == "/admin/make":
            self._send_json(200, _make_response(payload))
        elif path == "/admin/skills/render":
            self._send_json(200, _skill_response(payload))
        elif path == "/api/language/execute":
            try:
                self._send_json(200, {"result": _language_operation_response(payload)})
            except (TypeError, ValueError) as exc:
                self._send_json(422, {"detail": str(exc)})
        elif path == "/admin/reload/code":
            try:
                self._send_json(200, _reload_response(payload))
            except (OSError, ValueError) as exc:
                self._send_json(422, {"detail": str(exc)})
        elif path == "/v1/sys/storage/raft/restore":
            # OpenBao break-glass restore — accept the raw bytes, return 204.
            token = self.headers.get("X-Vault-Token", "")
            if not token:
                self._send_json(403, {"detail": "missing X-Vault-Token"})
                return
            raw = raw_body
            global OPENBAO_RESTORE_LAST_PAYLOAD
            OPENBAO_RESTORE_LAST_PAYLOAD = {
                "size_bytes": len(raw),
                "head_hex": raw[:32].hex(),
            }
            # OpenBao returns 204 No Content for a successful restore.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        # ---- STS token lifecycle ------------------------------------------
        elif path == "/admin/sts/mint":
            agent_id = payload.get("agent_id", "unknown")
            parent_agent_id = payload.get("parent_agent_id", "root")
            self._send_json(201, _mint_token(agent_id, parent_agent_id=parent_agent_id))
        elif path.startswith("/admin/sts/revoke/"):
            agent_id = _agent_id_from_sts_path(path)
            if agent_id is None:
                self._send_json(404, {"detail": f"bad revoke path {path}"})
            else:
                self._send_json(200, _revoke_token(agent_id))
        else:
            self._send_json(404, {"detail": f"no mock route for POST {path}"})

    # ---- PATCH ------------------------------------------------------------
    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        _record_request("PATCH", path)
        payload = self._read_body()
        if path.startswith("/api/todos/"):
            todo_id = path.rsplit("/", 1)[-1]
            self._send_json(200, {"id": todo_id, "status": payload.get("status", "unknown")})
        else:
            self._send_json(404, {"detail": f"no mock route for PATCH {path}"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock general_ludd daemon for molecule")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--pidfile", default="")
    parser.add_argument("--ready-file", default="")
    parser.add_argument("--instance-id", default="")
    parser.add_argument(
        "--lease-seconds",
        type=float,
        default=0.0,
        help="Self-terminate after this bounded lifetime; zero disables the lease.",
    )
    parser.add_argument(
        "--managed-pid",
        type=int,
        default=0,
        help=(
            "When > 0, the first /admin/processes record reports this pid/pgid "
            "instead of the canned placeholder, so the listing references a real "
            "process spawned by the calling scenario (test_gludd_process)."
        ),
    )
    args = parser.parse_args()

    if args.managed_pid and args.managed_pid > 0:
        global _MANAGED_PID_OVERRIDE
        _MANAGED_PID_OVERRIDE = args.managed_pid

    server = ThreadingHTTPServer((args.host, args.port), MockDaemonHandler)
    bound_host = str(server.server_address[0])
    bound_port = int(server.server_address[1])
    sys.stderr.write(f"[mock-daemon] listening on {bound_host}:{bound_port}\n")

    shutdown_event = threading.Event()

    def _on_sigterm(signum: int, frame: object) -> None:
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_sigterm)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    if args.pidfile:
        _atomic_write(args.pidfile, str(os.getpid()))
    if args.ready_file:
        _atomic_write(
            args.ready_file,
            json.dumps(
                {
                    "base_url": f"http://{bound_host}:{bound_port}",
                    "host": bound_host,
                    "instance_id": args.instance_id,
                    "pid": os.getpid(),
                    "port": bound_port,
                },
                sort_keys=True,
            ),
        )
    lease_timer: threading.Timer | None = None
    if args.lease_seconds > 0:
        lease_timer = threading.Timer(args.lease_seconds, shutdown_event.set)
        lease_timer.daemon = True
        lease_timer.start()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            shutdown_event.wait()
    finally:
        if lease_timer is not None:
            lease_timer.cancel()
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()
        for lifecycle_file in (args.ready_file, args.pidfile):
            if lifecycle_file:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(lifecycle_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
