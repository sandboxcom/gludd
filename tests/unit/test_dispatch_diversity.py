from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dispatch_diversity.py"


def _run(wave_file: Path, tasks_dir: Path, tasks_md: Path | None = None) -> tuple[int, str, str]:
    cmd = [sys.executable, str(SCRIPT), str(wave_file)]
    if tasks_md is not None:
        cmd.append(str(tasks_md))
    proc = subprocess.run(
        cmd,
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
            "implement NF.5 coverage heatmap",
            "audit ENF.2 process isolation",
        ],
    )

    rc, stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
    assert rc == 0, f"exit={rc} stderr={stderr}"
    assert "PASS" in stdout


def test_more_than_three_dispatches_are_rejected(tmp_path: Path) -> None:
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
            "fix SEC.1 controls",
            "implement NF.5 coverage",
            "audit ENF.2 isolation",
            "document REL.1 release",
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
    assert rc == 1
    assert "at most 3" in stderr


def test_single_concrete_continuation_is_allowed(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "repo"
    tasks_dir.mkdir()
    _write_tasks(
        tasks_dir / "TASKS.md",
        ["- [ ] SEC.1 — fix controls | status: in_progress"],
    )

    wave_file = tmp_path / "wave.json"
    _write_wave(wave_file, ["fix SEC.1 controls"])

    rc, stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
    assert rc == 0, f"exit={rc} stderr={stderr}"
    assert "PASS" in stdout


def test_multi_prompt_wave_requires_multiple_topics(tmp_path: Path) -> None:
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
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
    assert rc == 1
    assert "TOPIC DIVERSITY" in stderr


def test_one_topic_cannot_consume_an_entire_multi_prompt_wave(tmp_path: Path) -> None:
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
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
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
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
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
            "fix SEC.1 extras",
            "implement NF.5 coverage",
            "audit ENF.2 isolation",
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
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
    rc, _stdout, _stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
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

    rc, _stdout, _stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
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

    rc, _stdout, _stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
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

    rc, _stdout, _stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
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
        ],
    )

    rc, _stdout, stderr = _run(wave_file, tasks_dir, tasks_dir / "TASKS.md")
    assert rc == 0, f"exit={rc} stderr={stderr}"
