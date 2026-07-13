from __future__ import annotations

import tempfile
from typing import cast

from general_ludd.dogfood.sprint_parser import SprintItem, parse_sprint_markdown


class TestSprintItem:
    def test_default_construction(self) -> None:
        item = SprintItem(objective_number=1, title="Test", status="active")
        assert item.objective_number == 1
        assert item.title == "Test"
        assert item.status == "active"
        assert item.tasks == []
        assert item.acceptance_criteria == []

    def test_with_tasks_and_ac(self) -> None:
        item = SprintItem(
            objective_number=2,
            title="Feature X",
            status="done",
            tasks=["task a", "task b"],
            acceptance_criteria=["must pass", "must be fast"],
        )
        assert item.tasks == ["task a", "task b"]
        assert item.acceptance_criteria == ["must pass", "must be fast"]


class TestParseSprintMarkdown:
    _SAMPLE = (
        "## Objective 1: Setup\n"
        "  Status: active\n"
        "- [ ] Install deps\n"
        "- [ ] Configure env\n"
        "- AC1: environment boots\n"
        "- AC2: health check green\n"
        "\n"
        "## Objective 2: Feature\n"
        "  Status: done\n"
        "- [ ] Implement TUI\n"
        "- [ ] Add tests\n"
        "- AC1: all tests pass\n"
    )

    def _write_and_parse(self, content: str) -> list[SprintItem]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()
            self.last_path = cast(str, f.name)
        result = parse_sprint_markdown(self.last_path)
        return result

    def test_parses_objective_count(self) -> None:
        items = self._write_and_parse(self._SAMPLE)
        assert len(items) == 2

    def test_parses_objective_title(self) -> None:
        items = self._write_and_parse(self._SAMPLE)
        assert items[0].title == "Setup"
        assert items[1].title == "Feature"

    def test_parses_objective_number(self) -> None:
        items = self._write_and_parse(self._SAMPLE)
        assert items[0].objective_number == 1
        assert items[1].objective_number == 2

    def test_parses_status(self) -> None:
        items = self._write_and_parse(self._SAMPLE)
        assert items[0].status == "active"
        assert items[1].status == "done"

    def test_parses_tasks(self) -> None:
        items = self._write_and_parse(self._SAMPLE)
        assert items[0].tasks == ["Install deps", "Configure env"]
        assert items[1].tasks == ["Implement TUI", "Add tests"]

    def test_parses_acceptance_criteria(self) -> None:
        items = self._write_and_parse(self._SAMPLE)
        assert items[0].acceptance_criteria == ["environment boots", "health check green"]
        assert items[1].acceptance_criteria == ["all tests pass"]

    def test_unknown_status_when_missing(self) -> None:
        content = "## Objective 1\n- [ ] a task\n"
        items = self._write_and_parse(content)
        assert items[0].status == "unknown"

    def test_title_empty_when_missing(self) -> None:
        content = "## Objective 5\nStatus: pending\n- [ ] x\n"
        items = self._write_and_parse(content)
        assert items[0].title == ""
        assert items[0].objective_number == 5

    def test_no_objectives_returns_empty(self) -> None:
        content = "Some preamble text\n- [ ] just a task\n"
        items = self._write_and_parse(content)
        assert items == []

    def test_tasks_strip_whitespace(self) -> None:
        content = "## Objective 1\nStatus: pending\n- [ ]   spaced task   \n"
        items = self._write_and_parse(content)
        assert items[0].tasks == ["spaced task"]

    def test_single_objective(self) -> None:
        content = "## Objective 3: Solo\nStatus: done\n- [ ] one\n- AC1: criteria\n"
        items = self._write_and_parse(content)
        assert len(items) == 1
        assert items[0].title == "Solo"
        assert items[0].tasks == ["one"]
        assert items[0].acceptance_criteria == ["criteria"]
