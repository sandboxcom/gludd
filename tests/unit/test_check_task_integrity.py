"""Behavioral tests for the migration-aware TASKS.md integrity audit."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_task_integrity.py"


@pytest.fixture
def checker() -> ModuleType:
    module = importlib.import_module("scripts.check_task_integrity")
    return importlib.reload(module)


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


def test_invalid_active_values_and_empty_evidence(checker: ModuleType) -> None:
    content = """\
## Active — Current
- [x] CUR.1 — done | priority: urgent | effort: XXL | status: done | evidence:
"""

    violations, item_count = checker.audit_content(content)

    assert item_count == 1
    assert any("empty | evidence: value" in violation for violation in violations)
    assert any("invalid priority 'urgent'" in violation for violation in violations)
    assert any("invalid effort 'XXL'" in violation for violation in violations)
    assert any("invalid status 'done'" in violation for violation in violations)


def test_nested_archived_sections_remain_outside_audit(checker: ModuleType) -> None:
    content = """\
## Archived — Session snapshots
### Older wave
- [x] OLD.1 — deliberately lacks current metadata
## Active — Current
- [ ] CUR.1 — open | priority: high | effort: small | status: pending
"""

    violations, item_count = checker.audit_content(content)

    assert violations == []
    assert item_count == 1


def test_concatenated_checklist_items_on_one_line_are_rejected(
    checker: ModuleType,
) -> None:
    content = (
        "## Active\n"
        "- [ ] CUR.1 - first | priority: high | effort: S | status: pending "
        "- [ ] CUR.2 - second | priority: low | effort: XS | status: pending\n"
    )

    violations, item_count = checker.audit_content(content)

    assert item_count == 1
    assert violations == [
        "line 2: multiple checklist items must use separate physical lines"
    ]


def test_main_reports_missing_valid_and_invalid_ledgers(
    checker: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task_path = tmp_path / "TASKS.md"
    checker.__dict__["TASKS_PATH"] = task_path

    assert checker.main() == 1
    assert "not found" in capsys.readouterr().out

    task_path.write_text(
        "## Active\n"
        "- [ ] CUR.1 — open | priority: high | effort: S | status: pending\n"
    )
    assert checker.main() == 0
    assert "PASSED (1 items, 0 violations)" in capsys.readouterr().out

    task_path.write_text(
        "## Active\n"
        "- [x] CUR.1 — done | priority: high | effort: S | status: completed\n"
    )
    assert checker.main() == 1
    assert "checked item lacks | evidence: field" in capsys.readouterr().out
