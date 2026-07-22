from pathlib import Path

from scripts import run_unit_shards
from scripts.run_unit_shards import discover_tests, shard_files


def test_discover_tests_excludes_pycache_and_non_tests(tmp_path: Path) -> None:
    (tmp_path / "test_alpha.py").write_text("def test_alpha(): pass\n")
    (tmp_path / "helper.py").write_text("")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "test_stale.py").write_text("")

    assert discover_tests(tmp_path) == [tmp_path / "test_alpha.py"]


def test_shard_files_uses_one_based_round_robin() -> None:
    files = [Path(f"test_{index}.py") for index in range(6)]

    assert shard_files(files, shard_count=3, index=1) == [files[0], files[3]]
    assert shard_files(files, shard_count=3, index=2) == [files[1], files[4]]
    assert shard_files(files, shard_count=3, index=3) == [files[2], files[5]]


def test_shard_files_rejects_invalid_index() -> None:
    try:
        shard_files([Path("test_a.py")], shard_count=2, index=0)
    except ValueError as exc:
        assert "between 1 and shard count" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_run_shard_timeout_zero_uses_long_hard_timeout(monkeypatch, tmp_path: Path) -> None:
    test_file = tmp_path / "test_alpha.py"
    test_file.write_text("def test_alpha(): pass\n")
    observed_timeouts: list[int | None] = []

    class FakeProc:
        pid = 123456

        def wait(self, timeout=None):
            observed_timeouts.append(timeout)
            return 0

    monkeypatch.setenv("SHARD_HARD_TIMEOUT", "1800")
    monkeypatch.setattr(run_unit_shards.subprocess, "Popen", lambda *args, **kwargs: FakeProc())

    rc = run_unit_shards.run_shard(
        files=[test_file],
        shard_count=1,
        index=1,
        timeout=0,
        pytest_args=[],
        verbosity="-q",
    )

    assert rc == 0
    assert observed_timeouts == [1800]
