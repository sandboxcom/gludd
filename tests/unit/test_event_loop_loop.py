"""Tests for the EventLoop module — importability and key public API."""

from __future__ import annotations


class TestEventLoopImports:
    def test_module_importable(self) -> None:
        from general_ludd.event_loop import loop

        assert loop is not None

    def test_event_loop_class_exists(self) -> None:
        from general_ludd.event_loop.loop import EventLoop

        assert EventLoop is not None

    def test_file_claim_conflict_exists(self) -> None:
        from general_ludd.event_loop.loop import _FileClaimConflict

        assert issubclass(_FileClaimConflict, Exception)


class TestTaskTypeHelpers:
    def test_work_type_to_task_type_known_mapping(self) -> None:
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("generation")
        assert isinstance(result, TaskType)

    def test_work_type_to_task_type_unknown_falls_back(self) -> None:
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("nonexistent_work_type_xyz")
        assert isinstance(result, TaskType)


class TestSafeStrHelper:
    def test_safe_str_valid_attr(self) -> None:
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            name = "hello"

        result = _safe_str(Obj(), "name")
        assert result == "hello"

    def test_safe_str_missing_attr_with_default(self) -> None:
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            pass

        result = _safe_str(Obj(), "missing", default="fallback")
        assert result == "fallback"

    def test_safe_str_missing_attr_no_default(self) -> None:
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            pass

        result = _safe_str(Obj(), "missing")
        assert result is None

    def test_safe_str_non_string_attr_returns_default(self) -> None:
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            count = 42

        result = _safe_str(Obj(), "count")
        assert result is None
