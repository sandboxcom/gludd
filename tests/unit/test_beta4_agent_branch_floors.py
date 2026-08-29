"""Canonical branch-floor regressions for beta4 agent modules."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from general_ludd.agents import dispatch_checkpoint, researcher
from general_ludd.agents.hibernation import AgentEnvironmentSnapshot, DispatchState
from general_ludd.retrieval.searx_client import SearxNGClient, SearxResponse, SearxResult


def _dispatch_state(task_id: str) -> DispatchState:
    return DispatchState(
        todo_id=task_id,
        resolved_model_profile="default",
        resolved_prompt_profile="coder",
        prompt_text="resume",
        phase_marker="pre_model",
        tool_iterations=0,
        accumulated_messages=[],
        lease_holder_id="writer",
    )


def _durable_manager(tmp_path: Path) -> dispatch_checkpoint.CheckpointManager:
    store = dispatch_checkpoint.DurableHibernationStore(
        tmp_path / "snapshots",
        key_file=tmp_path / "hibernation.key",
    )
    return dispatch_checkpoint.CheckpointManager(store)


def test_dispatch_default_key_and_hostile_stem_are_namespaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default key placement and hostile identifiers stay inside owned roots."""
    monkeypatch.setattr(
        dispatch_checkpoint,
        "default_hibernation_dir",
        lambda: tmp_path / "snapshots",
    )
    assert dispatch_checkpoint._default_key_file() == tmp_path / "hibernation.key"
    assert dispatch_checkpoint._safe_stem("../../") == "unnamed"
    assert dispatch_checkpoint._safe_stem("todo/a b") == "todo_a_b"


def test_dispatch_wrong_sized_key_is_regenerated(tmp_path: Path) -> None:
    """A malformed durable key is replaced with an owner-valid 32-byte key."""
    key_file = tmp_path / "hibernation.key"
    key_file.write_bytes(b"short")
    store = dispatch_checkpoint.DurableHibernationStore(
        tmp_path / "snapshots",
        key_file=key_file,
    )
    assert store._mac_key == key_file.read_bytes()
    assert len(store._mac_key) == 32


def test_dispatch_checkpoint_enumeration_skips_every_invalid_shape(tmp_path: Path) -> None:
    """Boot enumeration ignores unreadable, malformed, and legacy snapshots."""
    manager = _durable_manager(tmp_path)
    base = manager.store.base_dir
    (base / "invalid.snapshot.json").write_text("{")
    (base / "list.snapshot.json").write_text("[]")
    (base / "payload-type.snapshot.json").write_text(json.dumps({"payload": 1}))
    (base / "bad-model.snapshot.json").write_text(json.dumps({"payload": "{}"}))

    legacy = AgentEnvironmentSnapshot(task_id="legacy", agent_name="coder")
    (base / "legacy.snapshot.json").write_text(
        json.dumps({"payload": legacy.model_dump_json()})
    )

    assert manager.list_interrupted() == []
    assert dispatch_checkpoint.CheckpointManager._read_raw_envelope(base) is None


def test_dispatch_no_bus_and_invalid_spool_offsets_fail_safe(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No-bus resume stays observable and corrupt offsets restart from zero."""
    caplog.set_level("INFO", logger=dispatch_checkpoint.__name__)
    manager = _durable_manager(tmp_path)
    manager.mark_resumed("todo-1", phase="pre_model")
    assert "dispatch resumed (no bus)" in caplog.text
    assert manager.read_spool_offset("missing") is None

    sidecar = manager.spool_sidecar_path("todo-1")
    sidecar.write_text("{")
    assert manager.read_spool_offset("todo-1") is None
    sidecar.write_text(json.dumps({"offset": -1}))
    assert manager.read_spool_offset("todo-1") is None
    sidecar.write_text(json.dumps({"offset": "4"}))
    assert manager.read_spool_offset("todo-1") is None


def test_dispatch_checkpoint_without_state_preserves_snapshot(tmp_path: Path) -> None:
    """Checkpointing a non-dispatch snapshot does not invent resumable state."""
    manager = _durable_manager(tmp_path)
    snapshot = AgentEnvironmentSnapshot(task_id="legacy", agent_name="coder")
    manager.checkpoint(snapshot, phase="pre_model")
    assert snapshot.dispatch_state is None


def test_researcher_rejects_non_numeric_confidence() -> None:
    """Strict findings reject confidence values that cannot be scored."""
    with pytest.raises(ValidationError, match="confidence must be a float"):
        researcher.ResearchFinding.model_validate({"confidence": "high"})


def _searx_instance(monkeypatch: pytest.MonkeyPatch, response: object) -> SearxNGClient:
    client = object.__new__(SearxNGClient)
    monkeypatch.setattr(client, "search", AsyncMock(return_value=response))
    return client


@pytest.mark.asyncio
async def test_researcher_executes_real_client_contract_and_filters_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real client seam rejects bad URLs/domains and enforces result caps."""
    response = SearxResponse(
        query="coverage",
        results=[
            SearxResult(url="not-a-url"),
            SearxResult(url="https://quora.com/answer", title="excluded"),
            SearxResult(url="https://docs.python.org/3/", title="primary", engine="docs"),
            SearxResult(url="https://github.com/coveragepy/coveragepy", title="second"),
        ],
    )
    agent = researcher.ResearcherAgent(searx_client=_searx_instance(monkeypatch, response))
    query = researcher.ResearchQuery(
        original_query="coverage",
        refined_query="coverage",
        exclude_domains=["quora.com"],
        max_results=1,
    )
    results = await agent._execute_search(query)
    assert [item["url"] for item in results] == ["https://docs.python.org/3/"]


@pytest.mark.asyncio
async def test_researcher_search_exception_is_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SearXNG exception yields no results rather than an unowned task failure."""
    client = object.__new__(SearxNGClient)
    monkeypatch.setattr(client, "search", AsyncMock(side_effect=RuntimeError("offline")))
    agent = researcher.ResearcherAgent(searx_client=client)
    assert await agent._execute_search(researcher.ResearchQuery(refined_query="offline")) == []


class _PageRetriever:
    def fetch_web_page(self, url: str) -> SimpleNamespace:
        if "error" in url:
            raise RuntimeError("page unavailable")
        if "empty" in url:
            return SimpleNamespace(status_code=404, content="", title="")
        return SimpleNamespace(status_code=200, content="full page", title="Page title")


@pytest.mark.asyncio
async def test_researcher_page_enrichment_owns_success_empty_and_error_paths() -> None:
    """Page enrichment updates successes and preserves fail-soft citations."""
    findings = [
        researcher.ResearchFinding(
            claim="claim",
            citations=[
                researcher.Citation(url="https://example.com/good"),
                researcher.Citation(url="https://example.com/empty"),
                researcher.Citation(url="https://example.com/error"),
            ],
        )
    ]
    agent = researcher.ResearcherAgent(web_retriever=_PageRetriever())
    enriched = await agent._enrich_with_page_content(findings)
    assert enriched[0].citations[0].snippet == "full page"
    assert enriched[0].citations[0].title == "Page title"
    assert enriched[0].citations[1].snippet == ""
    assert enriched[0].citations[2].snippet == ""


def test_researcher_score_handles_invalid_dates_and_report_confidence_bands() -> None:
    """Invalid dates are fail-soft and reports expose high and low confidence."""
    agent = researcher.ResearcherAgent()
    score = agent._score_single_finding(
        [{"domain": "docs.python.org", "published_date": "not-a-date"}]
    )
    assert 0.0 <= score <= 1.0

    findings = [
        researcher.ResearchFinding(claim="high confidence", confidence=0.8, corroborating_sources=1),
        researcher.ResearchFinding(claim="low confidence", confidence=0.2, corroborating_sources=1),
    ]
    report = agent._build_report(
        researcher.ResearchQuery(original_query="bands"),
        findings,
        0.125,
    )
    assert "high-confidence" in report.summary
    assert "low-confidence" in report.summary
