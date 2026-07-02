"""Regression test: the traces facet must NOT leak traces across tenants.

The /api/traces (+ /api/facts ``traces`` block) facet is sourced from the
in-process ``RecentTracesBuffer``. Before the XT trace-leak fix,
``ExecutionTrace`` had no ``project_id`` and ``snapshot()`` could only filter by
``todo_id``, so ANY PSK holder saw EVERY project's execution traces — trace
names, phases, costs, and tokens of other tenants.

The fix stamps each trace with its owning ``project_id`` and gives
``RecentTracesBuffer.recent()/snapshot()`` (and ``_traces_facet``) a
``project_id`` scope: a scoped query returns only that project's traces and
EXCLUDES legacy None-project traces, while an unscoped (``None``) query
preserves the pre-existing "return everything" behaviour. These tests pin the
no-leak contract, mirroring test_accounting_facet_no_leak.py.
"""

from __future__ import annotations

from types import SimpleNamespace

from general_ludd.code_intelligence.introspect import CodebaseIntrospector
from general_ludd.observability.trace_store import RecentTracesBuffer
from general_ludd.observability.tracer import ExecutionTrace
from general_ludd.routers import embeddings, facts


def _trace(todo_id: str, project_id: str | None, phase: str = "generate") -> ExecutionTrace:
    """A completed single-span trace stamped with an owning project."""
    t = ExecutionTrace(todo_id=todo_id, work_type="code", project_id=project_id)
    span = t.start_span(name=f"{phase}-span", phase=phase)
    span.complete(
        status="success",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        model_profile_id="profile-a",
    )
    return t


def _fake_app(buffer: RecentTracesBuffer) -> SimpleNamespace:
    """A minimal stand-in exposing only ``app.state._recent_traces``.

    ``_traces_facet`` reads the buffer and the (absent) otel bridge via
    ``getattr(app.state, ...)`` — SimpleNamespace satisfies that contract
    without spinning up a real FastAPI app.
    """
    return SimpleNamespace(state=SimpleNamespace(_recent_traces=buffer))


def _seed_two_tenants() -> RecentTracesBuffer:
    buf = RecentTracesBuffer(maxlen=50)
    buf.record(_trace("A-todo-1", project_id="proj-A"))
    buf.record(_trace("B-todo-1", project_id="proj-B"))
    buf.record(_trace("A-todo-2", project_id="proj-A"))
    buf.record(_trace("B-todo-2", project_id="proj-B"))
    return buf


class TestRecentTracesBufferScoping:
    def test_snapshot_scoped_returns_only_that_project(self):
        buf = _seed_two_tenants()

        snap = buf.snapshot(project_id="proj-A")

        todo_ids = {row["todo_id"] for row in snap["recent"]}
        project_ids = {row["project_id"] for row in snap["recent"]}
        # Only project A's traces are present...
        assert todo_ids == {"A-todo-1", "A-todo-2"}
        assert project_ids == {"proj-A"}
        assert snap["count"] == 2
        # ...and NONE of project B's traces leak through.
        assert "B-todo-1" not in todo_ids
        assert "B-todo-2" not in todo_ids
        assert "proj-B" not in project_ids

    def test_snapshot_by_phase_aggregate_is_tenant_isolated(self):
        buf = _seed_two_tenants()

        snap = buf.snapshot(project_id="proj-A")

        # A has exactly two "generate" spans; B's spans must not be aggregated
        # into the cost/token rollup a scoped caller sees.
        assert snap["by_phase"]["generate"]["span_count"] == 2
        assert snap["by_phase"]["generate"]["total_tokens"] == 100  # 2 * 50
        assert abs(snap["by_phase"]["generate"]["total_cost_usd"] - 0.002) < 1e-9

    def test_recent_scoped_filter(self):
        buf = _seed_two_tenants()

        only_b = buf.recent(project_id="proj-B")

        assert {t.todo_id for t in only_b} == {"B-todo-1", "B-todo-2"}
        assert all(t.project_id == "proj-B" for t in only_b)

    def test_scoped_query_excludes_legacy_none_project_traces(self):
        buf = _seed_two_tenants()
        # A legacy trace captured before the project_id field existed.
        buf.record(_trace("legacy-todo", project_id=None))

        scoped = buf.snapshot(project_id="proj-A")

        todo_ids = {row["todo_id"] for row in scoped["recent"]}
        # The unattributed legacy trace must NOT leak to a scoped caller.
        assert "legacy-todo" not in todo_ids
        assert todo_ids == {"A-todo-1", "A-todo-2"}

    def test_unscoped_none_preserves_existing_behaviour(self):
        buf = _seed_two_tenants()
        buf.record(_trace("legacy-todo", project_id=None))

        # An unscoped/global caller (project_id=None) still sees everything,
        # including legacy None-project traces — no behavioural regression.
        snap = buf.snapshot()

        todo_ids = {row["todo_id"] for row in snap["recent"]}
        assert todo_ids == {
            "A-todo-1",
            "A-todo-2",
            "B-todo-1",
            "B-todo-2",
            "legacy-todo",
        }
        assert snap["count"] == 5


class TestTracesFacetScoping:
    def test_traces_facet_scoped_does_not_leak_other_tenant(self):
        app = _fake_app(_seed_two_tenants())

        facet = facts._traces_facet(app, project_id="proj-A")

        todo_ids = {row["todo_id"] for row in facet["recent"]}
        assert todo_ids == {"A-todo-1", "A-todo-2"}
        # Project B's trace names / phases / costs never reach the caller.
        assert "B-todo-1" not in todo_ids
        assert "B-todo-2" not in todo_ids
        assert all(row["project_id"] == "proj-A" for row in facet["recent"])
        # Otel status still reported honestly (no bridge configured here).
        assert facet["otel_exporter_status"] == "disabled"

    def test_traces_facet_unscoped_returns_all(self):
        app = _fake_app(_seed_two_tenants())

        facet = facts._traces_facet(app, project_id=None)

        todo_ids = {row["todo_id"] for row in facet["recent"]}
        assert todo_ids == {"A-todo-1", "A-todo-2", "B-todo-1", "B-todo-2"}


class TestCodebasePerfCostScoping:
    """The codebase facet's perf_cost by-phase aggregate reads the SAME trace
    buffer and must be tenant-isolated too — otherwise /api/facts?project_id=A
    re-leaks cross-tenant trace cost/token totals through codebase.perf_cost.
    """

    def test_perf_cost_scoped_isolates_cost_and_tokens(self):
        buf = _seed_two_tenants()
        # A has exactly two "generate" spans (cost 0.001 / 50 output tokens each).
        introspector = CodebaseIntrospector(
            repo_root=".", traces_buffer=buf, project_id="proj-A"
        )

        perf = introspector._perf_cost()

        by_phase = perf["by_phase"]
        assert by_phase["generate"]["span_count"] == 2
        assert by_phase["generate"]["total_tokens"] == 100  # 2 * 50, A only
        assert abs(by_phase["generate"]["total_cost_usd"] - 0.002) < 1e-9

    def test_perf_cost_unscoped_aggregates_all(self):
        buf = _seed_two_tenants()  # 2 A + 2 B generate spans
        introspector = CodebaseIntrospector(
            repo_root=".", traces_buffer=buf, project_id=None
        )

        perf = introspector._perf_cost()

        # Unscoped/global caller still sees the full cross-tenant aggregate.
        assert perf["by_phase"]["generate"]["span_count"] == 4
        assert perf["by_phase"]["generate"]["total_tokens"] == 200


class TestEmbeddingsTracesCorpusScoping:
    """POST /api/embeddings/search corpus=traces reads the SAME buffer and
    returned every tenant's trace_id / todo_id / cost. It must honour the
    request's project_id (cross-tenant isolation), mirroring the events corpus.
    """

    def _search(self, buffer: RecentTracesBuffer, project_id: str | None):
        app = _fake_app(buffer)
        req = embeddings.EmbeddingSearchRequest(
            text="generate", top_k=20, project_id=project_id
        )
        return embeddings._search_traces(app, req)

    def test_traces_corpus_scoped_does_not_leak_other_tenant(self):
        resp = self._search(_seed_two_tenants(), project_id="proj-A")

        todo_ids = {item.metadata.get("todo_id") for item in resp.results}
        assert todo_ids == {"A-todo-1", "A-todo-2"}
        # Project B's traces never appear in the ranked search results.
        assert "B-todo-1" not in todo_ids
        assert "B-todo-2" not in todo_ids

    def test_traces_corpus_unscoped_returns_all(self):
        resp = self._search(_seed_two_tenants(), project_id=None)

        todo_ids = {item.metadata.get("todo_id") for item in resp.results}
        assert todo_ids == {"A-todo-1", "A-todo-2", "B-todo-1", "B-todo-2"}
