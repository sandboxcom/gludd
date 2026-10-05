"""Typed fail-closed coverage for the Ornith training repository."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.ornith import training_repo


class _ScalarRows:
    """Expose SQLAlchemy's scalar-result surface."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        """Return configured scalar rows."""
        return self._rows


class _Result:
    """Expose the result methods used by the repository."""

    def __init__(self, rows: list[object], scalar: object | None = None) -> None:
        self._rows = rows
        self._scalar = scalar

    def scalars(self) -> _ScalarRows:
        """Return scalar-row access."""
        return _ScalarRows(self._rows)

    def scalar_one_or_none(self) -> object | None:
        """Return the configured single scalar."""
        return self._scalar


class _Session:
    """Own deterministic async execute results."""

    def __init__(self, results: list[_Result] | None = None) -> None:
        self.results = list(results or [])
        self.added: list[object] = []

    def add(self, row: object) -> None:
        """Record an added row."""
        self.added.append(row)

    async def flush(self) -> None:
        """Model the repository flush boundary."""

    async def execute(self, _statement: object) -> _Result:
        """Return the next configured result."""
        return self.results.pop(0) if self.results else _Result([])


def _repo(session: _Session | None = None) -> training_repo.OrnithTrainingRepo:
    """Build a repository over the typed fake session."""
    return training_repo.OrnithTrainingRepo(cast(AsyncSession, session or _Session()))


def _invocation(**updates: Any) -> training_repo.OrnithInvocation:
    """Build a valid invocation with optional field updates."""
    values: dict[str, Any] = {
        "task_description": "Generate a safe role",
        "target_files": ["roles/example/tasks/main.yml"],
        "scaffold_kind": "playbook",
        "scaffold_content": "- hosts: all",
        "agent_id": "ornith",
    }
    values.update(updates)
    return training_repo.OrnithInvocation(**values)


@pytest.mark.asyncio
async def test_record_pair_rejects_invalid_kind_and_empty_agent() -> None:
    """Reject invalid scaffold ownership before database mutation."""
    repo = _repo()
    with pytest.raises(ValueError, match="invalid scaffold_kind"):
        await repo.record_pair(_invocation(scaffold_kind="binary"))
    with pytest.raises(ValueError, match="agent_id must not be empty"):
        await repo.record_pair(_invocation(agent_id=""))


@pytest.mark.asyncio
async def test_set_outcome_rejects_invalid_status_and_missing_pair() -> None:
    """Fail closed for unknown statuses and pair identifiers."""
    repo = _repo()
    with pytest.raises(ValueError, match="invalid outcome_status"):
        await repo.set_outcome("pair", "unknown")
    with pytest.raises(KeyError, match="missing"):
        await repo.set_outcome("missing", "succeeded")


@pytest.mark.asyncio
async def test_filtered_pair_queries_cover_empty_invalid_and_bounded_paths() -> None:
    """Apply status, project, and lookback filters with bounded limits."""
    row = object()
    session = _Session([_Result([row]), _Result([row])])
    repo = _repo(session)

    assert await repo.list_pairs(status="succeeded", project_id="project", limit=2) == [row]
    assert await repo.list_pairs_by_statuses([]) == []
    with pytest.raises(ValueError, match="invalid outcome_status"):
        await repo.list_pairs_by_statuses(["unknown"])
    assert await repo.list_pairs_by_statuses(
        ["reverted"], project_id="project", lookback_days=7, limit=5000
    ) == [row]


@pytest.mark.asyncio
async def test_export_filters_and_sanitizes_malformed_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confine export and replace malformed stored JSON with safe defaults."""
    row = SimpleNamespace(
        id="ORN-1",
        scaffold_kind="patch",
        scaffold_content="patch",
        scaffold_hash="digest",
        task_description="task",
        target_files="not-json",
        iterations_used=1,
        tokens_consumed=2,
        model_sha="sha",
        agent_id="ornith",
        project_id="project",
        invoked_at=None,
        outcome_status="succeeded",
        outcome_details="not-json",
        outcome_set_at=None,
    )
    out = tmp_path / "dataset.jsonl"
    monkeypatch.setattr(training_repo, "confine_export_path", lambda _path, _default: out)
    repo = _repo(_Session([_Result([row])]))

    path = await repo.export_dataset(
        since=datetime.now(UTC),
        project_id="project",
        out_path=out,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["scaffold"]["target_files"] == []
    assert record["scaffold"]["invoked_at"] is None
    assert record["outcome"] == {"status": "succeeded", "details": {}, "set_at": None}
