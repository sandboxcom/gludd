"""Tests for the incremental resume logic in run_ci_shards_serial.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.run_ci_shards_serial import (
    ResourcePaths,
    _batch_key,
    _load_resume_state,
    _preserve_coverage_fragments_for_resume,
    _resume_skip_batch,
    _save_resume_state,
    run,
)


def test_batch_key_is_stable_and_order_sensitive() -> None:
    files = ["tests/unit/test_a.py", "tests/unit/test_b.py"]
    key1 = _batch_key("unit-1a1", 1, files)
    key2 = _batch_key("unit-1a1", 1, files)
    assert key1 == key2
    assert key1.startswith("unit-1a1:batch-001:")
    assert _batch_key("unit-1a1", 2, files) != key1
    assert _batch_key("unit-1a1", 1, list(reversed(files))) != key1


def test_resume_skip_returns_false_when_no_entry(tmp_path: Path) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    coverage_shards.mkdir()
    assert _resume_skip_batch({}, "unit-1a1", 1, ["a.py"], coverage_shards, tmp_path) is False


def test_resume_skip_returns_false_for_failed_batch(tmp_path: Path) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    coverage_shards.mkdir()
    state = {"rc": 1, "coverage_fragment": "coverage-fragments/.coverage.unit-1a1.batch-001"}
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
    state = {"rc": 0, "coverage_fragment": "coverage-fragments/.coverage.unit-1a1.batch-001"}
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
    state = {"rc": 0, "coverage_fragment": "coverage-fragments/.coverage.unit-1a1.batch-001"}
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
        "runner": "scripts/run_ci_shards_serial.py",
        "unit-1a1:batch-001:abc": {"rc": 0, "coverage_fragment": "coverage-fragments/x"},
    }
    _save_resume_state(path, state)
    loaded = _load_resume_state(path)
    assert loaded == state


def test_resume_state_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "resume.json"
    path.write_text("not-json{", encoding="utf-8")
    assert _load_resume_state(path) == {}


def test_preserve_fragments_when_resume_enabled_and_run_failed() -> None:
    assert _preserve_coverage_fragments_for_resume(Path("/tmp/resume.json"), 1) is True


def test_preserve_fragments_when_resume_enabled_and_run_cancelled() -> None:
    assert _preserve_coverage_fragments_for_resume(Path("/tmp/resume.json"), 130) is True


def test_do_not_preserve_fragments_when_resume_disabled() -> None:
    assert _preserve_coverage_fragments_for_resume(None, 1) is False


def test_do_not_preserve_fragments_when_run_succeeded() -> None:
    assert _preserve_coverage_fragments_for_resume(Path("/tmp/resume.json"), 0) is False


def test_run_resumes_from_failed_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock three batches; fail the second, then resume and continue from it."""
    root = tmp_path / "ci-shards"
    coverage_shards = root / "coverage-fragments"
    coverage_json = root / "coverage.json"
    coverage_audit = root / "coverage-audit.json"
    attestation = root / "attestation.json"
    resume_file = root / "resume.json"

    module = "scripts.run_ci_shards_serial"
    resource_paths = ResourcePaths(
        root=root,
        coverage_shards=coverage_shards,
        coverage_json=coverage_json,
        coverage_audit=coverage_audit,
        attestation=attestation,
        resume=resume_file,
    )
    monkeypatch.setattr(f"{module}._RESOURCE_PATHS", resource_paths)
    monkeypatch.setattr(f"{module}.COVERAGE_SHARDS", coverage_shards)
    monkeypatch.setattr(f"{module}.COVERAGE_JSON", coverage_json)
    monkeypatch.setattr(f"{module}.COVERAGE_AUDIT", coverage_audit)
    monkeypatch.setattr(f"{module}._git_output", lambda *_a, **_k: (0, "deadbeef1122"))
    monkeypatch.setattr(f"{module}._interpreter_identity", lambda: {"version": "3.14.0"})
    monkeypatch.setattr(f"{module}._interpreter_is_unchanged", lambda *_a, **_k: True)
    monkeypatch.setattr(f"{module}._read_disk_usage", lambda _p: type("U", (), {"free": 10**12})())
    monkeypatch.setattr(f"{module}._cleanup_owned_tmpdir_safely", lambda *_a, **_k: 0)

    def _save_shard_coverage(shard: str, batch_index: int, *_args: object, **_kwargs: object) -> bool:
        fragment = coverage_shards / f".coverage.{shard}.batch-{batch_index:03d}"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        fragment.write_bytes(b"coverage")
        return True

    monkeypatch.setattr(f"{module}._save_shard_coverage", _save_shard_coverage)
    monkeypatch.setattr(f"{module}._aggregate_coverage", lambda: 0)

    plan = [("unit-mock", [["tests/unit/test_a.py"], ["tests/unit/test_b.py"], ["tests/unit/test_c.py"]])]
    monkeypatch.setattr(f"{module}._plan_shards", lambda *_a, **_k: plan)

    executed_labels: list[str] = []

    def _run_owned_pytest(command: list[str], *, env: dict[str, str], label: str, **kwargs: object) -> int:
        executed_labels.append(label)
        if label == "unit-mock:batch-002":
            return 1
        return 0

    monkeypatch.setattr(f"{module}._run_owned_pytest", _run_owned_pytest)

    rc_first = run(["unit-mock"], [], resume_path=resume_file, aggregate_coverage=False)
    assert rc_first != 0
    state_first = _load_resume_state(resume_file)
    assert any(k.startswith("unit-mock:batch-001:") for k in state_first)
    assert not any(k.startswith("unit-mock:batch-002:") for k in state_first)
    assert not any(k.startswith("unit-mock:batch-003:") for k in state_first)

    def _run_owned_pytest_resume(command: list[str], *, env: dict[str, str], label: str, **kwargs: object) -> int:
        executed_labels.append(label)
        return 0

    monkeypatch.setattr(f"{module}._run_owned_pytest", _run_owned_pytest_resume)

    rc_second = run(["unit-mock"], [], resume_path=resume_file, aggregate_coverage=False)
    assert rc_second == 0
    # Batch 001 was already recorded; it must not be re-executed.
    assert "unit-mock:batch-001" not in executed_labels[-3:]
    assert "unit-mock:batch-002" in executed_labels
    assert "unit-mock:batch-003" in executed_labels
    state_second = _load_resume_state(resume_file)
    assert any(k.startswith("unit-mock:batch-001:") for k in state_second)
    assert any(k.startswith("unit-mock:batch-002:") for k in state_second)
    assert any(k.startswith("unit-mock:batch-003:") for k in state_second)
