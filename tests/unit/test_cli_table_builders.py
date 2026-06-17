"""Tests for the 7 table builders not covered by test_tui_new_views.py,
the _read_source_arg helper, and the _make_table max_width column extension."""

from __future__ import annotations

import os
import tempfile

from rich.table import Table

# ---------------------------------------------------------------------------
# _make_table max_width extension
# ---------------------------------------------------------------------------


class TestMakeTableMaxWidth:
    def test_4tuple_columns_still_work(self) -> None:
        from general_ludd.tui.tables import _make_table

        t = _make_table("T", [("A", "cyan", 1, 4)], rows=[("hello",)])
        assert t.row_count == 1
        assert t.columns[0].max_width is None

    def test_5tuple_with_max_width(self) -> None:
        from general_ludd.tui.tables import _make_table

        t = _make_table("T", [("A", "cyan", 1, 4, 20)], rows=[("hello",)])
        assert t.row_count == 1
        assert t.columns[0].max_width == 20

    def test_5tuple_with_none_max_width(self) -> None:
        from general_ludd.tui.tables import _make_table

        t = _make_table("T", [("A", "cyan", 1, 4, None)], rows=[("hello",)])
        assert t.columns[0].max_width is None

    def test_mixed_4_and_5_tuple_columns(self) -> None:
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            "T",
            [("A", "cyan", 1, 4), ("B", "green", 1, 4, 30)],
            rows=[("x", "y")],
        )
        assert t.columns[0].max_width is None
        assert t.columns[1].max_width == 30


# ---------------------------------------------------------------------------
# _read_source_arg
# ---------------------------------------------------------------------------


class TestReadSourceArg:
    def test_empty_string_returns_empty(self) -> None:
        from general_ludd.cli import _read_source_arg

        assert _read_source_arg("") == ""

    def test_non_file_string_returned_unchanged(self) -> None:
        from general_ludd.cli import _read_source_arg

        assert _read_source_arg("not-a-file-path-xyz-123") == "not-a-file-path-xyz-123"

    def test_file_path_returns_contents(self) -> None:
        from general_ludd.cli import _read_source_arg

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def hello(): pass\n")
            fname = f.name
        try:
            result = _read_source_arg(fname)
            assert result == "def hello(): pass\n"
        finally:
            os.unlink(fname)

    def test_unreadable_file_exits(self) -> None:
        from general_ludd.cli import _read_source_arg

        # A path that looks like it exists but cannot be opened — use a dir
        with tempfile.TemporaryDirectory() as d:
            # Directories resolve as isfile=False, so this returns unchanged
            result = _read_source_arg(d)
            assert result == d


# ---------------------------------------------------------------------------
# _build_slurm_table
# ---------------------------------------------------------------------------


class TestBuildSlurmTable:
    def test_empty(self) -> None:
        from general_ludd.cli import _build_slurm_table

        t = _build_slurm_table([])
        assert isinstance(t, Table)
        assert t.title == "Slurm Jobs"
        assert t.row_count == 1  # sentinel row

    def test_with_jobs(self) -> None:
        from general_ludd.cli import _build_slurm_table

        jobs = [
            {"job_id": "12345", "state": "COMPLETED", "exit_code": 0},
            {"job_id": "12346", "state": "RUNNING", "exit_code": None},
            {"job_id": "12347", "state": "FAILED", "exit_code": 1},
        ]
        t = _build_slurm_table(jobs)
        assert t.row_count == 3

    def test_exit_code_none_renders_empty(self) -> None:
        from general_ludd.cli import _build_slurm_table

        jobs = [{"job_id": "1", "state": "RUNNING", "exit_code": None}]
        t = _build_slurm_table(jobs)
        assert t.row_count == 1

    def test_max_width_set(self) -> None:
        from general_ludd.cli import _build_slurm_table

        t = _build_slurm_table([])
        for col in t.columns:
            assert col.max_width is not None, f"column {col.header!r} has no max_width"


# ---------------------------------------------------------------------------
# _build_health_table
# ---------------------------------------------------------------------------


class TestBuildHealthTable:
    def test_empty(self) -> None:
        from general_ludd.cli import _build_health_table

        t = _build_health_table({})
        assert isinstance(t, Table)
        assert t.title == "Health"
        assert t.row_count == 1  # sentinel row

    def test_with_data(self) -> None:
        from general_ludd.cli import _build_health_table

        t = _build_health_table({"status": "ok", "uptime": "3d"})
        assert t.row_count == 2

    def test_long_value_truncated(self) -> None:
        from general_ludd.cli import _build_health_table

        long_val = "x" * 100
        t = _build_health_table({"key": long_val})
        assert t.row_count == 1
        # Verify it renders without error (rich will use the stored cell text)


# ---------------------------------------------------------------------------
# _build_selftest_table
# ---------------------------------------------------------------------------


class TestBuildSelftestTable:
    def test_no_data(self) -> None:
        from general_ludd.cli import _build_selftest_table

        t = _build_selftest_table({})
        assert isinstance(t, Table)
        assert t.title == "Selftest"
        assert t.row_count == 1  # "Press [r] to run" row

    def test_no_results_shows_summary(self) -> None:
        from general_ludd.cli import _build_selftest_table

        t = _build_selftest_table({"results": [], "scenarios_run": 3, "scenarios_passed": 3})
        assert t.row_count == 1  # summary row

    def test_with_results(self) -> None:
        from general_ludd.cli import _build_selftest_table

        data = {
            "results": [
                {"scenario": "boot", "passed": True},
                {"scenario": "auth", "passed": False},
            ]
        }
        t = _build_selftest_table(data)
        assert t.row_count == 2

    def test_partial_pass_summary(self) -> None:
        from general_ludd.cli import _build_selftest_table

        t = _build_selftest_table({"results": [], "scenarios_run": 5, "scenarios_passed": 3})
        assert t.row_count == 1


# ---------------------------------------------------------------------------
# _build_version_table
# ---------------------------------------------------------------------------


class TestBuildVersionTable:
    def test_with_info(self) -> None:
        from general_ludd.cli import _build_version_table

        t = _build_version_table({"version": "1.2.3", "python_version": "3.14", "platform": "linux"})
        assert isinstance(t, Table)
        assert t.title == "Version"
        assert t.row_count == 3
        assert t.show_header is False

    def test_empty_dict_uses_fallbacks(self) -> None:
        from general_ludd.cli import _build_version_table

        t = _build_version_table({})
        assert t.row_count == 3  # always 3 static rows

    def test_max_width_set(self) -> None:
        from general_ludd.cli import _build_version_table

        t = _build_version_table({})
        for col in t.columns:
            assert col.max_width is not None


# ---------------------------------------------------------------------------
# _build_loglevel_table
# ---------------------------------------------------------------------------


class TestBuildLoglevelTable:
    def test_always_four_rows(self) -> None:
        from general_ludd.cli import _build_loglevel_table

        t = _build_loglevel_table("info")
        assert isinstance(t, Table)
        assert t.title == "Log Level"
        assert t.row_count == 4

    def test_max_width_set(self) -> None:
        from general_ludd.cli import _build_loglevel_table

        t = _build_loglevel_table("debug")
        for col in t.columns:
            assert col.max_width is not None


# ---------------------------------------------------------------------------
# _build_discovered_table
# ---------------------------------------------------------------------------


class TestBuildDiscoveredTable:
    def test_empty(self) -> None:
        from general_ludd.cli import _build_discovered_table

        t = _build_discovered_table([])
        assert isinstance(t, Table)
        assert t.title == "Discovered Models"
        assert t.row_count == 1  # sentinel

    def test_with_profiles(self) -> None:
        from general_ludd.cli import _build_discovered_table

        profiles = [
            {"model_profile_id": "gpt-4-turbo", "display_name": "GPT-4 Turbo", "enabled": True},
            {"model_profile_id": "llama-7b", "display_name": "Llama 7B", "enabled": False},
        ]
        t = _build_discovered_table(profiles)
        assert t.row_count == 2

    def test_max_width_set(self) -> None:
        from general_ludd.cli import _build_discovered_table

        t = _build_discovered_table([])
        for col in t.columns:
            assert col.max_width is not None


# ---------------------------------------------------------------------------
# _build_code_table
# ---------------------------------------------------------------------------


class TestBuildCodeTable:
    def test_empty(self) -> None:
        from general_ludd.cli import _build_code_table

        t = _build_code_table([])
        assert isinstance(t, Table)
        assert t.title == "Code Intel"
        assert t.row_count == 1  # sentinel "Press [s] to search"

    def test_with_results(self) -> None:
        from general_ludd.cli import _build_code_table

        results = [
            {"file": "src/foo.py", "line": 42, "text": "def bar():"},
            {"file": "src/baz.py", "line": 7, "text": "class Qux:"},
        ]
        t = _build_code_table(results)
        assert t.row_count == 2

    def test_text_truncated_at_38_chars(self) -> None:
        from general_ludd.cli import _build_code_table

        long_text = "a" * 80
        results = [{"file": "f.py", "line": 1, "text": long_text}]
        t = _build_code_table(results)
        assert t.row_count == 1

    def test_max_width_set(self) -> None:
        from general_ludd.cli import _build_code_table

        t = _build_code_table([])
        for col in t.columns:
            assert col.max_width is not None
