from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dispatch_diversity.py"


def _run(wave_file: Path, tasks_dir: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(wave_file)],
        cwd=tasks_dir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _write_wave(path: Path, prompts: list[str]) -> None:
    path.write_text(json.dumps(prompts, ensure_ascii=False), encoding="utf-8")


def _write_tasks(path: Path, lines: list[str]) -> None:
    lines.insert(0, "# TASKS.md\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_valid_wave_exits_0(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix sandbox controls | status: in_progress",
            "- [ ] NF.5 — e2e test gen | status: in_progress",
            "- [ ] ENF.2 — enforcement isolation | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(
        wave_file,
        [
            "fix SEC.1 sandbox hardening controls",
            "fix SEC.1 extravars validation",
            "implement NF.5 coverage heatmap",
            "implement NF.5 scenario generator",
            "audit ENF.2 process isolation",
            "write tests for daemon startup",
            "refactor ansible runner paths",
            "add documentation for release runbook",
            "fix typecheck errors in gateway.py",
            "update AGENTS.md with new policy",
        ],
    )

    rc, stdout, stderr = _run(wave_file, tasks_dir)
    assert rc == 0, f"exit={rc} stderr={stderr}"
    assert "PASS" in stdout


def test_exactly_10_required(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix controls | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(wave_file, ["task 1", "task 2", "task 3"])

    rc, _stdout, stderr = _run(wave_file, tasks_dir)
    assert rc == 1
    assert "3" in stderr


def test_at_least_3_topics_required(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix controls | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(
        wave_file,
        [
            "fix SEC.1 sandbox 1",
            "fix SEC.1 sandbox 2",
            "fix SEC.1 sandbox 3",
            "fix SEC.1 sandbox 4",
            "fix SEC.1 sandbox 5",
            "fix SEC.1 sandbox 6",
            "fix SEC.1 sandbox 7",
            "fix SEC.1 sandbox 8",
            "fix SEC.1 sandbox 9",
            "fix SEC.1 sandbox 10",
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir)
    assert rc == 1
    assert "TOPIC DIVERSITY" in stderr


def test_no_single_topic_exceeds_50_percent(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix controls | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(
        wave_file,
        [
            "fix SEC.1 sandbox 1",
            "fix SEC.1 sandbox 2",
            "fix SEC.1 sandbox 3",
            "fix SEC.1 sandbox 4",
            "fix SEC.1 sandbox 5",
            "fix SEC.1 sandbox 6",
            "write tests for module A",
            "write tests for module B",
            "write tests for module C",
            "write tests for module D",
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir)
    assert rc == 1
    assert "SLOT CONCENTRATION" in stderr


def test_at_least_1_continuation_required(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix controls | status: in_progress",
            "- [ ] NF.5 — e2e tests | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(
        wave_file,
        [
            "write tests for module A",
            "write tests for module B",
            "write tests for module C",
            "refactor ansible paths",
            "add documentation",
            "fix typecheck errors",
            "update AGENTS.md",
            "improve coverage",
            "clean dead code",
            "audit lint errors",
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir)
    assert rc == 1
    assert "NO CONTINUATIONS" in stderr


def test_multiple_continuations_pass(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix controls | status: in_progress",
            "- [ ] NF.5 — e2e tests | status: in_progress",
            "- [ ] ENF.2 — enforcement | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(
        wave_file,
        [
            "fix SEC.1 extras 1",
            "fix SEC.1 extras 2",
            "implement NF.5 coverage 1",
            "implement NF.5 coverage 2",
            "audit ENF.2 isolation",
            "write tests for daemon",
            "refactor ansible paths",
            "add release docs",
            "improve coverage gaps",
            "fix lint in gateway",
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir)
    assert rc == 0, f"exit={rc} stderr={stderr}"


def test_missing_file_exits_2(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix | status: in_progress",
        ],
    )

    wave_file = tmp_path / "nonexistent.json"
    rc, _stdout, _stderr = _run(wave_file, tasks_dir)
    assert rc == 2


def test_invalid_json_exits_2(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    wave_file.write_text("not json", encoding="utf-8")

    rc, _stdout, _stderr = _run(wave_file, tasks_dir)
    assert rc == 2


def test_no_args_exits_2(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 2


def test_non_array_json_exits_2(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    wave_file.write_text('{"a": 1}', encoding="utf-8")

    rc, _stdout, _stderr = _run(wave_file, tasks_dir)
    assert rc == 2


def test_non_string_entries_exits_2(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    wave_file.write_text("[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]", encoding="utf-8")

    rc, _stdout, _stderr = _run(wave_file, tasks_dir)
    assert rc == 2


def test_without_id_uses_keyword_topics(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        [
            "- [ ] SEC.1 — fix | status: in_progress",
        ],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(
        wave_file,
        [
            "fix SEC.1 sandbox 1",
            "write tests for daemon",
            "refactor ansible paths",
            "implement caching layer",
            "document release process",
            "audit security posture",
            "improve coverage gaps",
            "clean dead code",
            "update dependencies",
            "review error handling",
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir)
    assert rc == 0, f"exit={rc} stderr={stderr}"
