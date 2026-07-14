"""Structural tests for coordination/file_overlap.py."""

from __future__ import annotations

from general_ludd.coordination.file_overlap import (
    FileOverlapCoordinator,
    OverlapTimeout,
    _is_glob,
    _normalize,
    globs_overlap,
    paths_to_resources,
)

# ---- _normalize ----------------------------------------------------------------


def test_normalize_strips_whitespace() -> None:
    assert _normalize("  src/foo.py  ") == "src/foo.py"


def test_normalize_removes_leading_dot_slash() -> None:
    assert _normalize("./src/foo.py") == "src/foo.py"


def test_normalize_removes_multiple_leading_dot_slash() -> None:
    assert _normalize("./././src/foo.py") == "src/foo.py"


def test_normalize_collapses_double_slashes() -> None:
    assert _normalize("src//foo//bar.py") == "src/foo/bar.py"


def test_normalize_strips_trailing_slash() -> None:
    assert _normalize("src/foo/") == "src/foo"


def test_normalize_falls_back_to_root_on_empty_string() -> None:
    assert _normalize("") == "/"


def test_normalize_falls_back_to_root_on_slashes_only() -> None:
    assert _normalize("///") == "/"


def test_normalize_preserves_glob_characters() -> None:
    assert _normalize("src/*.py") == "src/*.py"
    assert _normalize("src/foo?.py") == "src/foo?.py"
    assert _normalize("src/[ab]*.py") == "src/[ab]*.py"


# ---- _is_glob ----------------------------------------------------------------


def test_is_glob_detects_star() -> None:
    assert _is_glob("src/*.py") is True


def test_is_glob_detects_question_mark() -> None:
    assert _is_glob("src/file?.py") is True


def test_is_glob_detects_bracket() -> None:
    assert _is_glob("src/file[0-9].py") is True


def test_is_glob_returns_false_for_literal_path() -> None:
    assert _is_glob("src/foo/bar.py") is False


def test_is_glob_returns_false_for_empty_string() -> None:
    assert _is_glob("") is False


# ---- globs_overlap -----------------------------------------------------------


def test_globs_overlap_identical_paths() -> None:
    assert globs_overlap("src/foo.py", "src/foo.py") is True


def test_globs_overlap_normalized_identical() -> None:
    assert globs_overlap("./src/foo.py", "src/foo.py") is True


def test_globs_overlap_literal_matches_glob() -> None:
    assert globs_overlap("src/foo.py", "src/*.py") is True


def test_globs_overlap_glob_matches_literal() -> None:
    assert globs_overlap("src/*.py", "src/foo.py") is True


def test_globs_overlap_literal_no_match_glob() -> None:
    assert globs_overlap("other/bar.py", "src/*.py") is False


def test_globs_overlap_two_disjoint_literals() -> None:
    assert globs_overlap("src/module_a.py", "src/module_b.py") is False


def test_globs_overlap_literal_is_directory_prefix() -> None:
    assert globs_overlap("src/foo", "src/foo/bar.py") is True


def test_globs_overlap_literal_is_directory_prefix_reversed() -> None:
    assert globs_overlap("src/foo/bar.py", "src/foo") is True


def test_globs_overlap_two_glob_same_prefix() -> None:
    assert globs_overlap("src/module_*.py", "src/module_*_test.py") is True


def test_globs_overlap_two_glob_disjoint_prefixes() -> None:
    assert globs_overlap("src/a/*.py", "src/b/*.py") is False


def test_globs_overlap_empty_paths() -> None:
    assert globs_overlap("", "") is True
    assert globs_overlap("/", "/") is True


# ---- paths_to_resources ------------------------------------------------------


def test_paths_to_resources_maps_single_path() -> None:
    result = paths_to_resources(["src/foo.py"])
    assert result == frozenset({"file:src/foo.py"})


def test_paths_to_resources_normalizes_paths() -> None:
    result = paths_to_resources(["./src/foo.py", "src//bar.py"])
    assert result == frozenset({"file:src/foo.py", "file:src/bar.py"})


def test_paths_to_resources_handles_glob_patterns() -> None:
    result = paths_to_resources(["src/*.py"])
    assert result == frozenset({"file:src/*.py"})


def test_paths_to_resources_returns_empty_frozenset_for_no_paths() -> None:
    result = paths_to_resources([])
    assert result == frozenset()


def test_paths_to_resources_deduplicates_equivalent_paths() -> None:
    result = paths_to_resources(["src/foo.py", "./src/foo.py", "src//foo.py"])
    assert result == frozenset({"file:src/foo.py"})


# ---- OverlapTimeout ----------------------------------------------------------


def test_overlap_timeout_is_timeout_error_subclass() -> None:
    assert issubclass(OverlapTimeout, TimeoutError)


def test_overlap_timeout_can_be_raised_and_caught() -> None:
    exc = OverlapTimeout("timed out waiting")
    assert str(exc) == "timed out waiting"


def test_overlap_timeout_caught_as_timeout_error() -> None:
    try:
        raise OverlapTimeout("test")
    except TimeoutError:
        pass


# ---- FileOverlapCoordinator --------------------------------------------------


def test_coordinator_instantiation_with_defaults() -> None:
    coord = FileOverlapCoordinator()
    assert coord._default_timeout == 30.0
    assert coord._active == {}
    assert coord._path_locks == {}


def test_coordinator_instantiation_custom_timeout() -> None:
    coord = FileOverlapCoordinator(default_timeout=60.0)
    assert coord._default_timeout == 60.0


def test_coordinator_register_active_adds_role() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    assert "role-A" in coord._active
    assert coord._active["role-A"].paths == frozenset({"src/foo.py"})


def test_coordinator_register_active_normalizes_paths() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["./src/foo.py"])
    assert coord._active["role-A"].paths == frozenset({"src/foo.py"})


def test_coordinator_register_active_replaces_existing_unreleased() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    coord.register_active("role-A", ["src/bar.py"])
    assert coord._active["role-A"].paths == frozenset({"src/bar.py"})


def test_coordinator_release_marks_role_released() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    coord.release("role-A")
    assert coord._active["role-A"].released is True
    assert coord._active["role-A"].merged is False


def test_coordinator_release_merged_removes_role() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    coord.release("role-A", merged=True)
    assert "role-A" not in coord._active


def test_coordinator_release_unknown_role_no_error() -> None:
    coord = FileOverlapCoordinator()
    coord.release("nonexistent")


def test_coordinator_active_roles_returns_unreleased_only() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    coord.register_active("role-B", ["src/bar.py"])
    coord.release("role-A")
    result = coord.active_roles()
    assert "role-A" not in result
    assert "role-B" in result
    assert result["role-B"] == frozenset({"src/bar.py"})


def test_coordinator_active_roles_empty_by_default() -> None:
    coord = FileOverlapCoordinator()
    assert coord.active_roles() == {}


def test_coordinator_overlaps_detects_overlapping_role() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    result = coord.overlaps(["src/foo.py"])
    assert result == ["role-A"]


def test_coordinator_overlaps_returns_empty_for_disjoint_paths() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    result = coord.overlaps(["other/bar.py"])
    assert result == []


def test_coordinator_overlaps_excludes_self() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    result = coord.overlaps(["src/foo.py"], role_id="role-A")
    assert result == []


def test_coordinator_overlaps_returns_sorted_ids() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-C", ["src/foo.py"])
    coord.register_active("role-A", ["src/foo.py"])
    result = coord.overlaps(["src/foo.py"])
    assert result == ["role-A", "role-C"]


def test_coordinator_overlaps_released_role_not_returned() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("role-A", ["src/foo.py"])
    coord.release("role-A")
    result = coord.overlaps(["src/foo.py"])
    assert result == []
