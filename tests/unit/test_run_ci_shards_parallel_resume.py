"""Tests for the incremental resume logic in run_ci_shards_parallel.py."""

from __future__ import annotations

from pathlib import Path

from scripts.run_ci_shards_parallel import (
    _batch_key,
    _load_resume_state,
    _partition_test_paths,
    _resume_skip_batch,
    _save_resume_state,
)


def test_batch_key_is_stable_and_order_sensitive() -> None:
    files = ["tests/unit/test_a.py", "tests/unit/test_b.py"]
    key1 = _batch_key("unit-1a1", 1, files)
    key2 = _batch_key("unit-1a1", 1, files)
    assert key1 == key2
    assert key1.startswith("unit-1a1:batch-001:")
    assert _batch_key("unit-1a1", 2, files) != key1
    assert _batch_key("unit-1a1", 1, list(reversed(files))) != key1


def test_partition_test_paths_expands_and_chunks_directories(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_b.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_c.py").write_text("", encoding="utf-8")

    batches = _partition_test_paths(["tests/unit"], max_files=2, root=tmp_path)

    assert len(batches) == 2
    assert batches[0] == ["tests/unit/test_a.py", "tests/unit/test_b.py"]
    assert batches[1] == ["tests/unit/test_c.py"]


def test_partition_test_paths_keeps_file_arguments(tmp_path: Path) -> None:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_z.py").write_text("", encoding="utf-8")

    batches = _partition_test_paths(
        ["tests/unit/test_z.py"],
        max_files=2,
        root=tmp_path,
    )

    assert batches == [["tests/unit/test_z.py"]]


def test_resume_skip_returns_false_when_no_entry(tmp_path: Path) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    coverage_shards.mkdir()
    assert _resume_skip_batch({}, "unit-1a1", 1, ["a.py"], coverage_shards, tmp_path) is False


def test_resume_skip_returns_false_for_failed_batch(tmp_path: Path) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    coverage_shards.mkdir()
    state = {
        "rc": 1,
        "coverage_fragment": "coverage-fragments/.coverage.unit-1a1.batch-001",
    }
    assert (
        _resume_skip_batch(
            {"unit-1a1:batch-001:abc": state},
            "unit-1a1",
            1,
            ["a.py"],
            coverage_shards,
            tmp_path,
        )
        is False
    )


def test_resume_skip_copies_fragment_for_passed_batch(tmp_path: Path) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    coverage_shards.mkdir()
    source = coverage_shards / ".coverage.unit-1a1.batch-001"
    source.write_bytes(b"coverage-data")
    state = {
        "rc": 0,
        "coverage_fragment": "coverage-fragments/.coverage.unit-1a1.batch-001",
    }
    key = _batch_key("unit-1a1", 1, ["a.py"])
    assert (
        _resume_skip_batch(
            {key: state},
            "unit-1a1",
            1,
            ["a.py"],
            coverage_shards,
            tmp_path,
        )
        is True
    )
    copied = coverage_shards / ".coverage.unit-1a1.batch-001"
    assert copied.read_bytes() == b"coverage-data"


def test_resume_skip_returns_false_when_fragment_missing(tmp_path: Path) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    coverage_shards.mkdir()
    state = {
        "rc": 0,
        "coverage_fragment": "coverage-fragments/.coverage.unit-1a1.batch-001",
    }
    key = _batch_key("unit-1a1", 1, ["a.py"])
    assert (
        _resume_skip_batch(
            {key: state},
            "unit-1a1",
            1,
            ["a.py"],
            coverage_shards,
            tmp_path,
        )
        is False
    )


def test_resume_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "resume.json"
    state: dict[str, object] = {
        "schema_version": 1,
        "candidate_sha": "abc123",
        "runner": "scripts/run_ci_shards_parallel.py",
        "unit-1a1:batch-001:abc": {"rc": 0, "coverage_fragment": "coverage-fragments/x"},
    }
    _save_resume_state(path, state)
    loaded = _load_resume_state(path)
    assert loaded == state


def test_resume_state_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "resume.json"
    path.write_text("not-json{", encoding="utf-8")
    assert _load_resume_state(path) == {}
