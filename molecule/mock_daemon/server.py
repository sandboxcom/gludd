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
  POST /api/dispatch                  -> 200 {"result":{...}}                (gludd_dispatch dispatch)
  GET  /api/dispatch/available        -> 200 {"handlers":[...]}              (gludd_dispatch available)
  GET  /api/dispatch/recent           -> 200 {"records":[...]}               (gludd_dispatch recent)

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


def _dispatch_response(payload: dict) -> dict:
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
        elif path == "/api/dispatch/available":
            self._send_json(200, {"handlers": list(DISPATCH_HANDLERS)})
        elif path == "/api/dispatch/recent":
            self._send_json(200, {"records": list(DISPATCH_RECENT)})
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
        elif path == "/api/dispatch":
            self._send_json(200, _dispatch_response(payload))
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
