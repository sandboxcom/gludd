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
  GET  /api/messages                  -> 200 {"messages":[...]}             (gludd_message receive)
  POST /api/messages                  -> 201 created message                (gludd_message send)
  POST /api/messages/<id>/ack         -> 200 {"acked":true}                 (gludd_message ack)
  POST /admin/models/call             -> 200 {"text":..,"usage":..}         (gludd_model_call / gludd_agent_run HTTP)
  GET  /api/todos/<id>                 -> 200 todo record                    (gludd_db todo_get)
  PATCH /api/todos/<id>               -> 200 {"status":..}                  (gludd_db todo_update_status)
  GET  /api/resource-preferences      -> 200 {"preference":..}              (gludd_db resource_preference)

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
        elif path == "/api/messages":
            self._send_json(200, {"messages": [
                {"id": "MSG-MOCK-IN-1", "sender": "planner", "topic": "standup", "status": "unread"},
            ]})
        elif path.startswith("/api/todos/"):
            todo_id = path.rsplit("/", 1)[-1]
            self._send_json(200, _todo_record(todo_id))
        elif path == "/api/resource-preferences":
            self._send_json(200, {"preference": "mock-profile", "value": "mock-profile"})
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
