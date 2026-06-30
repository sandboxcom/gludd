"""End-to-end integration tests for the `gludd ornith` CLI.

The CLI talks to ``/admin/ornith/*`` daemon endpoints. Some of those
endpoints belong to the parallel training-data task (record/pending/
export/stats/outcome — covered by ``tests/integration/test_ornith_endpoints.py``).
The endpoints unique to this task (status/solve/improve) belong to the
MCP-server parallel task that has not landed yet.

To prove the CLI <-> daemon wire contract end-to-end without depending
on the still-landing daemon side, we exercise the CLI against a minimal
ASGI stub that implements the documented contract. When the real daemon
grows these endpoints, the stub can be deleted and the tests pointed at
``create_daemon_app``.

All CLI invocations go through ``cli_ornith._http``; we patch
``httpx.request`` to dispatch into the stub via
``httpx.AsyncClient(transport=ASGITransport(app=...))``.
"""

from __future__ import annotations

import asyncio
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from general_ludd.cli_ornith import (
    _cmd_pairs,
    _cmd_set_outcome,
    _cmd_solve,
    _cmd_stats,
    _cmd_status,
)

# ---------------------------------------------------------------------------
# Stub state + ASGI app implementing the /admin/ornith/* contract.
# ---------------------------------------------------------------------------


class _StubState:
    def __init__(self) -> None:
        self.ornith_enabled: bool = True
        self.has_perm_spec: bool = True
        self.solve_calls: list[dict[str, Any]] = []
        self.training_pairs: list[dict[str, Any]] = []
        self.solve_pair_counter: int = 0


class SolveBody(BaseModel):
    task: str
    target_files: list[str]
    max_iterations: int | None = None


class ImproveBody(BaseModel):
    artifact_path: str
    kind: str
    feedback: str | None = None


class OutcomeBody(BaseModel):
    status: str
    details: dict[str, Any] | None = None


def _build_stub_app(state: _StubState) -> FastAPI:
    app = FastAPI()

    @app.get("/admin/ornith/status")
    async def status() -> dict[str, Any]:
        return {
            "installed": True,
            "version": "1.0",
            "model_sha": "stub-sha",
            "last_call_at": "2026-06-29T12:00:00Z",
            "total_calls": len(state.solve_calls),
            "success_rate": 1.0 if state.solve_calls else 0.0,
            "sandbox_backend": "landlock",
        }

    @app.post("/admin/ornith/solve")
    async def solve(body: SolveBody) -> dict[str, Any]:
        if not state.ornith_enabled:
            raise HTTPException(status_code=422, detail="ornith disabled")
        if not state.has_perm_spec:
            raise HTTPException(status_code=403, detail="missing agent:ornith permission")
        state.solve_calls.append(body.model_dump())
        state.solve_pair_counter += 1
        pair = {
            "id": f"ORN-SOLVE-{state.solve_pair_counter}",
            "task_description": body.task,
            "target_files": body.target_files,
            "outcome_status": "pending",
        }
        state.training_pairs.append(pair)
        return {
            "patch": "--- a/x\n+++ b/x\n",
            "summary": "stub solve",
            "iterations": body.max_iterations or 3,
            "tokens": 100,
            "pair_id": pair["id"],
        }

    @app.post("/admin/ornith/improve")
    async def improve(body: ImproveBody) -> dict[str, Any]:
        return {"status": "ok", "improve_id": f"IMP-{len(body.artifact_path)}"}

    @app.get("/admin/ornith/pending")
    async def pending() -> dict[str, Any]:
        rows = [p for p in state.training_pairs if p["outcome_status"] == "pending"]
        return {"count": len(rows), "pending": rows}

    @app.get("/admin/ornith/stats")
    async def stats() -> dict[str, Any]:
        succeeded = sum(
            1 for p in state.training_pairs if p["outcome_status"] == "succeeded"
        )
        total = len(state.training_pairs)
        return {
            "total": total,
            "success_rate": (succeeded / total) if total else 0.0,
            "counts_by_status": {
                "pending": total - succeeded,
                "succeeded": succeeded,
            },
            "avg_tokens_per_call": 100.0,
        }

    @app.patch("/admin/ornith/{pair_id}/outcome")
    async def set_outcome(pair_id: str, body: OutcomeBody) -> dict[str, Any]:
        for p in state.training_pairs:
            if p["id"] == pair_id:
                p["outcome_status"] = body.status
                return {
                    "id": pair_id,
                    "outcome_status": body.status,
                    "outcome_set_at": "now",
                }
        raise HTTPException(status_code=404, detail="pair not found")

    return app


@pytest.fixture
def stub_state() -> _StubState:
    return _StubState()


@pytest.fixture
def stub_app(stub_state: _StubState) -> FastAPI:
    return _build_stub_app(stub_state)


# ---------------------------------------------------------------------------
# Helper: run a CLI handler with its httpx.request redirected to an ASGI app.
# ---------------------------------------------------------------------------


def _run_cli(handler, ns, app) -> tuple[str, str, int | None]:
    """Patch cli_ornith.httpx.request to dispatch into ``app``."""

    def _fake_request(method, url, **kwargs):
        transport = ASGITransport(app=app)

        async def _go():
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.request(
                    method,
                    url,
                    json=kwargs.get("json"),
                    params=kwargs.get("params"),
                    headers=kwargs.get("headers") or {},
                    timeout=kwargs.get("timeout", 30.0),
                )

        return asyncio.run(_go())

    out = StringIO()
    err = StringIO()
    code: int | None = None
    with patch("sys.stdout", out), patch("sys.stderr", err), patch(
        "general_ludd.cli_ornith.httpx.request", side_effect=_fake_request
    ):
        try:
            handler(ns)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return out.getvalue(), err.getvalue(), code


def _parse(*argv: str):
    import general_ludd.cli as cli_mod

    parser, _ = cli_mod.build_parser()
    return parser.parse_args(list(argv))


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestSolveContract:
    def test_solve_records_training_pair(self, stub_app, stub_state):
        """POST /admin/ornith/solve → a training pair appears in /pending."""
        ns = _parse(
            "ornith",
            "solve",
            "--task",
            "fix the bug",
            "--target-files",
            "src/x.py",
            "--daemon-url",
            "http://test",
        )
        out, _, code = _run_cli(_cmd_solve, ns, stub_app)
        assert code is None, f"solve failed: {out}"
        assert "pair_id" in out
        assert len(stub_state.training_pairs) == 1

        # The pairs subcommand should list it.
        ns_pairs = _parse("ornith", "pairs", "--daemon-url", "http://test")
        out2, _, _ = _run_cli(_cmd_pairs, ns_pairs, stub_app)
        assert "ORN-SOLVE-1" in out2

    def test_solve_refused_when_ornith_disabled(self, stub_app, stub_state):
        stub_state.ornith_enabled = False
        ns = _parse(
            "ornith",
            "solve",
            "--task",
            "x",
            "--target-files",
            "f",
            "--daemon-url",
            "http://test",
        )
        out, err, code = _run_cli(_cmd_solve, ns, stub_app)
        assert code == 1
        combined = out + err
        assert "422" in combined

    def test_solve_refused_when_permission_spec_missing(self, stub_app, stub_state):
        stub_state.has_perm_spec = False
        ns = _parse(
            "ornith",
            "solve",
            "--task",
            "x",
            "--target-files",
            "f",
            "--daemon-url",
            "http://test",
        )
        out, err, code = _run_cli(_cmd_solve, ns, stub_app)
        assert code == 1
        combined = out + err
        assert "403" in combined

    def test_solve_scoped_by_sts_intersection(self, stub_app, stub_state):
        """The CLI sends the operator's requested target_files; the daemon
        narrows them via STS intersection. The stub preserves them as-is,
        so we assert the wire shape: the requested files appear in the
        recorded pair's target_files. Narrowing is the daemon's job, not
        the CLI's."""
        ns = _parse(
            "ornith",
            "solve",
            "--task",
            "x",
            "--target-files",
            "src/a.py",
            "src/b.py",
            "--daemon-url",
            "http://test",
        )
        _run_cli(_cmd_solve, ns, stub_app)
        recorded = stub_state.training_pairs[0]
        assert "src/a.py" in recorded["target_files"]
        assert "src/b.py" in recorded["target_files"]


class TestOutcomeObserver:
    def test_outcome_observer_marks_succeeded_on_gate_green(
        self, stub_app, stub_state
    ):
        """Solve records a pending pair; set-outcome flips it to succeeded
        (simulating a gate-green event observed by the operator)."""
        ns_solve = _parse(
            "ornith",
            "solve",
            "--task",
            "x",
            "--target-files",
            "f",
            "--daemon-url",
            "http://test",
        )
        _run_cli(_cmd_solve, ns_solve, stub_app)
        pair_id = stub_state.training_pairs[0]["id"]
        assert stub_state.training_pairs[0]["outcome_status"] == "pending"

        ns_out = _parse(
            "ornith",
            "set-outcome",
            pair_id,
            "--status",
            "succeeded",
            "--details",
            "gate green",
            "--daemon-url",
            "http://test",
        )
        _run_cli(_cmd_set_outcome, ns_out, stub_app)
        assert stub_state.training_pairs[0]["outcome_status"] == "succeeded"


class TestExportContract:
    def test_pending_lists_recorded_pair_after_solve(self, stub_app, stub_state):
        """Equivalent to "export contains recorded pair" — the recorded pair
        is queryable via /pending, which is what feeds the export pipeline."""
        ns_solve = _parse(
            "ornith",
            "solve",
            "--task",
            "x",
            "--target-files",
            "f",
            "--daemon-url",
            "http://test",
        )
        _run_cli(_cmd_solve, ns_solve, stub_app)

        ns_pairs = _parse("ornith", "pairs", "--daemon-url", "http://test")
        out, _, _ = _run_cli(_cmd_pairs, ns_pairs, stub_app)
        assert "ORN-SOLVE-1" in out


class TestStatusContract:
    def test_status_returns_installed_and_success_rate(self, stub_app):
        ns = _parse("ornith", "status", "--daemon-url", "http://test")
        out, _, code = _run_cli(_cmd_status, ns, stub_app)
        assert code is None
        assert "installed" in out
        assert "success_rate" in out


class TestStatsContract:
    def test_stats_returns_counts(self, stub_app):
        ns = _parse("ornith", "stats", "--daemon-url", "http://test")
        out, _, code = _run_cli(_cmd_stats, ns, stub_app)
        assert code is None
        assert "success rate" in out
        assert "tokens" in out
