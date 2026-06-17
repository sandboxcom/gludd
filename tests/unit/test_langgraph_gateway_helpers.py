"""Focused tests for LangGraphGateway's extracted helpers.

Covers the two private helpers de-duplicated during the models dedup refactor:
- ``_task_type_from_str`` (module-level work-type parser with FEATURE fallback)
- ``LangGraphGateway._extract_content`` (result.content-or-str staticmethod)
"""

from __future__ import annotations

from general_ludd.models.langgraph_gateway import (
    LangGraphGateway,
    _task_type_from_str,
)
from general_ludd.schemas.benchmark import TaskType


class TestTaskTypeFromStr:
    def test_exact_match(self) -> None:
        assert _task_type_from_str("bug_fix") is TaskType.BUG_FIX

    def test_hyphen_normalized_to_underscore(self) -> None:
        assert _task_type_from_str("security-fix") is TaskType.SECURITY_FIX

    def test_uppercase_normalized(self) -> None:
        assert _task_type_from_str("REFACTOR") is TaskType.REFACTOR

    def test_mixed_case_and_hyphen(self) -> None:
        assert _task_type_from_str("Test-Write") is TaskType.TEST_WRITE

    def test_unknown_falls_back_to_feature_default(self) -> None:
        assert _task_type_from_str("not-a-real-type") is TaskType.FEATURE

    def test_custom_default_used_on_unknown(self) -> None:
        assert (
            _task_type_from_str("nope", default=TaskType.DOCUMENTATION)
            is TaskType.DOCUMENTATION
        )

    def test_known_value_ignores_custom_default(self) -> None:
        assert (
            _task_type_from_str("feature", default=TaskType.DOCUMENTATION)
            is TaskType.FEATURE
        )


class _WithContent:
    def __init__(self, content: str) -> None:
        self.content = content


class TestExtractContent:
    def test_returns_content_attribute(self) -> None:
        assert LangGraphGateway._extract_content(_WithContent("hello")) == "hello"

    def test_falls_back_to_str_when_no_content(self) -> None:
        assert LangGraphGateway._extract_content(42) == "42"

    def test_plain_string_passthrough(self) -> None:
        # A bare string has no ``.content`` attribute, so str() returns it as-is.
        assert LangGraphGateway._extract_content("raw") == "raw"

    def test_empty_content_preserved(self) -> None:
        assert LangGraphGateway._extract_content(_WithContent("")) == ""
