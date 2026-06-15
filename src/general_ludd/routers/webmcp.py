"""GET /api/webmcp — machine-readable self-description for context-free consumers.

This endpoint is intentionally PUBLIC (no PSK required) so that a fresh
consumer can read it first and learn how to authenticate before making any
other call.

A context-free consumer learns from this response:
  - auth.header / auth.scheme / auth.env_var — how to obtain and send the PSK
  - auth.public_paths — which paths need NO token (the rest need Bearer auth)
  - endpoints — the full endpoint inventory with method, path, purpose, and
    for POST endpoints, the request_body field schema
  - facts_facets — the keys returned by GET /api/facts so the consumer knows
    what the aggregation snapshot contains

Only endpoints that genuinely exist are listed here (honest documentation).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

# Paths that the daemon allows without a PSK token.
# Must match the _PUBLIC_PATHS set in daemon.py + /api/webmcp itself.
_PUBLIC_PATHS = [
    "/healthz",
    "/readyz",
    "/api/status",
    "/api/todos",
    "/api/webmcp",
    "/docs",
    "/openapi.json",
    "/redoc",
]

# The /api/facts response always contains these top-level keys.
# Update this list whenever a new facet is added to facts.py.
_FACTS_FACETS = [
    "work",
    "todos",
    "models",
    "history",
    "messages",
    "metrics",
    "traces",
    "codebase",
    "features",
    "dispatch",
    "spend",
    "accounting",
    "schedule",
    "coordination",
    "project_id",
]

# Endpoint inventory — only what truly exists on the daemon.
_ENDPOINTS: list[dict[str, Any]] = [
    # ── Probe / status ──────────────────────────────────────────────────────
    {
        "method": "GET",
        "path": "/healthz",
        "purpose": "Liveness probe — always 200 unless catastrophically unhealthy.",
        "auth_required": False,
    },
    {
        "method": "GET",
        "path": "/readyz",
        "purpose": "Readiness probe — 503 when degraded or the event-loop has stopped.",
        "auth_required": False,
    },
    {
        "method": "GET",
        "path": "/api/status",
        "purpose": (
            "Daemon status snapshot: version, uptime ticks, todo counts, "
            "queue depths, config paths, filestore info, quality-gate result."
        ),
        "auth_required": False,
    },
    {
        "method": "GET",
        "path": "/api/webmcp",
        "purpose": "Machine-readable self-description (this response). Always public.",
        "auth_required": False,
    },
    # ── Facts / observability ────────────────────────────────────────────────
    {
        "method": "GET",
        "path": "/api/facts",
        "purpose": (
            "Full live daemon state snapshot for playbook logic: "
            "work, todos, models, history, messages, metrics, traces, "
            "codebase, features, dispatch, spend, accounting, schedule. "
            "Optional query param: project_id (filter by project)."
        ),
        "auth_required": True,
        "query_params": {"project_id": "string | null — filter all facets to one project"},
        "response_shape": {
            "work": "object — in-flight/claimed task counts by status",
            "todos": "object — queue counts, oldest age, backlog size",
            "models": "object — model routing config + per-model usage/health",
            "history": "object — task return success/failure rates",
            "messages": "object — unread message counts per recipient",
            "metrics": "object — agent counts, cost by project, benchmark rankings",
            "traces": "object — recent execution traces + by-phase aggregates",
            "codebase": "object — codebase self-knowledge (churn, complexity, debt)",
            "features": "object — feature counts by status",
            "dispatch": "object — recent dispatch events + registered handler kinds",
            "spend": "object — rolling-window spend (limiter_active, window_spend_usd, limit_usd, remaining_usd)",
            "accounting": "object — per-project accounting snapshots (time, cost, quota, tokens, LoC, role stats)",
            "schedule": "object — last computed concurrency-safe batch plan (last_plan, batch_count, item_count)",
            "project_id": "string | null — the project_id filter used",
        },
    },
    {
        "method": "GET",
        "path": "/api/metrics",
        "purpose": (
            "Focused metrics snapshot (agent-level report, global model usage, "
            "per-project cost, benchmark rankings). "
            "Optional query params: project_id, agent_id."
        ),
        "auth_required": True,
        "query_params": {
            "project_id": "string | null",
            "agent_id": "string | null",
        },
    },
    {
        "method": "GET",
        "path": "/api/traces",
        "purpose": (
            "Focused execution-trace snapshot (recent traces, by-phase aggregates, "
            "OTel exporter status). "
            "Optional query params: todo_id, limit (default 20, max 100)."
        ),
        "auth_required": True,
        "query_params": {
            "todo_id": "string | null — filter to one task",
            "limit": "int — max traces to return (1-100, default 20)",
        },
    },
    # ── Todos ────────────────────────────────────────────────────────────────
    {
        "method": "GET",
        "path": "/api/todos",
        "purpose": "List tasks/todos, optionally filtered by queue, status, project_id.",
        "auth_required": False,
        "query_params": {
            "queue": "string | null",
            "status": "string | null — queued | in_progress | completed | reviewed | reconciled",
            "project_id": "string | null",
        },
        "response_shape": "array of todo objects",
    },
    {
        "method": "POST",
        "path": "/api/todos",
        "purpose": "Create a new task/todo. Returns the created todo object (HTTP 201).",
        "auth_required": False,
        "request_body": {
            "title": "string (required, 1-512 chars) — short task summary",
            "description": "string (optional, max 4096 chars) — detailed task description",
            "queue": "string (optional, default 'core') — target queue name",
            "priority": "string (optional, default 'medium') — low | medium | high | critical",
            "work_type": "string (optional, default 'code') — type of work",
            "project_id": "string | null (optional) — project scope",
        },
        "response_shape": {
            "todo_id": "string — auto-generated, format TODO-XXXXXXXX",
            "title": "string",
            "description": "string",
            "queue": "string",
            "priority": "string",
            "work_type": "string",
            "status": "string — initially 'queued'",
            "project_id": "string | null",
            "version": "int",
            "created_at": "string | null — ISO datetime",
        },
    },
    {
        "method": "GET",
        "path": "/api/todos/{todo_id}",
        "purpose": "Get a single todo by its TODO-XXXXXXXX id. 404 if not found.",
        "auth_required": False,
        "response_shape": "todo object (same shape as POST /api/todos response)",
    },
    # ── Messages ─────────────────────────────────────────────────────────────
    {
        "method": "POST",
        "path": "/api/messages",
        "purpose": (
            "Send an inter-agent message. Returns the created message object (HTTP 201). "
            "Use recipient='broadcast' to send to all agents."
        ),
        "auth_required": True,
        "request_body": {
            "sender": "string (required, 1-128 chars) — sending agent identifier",
            "recipient": "string (required, 1-128 chars) — receiving agent, or 'broadcast'",
            "topic": "string (optional, max 256 chars) — message topic/subject",
            "body": "string (optional, max 65536 chars) — message body",
            "priority": "string (optional, default 'normal') — low | normal | high | urgent",
            "ttl_seconds": "int | null (optional, ≥1) — message expiry in seconds",
            "project_id": "string | null (optional) — project scope",
        },
        "response_shape": {
            "id": "string — auto-generated message id",
            "sender": "string",
            "recipient": "string",
            "topic": "string",
            "body": "string",
            "priority": "string",
            "project_id": "string | null",
            "created_at": "string | null — ISO datetime",
            "read_at": "string | null — ISO datetime, null until acked",
            "ttl_seconds": "int | null",
        },
    },
    {
        "method": "GET",
        "path": "/api/messages",
        "purpose": (
            "Inbox: list messages for a recipient (includes broadcast). "
            "Required query param: recipient."
        ),
        "auth_required": True,
        "query_params": {
            "recipient": "string (required) — agent name to fetch inbox for",
            "unread": "bool (default true) — only return unread messages",
            "include_broadcast": "bool (default true) — include 'broadcast' messages",
            "project_id": "string | null",
        },
        "response_shape": {
            "messages": "array of message objects",
            "count": "int",
            "recipient": "string",
        },
    },
    {
        "method": "POST",
        "path": "/api/messages/{message_id}/ack",
        "purpose": "Mark a message as read (acknowledge). 404 if not found.",
        "auth_required": True,
        "response_shape": {
            "acked": "bool — always true on success",
            "id": "string",
            "read_at": "string — ISO datetime when acked",
        },
    },
    # ── Dispatch ─────────────────────────────────────────────────────────────
    {
        "method": "POST",
        "path": "/api/dispatch",
        "purpose": (
            "Dispatch one or more tool-call requests to role/mcp/skill/collection "
            "handlers. Fail-closed on unknown kinds."
        ),
        "auth_required": True,
        "request_body": {
            "kind": "string — role | mcp | skill | collection (single-call form)",
            "name": "string — handler name (single-call form)",
            "args": "object (optional) — handler arguments (single-call form)",
            "tool_calls": (
                "array (optional) — list of {kind, name, args} objects "
                "(multi-call form; mutually exclusive with single-call fields)"
            ),
        },
        "response_shape": {
            "results": "array of DispatchResult objects",
            "count": "int",
            "ok_count": "int",
            "error_count": "int",
        },
    },
    {
        "method": "GET",
        "path": "/api/dispatch/recent",
        "purpose": "Return the most recent dispatch events (bounded ring-buffer).",
        "auth_required": True,
        "query_params": {"limit": "int (default 20, max 50)"},
    },
    {
        "method": "GET",
        "path": "/api/dispatch/available",
        "purpose": "Return which handler kinds are registered on this daemon instance.",
        "auth_required": True,
    },
    # ── Features ─────────────────────────────────────────────────────────────
    {
        "method": "GET",
        "path": "/api/features",
        "purpose": (
            "List feature-database entries. "
            "Optional filters: status (requested|implemented|verified|regressed), category."
        ),
        "auth_required": True,
        "query_params": {
            "status": "string | null",
            "category": "string | null",
        },
    },
    {
        "method": "GET",
        "path": "/api/features/{feature_id}",
        "purpose": "Get a single feature by its FEAT-... id. 404 if not found.",
        "auth_required": True,
    },
    {
        "method": "POST",
        "path": "/api/features/verify",
        "purpose": "Trigger FeatureVerifier.verify_all — evidence-gated; empty evidence never reaches verified.",
        "auth_required": True,
        "query_params": {"project_id": "string | null"},
    },
    # ── Deployments ──────────────────────────────────────────────────────────
    {
        "method": "GET",
        "path": "/api/deployments",
        "purpose": "List active compute deployments (instance_id + status).",
        "auth_required": True,
    },
    # ── Spend limiter ────────────────────────────────────────────────────────
    {
        "method": "GET",
        "path": "/api/spend",
        "purpose": (
            "Current rolling-window spend summary: "
            "limiter_active, window_spend_usd, limit_usd, remaining_usd, window_seconds."
        ),
        "auth_required": True,
    },
    {
        "method": "POST",
        "path": "/api/spend/configure",
        "purpose": "Update the spend limiter's limit_usd and window_seconds at runtime.",
        "auth_required": True,
        "request_body": {
            "limit_usd": "float (required, >0) — spend cap in USD for the rolling window",
            "window_seconds": "float (required, >0) — rolling window width in seconds",
        },
    },
    # ── Accounting ───────────────────────────────────────────────────────────
    {
        "method": "GET",
        "path": "/api/accounting",
        "purpose": (
            "Per-project accounting snapshots for all known projects: "
            "time_elapsed_s, usd_spent, quota_pct, tokens_used, loc_changed, role_run_counts, todos."
        ),
        "auth_required": True,
    },
    {
        "method": "GET",
        "path": "/api/accounting/{project_id}",
        "purpose": "Accounting snapshot for a single project. 404 if project unknown.",
        "auth_required": True,
    },
    # ── Concurrency scheduler ────────────────────────────────────────────────
    {
        "method": "POST",
        "path": "/api/schedule",
        "purpose": (
            "Compute concurrency-safe ordered batches for a set of work items. "
            "Items within a batch may run concurrently; later batches depend on earlier ones. "
            "Returns 409 on dependency cycle."
        ),
        "auth_required": True,
        "request_body": {
            "items": (
                "array (required) — list of work-item objects, each with: "
                "id (string, required), resources (list[string], optional), "
                "depends_on (list[string], optional), is_greenfield (bool, optional)"
            ),
        },
        "response_shape": {
            "batches": "array of arrays — ordered batches of item ids",
        },
    },
]


_SELF_DESCRIPTION: dict[str, Any] = {
    "name": "general-ludd-agent",
    "description": (
        "Autonomous agentic SDLC daemon.  Exposes a REST API for task management, "
        "inter-agent messaging, observability, and dynamic dispatch.  "
        "Runs an internal event loop that claims, dispatches, reviews, and reconciles "
        "tasks through an Ansible collection."
    ),
    "auth": {
        "type": "PSK",
        "header": "Authorization",
        "scheme": "Bearer",
        "format": "Authorization: Bearer <token>",
        "env_var": "GLUDD_PSK",
        "description": (
            "Set GLUDD_PSK in the daemon environment.  "
            "Pass the same value as 'Authorization: Bearer <GLUDD_PSK>' on every "
            "request to a protected path.  "
            "When GLUDD_PSK is unset the daemon runs with auth disabled (dev mode)."
        ),
        "public_paths": _PUBLIC_PATHS,
        "note": (
            "All /api/* and /admin/* paths NOT listed in public_paths require "
            "the Bearer token.  The paths in public_paths are reachable without "
            "any authentication header."
        ),
    },
    "facts_facets": _FACTS_FACETS,
    "endpoints": _ENDPOINTS,
}


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    """Register GET /api/webmcp — no PSK required (deliberately public)."""

    @app.get("/api/webmcp")
    async def api_webmcp() -> dict[str, Any]:
        """Machine-readable self-description for context-free consumers.

        Returns the endpoint inventory, auth instructions, and /api/facts
        facet names so a caller with NO prior knowledge can bootstrap
        themselves without reading source code or external documentation.
        """
        return _SELF_DESCRIPTION
