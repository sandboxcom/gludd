"""Behavioral tests for the migration-aware TASKS.md integrity audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_task_integrity.py"


@pytest.fixture
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_task_integrity_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archived_session_snapshots_are_not_revalidated(checker: ModuleType) -> None:
    content = """\
## Active — Current
- [ ] CUR.1 — open | priority: critical | effort: medium | status: pending
## Archived — Session snapshots
- [x] Duplicate — old row without modern metadata
- [x] Duplicate — copied row without evidence
## Session 54 — Active
- [x] S54.1 — done | priority: high | effort: S | status: completed | evidence: commit abcdefa
"""

    violations, item_count = checker.audit_content(content)

    assert violations == []
    assert item_count == 2


def test_active_checked_item_requires_measurable_evidence(checker: ModuleType) -> None:
    content = """\
## Active — Current
- [x] CUR.1 — done | priority: high | effort: S | status: completed | evidence: Wave 34
"""

    violations, _item_count = checker.audit_content(content)

    assert any("not measurable evidence" in violation for violation in violations)


def test_active_pending_item_requires_current_metadata(checker: ModuleType) -> None:
    content = """\
## Active — Current
- [ ] CUR.1 — open | priority: high | status: pending
"""

    violations, _item_count = checker.audit_content(content)

    assert violations == ["line 2: item missing required field(s): effort"]


@pytest.mark.parametrize("effort", ["XS", "S", "M", "L", "XL", "small", "medium", "large"])
def test_project_effort_vocabulary_is_accepted(
    checker: ModuleType,
    effort: str,
) -> None:
    content = f"""\
## Active — Current
- [ ] CUR.1 — open | priority: critical | effort: {effort} | status: in_progress
"""

    violations, _item_count = checker.audit_content(content)

    assert violations == []


def test_duplicate_active_ids_are_rejected(checker: ModuleType) -> None:
    content = """\
## Active — Current
- [ ] CUR.1 — first | priority: high | effort: S | status: pending
- [ ] CUR.1 — second | priority: high | effort: S | status: pending
"""

    violations, _item_count = checker.audit_content(content)

    assert violations == ["line 3: duplicate item ID 'CUR.1' (first seen at line 2)"]
