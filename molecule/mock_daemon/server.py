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
  GET  /api/messages                  -> 200 {"messages":[...]}             (gludd_message receive)
  POST /api/messages                  -> 201 created message                (gludd_message send)
  POST /api/messages/<id>/ack         -> 200 {"acked":true}                 (gludd_message ack)
  POST /admin/models/call             -> 200 {"text":..,"usage":..}         (gludd_model_call / gludd_agent_run HTTP)
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

Usage:
    python3 server.py --port 8765 --pidfile /tmp/x.pid --logfile /tmp/x.log

Run in the background from a scenario's prepare.yml and stop it in verify.yml
(or cleanup) by killing the recorded pid. Binds 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


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
                {"span_id": "span-a", "phase": "plan", "status": "success",
                 "output_tokens": 40, "cost_usd": 0.0005},
                {"span_id": "span-b", "phase": "generate", "status": "success",
                 "output_tokens": 150, "cost_usd": 0.0021},
            ],
        },
    ],
    "by_phase": {
        "plan": {"span_count": 1, "total_cost_usd": 0.0005, "total_tokens": 40, "success_count": 1},
        "generate": {"span_count": 1, "total_cost_usd": 0.0021, "total_tokens": 150, "success_count": 1},
    },
    "otel_exporter_status": "disabled",
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


FEATURES_SNAPSHOT = [
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


def _schedule_response(payload: dict) -> dict:
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
    by_id: dict[str, dict] = {it["id"]: it for it in items}

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


def _model_call_response(payload: dict) -> dict:
    return {
        "text": "[mock-daemon] applied the requested change.",
        "model_profile_id": payload.get("model_profile") or "mock-profile",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _message_created(payload: dict) -> dict:
    return {
        "id": "MSG-MOCK-0001",
        "sender": payload.get("sender"),
        "recipient": payload.get("recipient"),
        "topic": payload.get("topic", ""),
        "priority": payload.get("priority", "normal"),
        "status": "unread",
    }


def _todo_record(todo_id: str) -> dict:
    return {
        "id": todo_id,
        "title": "mock todo",
        "description": "fetched from mock daemon",
        "status": "backlog",
        "queue": "core",
        "work_type": "code",
    }


class MockDaemonHandler(BaseHTTPRequestHandler):
    # Silence default request logging to stderr noise; route to logfile if set.
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        sys.stderr.write("[mock-daemon] " + (fmt % args) + "\n")

    def _send_json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # ---- GET --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif path == "/api/facts":
            self._send_json(200, dict(FACTS_SNAPSHOT))
        elif path == "/api/metrics":
            self._send_json(200, dict(METRICS_SNAPSHOT))
        elif path == "/api/traces":
            self._send_json(200, dict(TRACES_SNAPSHOT))
        elif path == "/api/messages":
            self._send_json(200, {"messages": [
                {"id": "MSG-MOCK-IN-1", "sender": "planner", "topic": "standup", "status": "unread"},
            ]})
        elif path.startswith("/api/todos/"):
            todo_id = path.rsplit("/", 1)[-1]
            self._send_json(200, _todo_record(todo_id))
        elif path == "/api/resource-preferences":
            self._send_json(200, {"preference": "mock-profile", "value": "mock-profile"})
        elif path == "/api/features":
            self._send_json(200, {"features": list(FEATURES_SNAPSHOT), "total": len(FEATURES_SNAPSHOT), "filtered": False})
        elif path == "/api/spend":
            self._send_json(200, dict(SPEND_SNAPSHOT))
        elif path == "/api/accounting":
            self._send_json(200, {"accounting": list(ACCOUNTING_SNAPSHOT), "total": len(ACCOUNTING_SNAPSHOT)})
        elif path.startswith("/api/accounting/"):
            project_id = path[len("/api/accounting/"):]
            snap = next((s for s in ACCOUNTING_SNAPSHOT if s["project_id"] == project_id), None)
            if snap is None:
                self._send_json(404, {"detail": f"Project not found: {project_id}"})
            else:
                self._send_json(200, dict(snap))
        else:
            self._send_json(404, {"detail": f"no mock route for GET {path}"})

    # ---- POST -------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_body()
        if path == "/admin/models/call":
            self._send_json(200, _model_call_response(payload))
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
        else:
            self._send_json(404, {"detail": f"no mock route for POST {path}"})

    # ---- PATCH ------------------------------------------------------------
    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
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
    args = parser.parse_args()

    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    server = ThreadingHTTPServer((args.host, args.port), MockDaemonHandler)
    sys.stderr.write(f"[mock-daemon] listening on {args.host}:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
