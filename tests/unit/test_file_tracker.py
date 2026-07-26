"""Tests for FileChangeTracker — Ansible file-module change tracking."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.ansible.file_tracker import (
    FILE_MODULES,
    FileChangeTracker,
)

# ── helpers ────────────────────────────────────────────────────────────────

_REPO = Path("/fake/repo")
_HEAD_SHA = "abcdef1234567890abcdef1234567890abcdef12"

_PLAIN_NAMES = frozenset({
    "copy", "template", "file", "blockinfile", "lineinfile",
    "replace", "assemble", "ini_file",
})


def _fake_run_ok(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


def _make_run_side_effect(responses: list[tuple[tuple, MagicMock]]):
    idx = 0

    def _run(argv, **kwargs):
        nonlocal idx
        key = tuple(argv)
        if idx < len(responses):
            expected_key, result = responses[idx]
            if key == expected_key:
                idx += 1
                return result
        return _fake_run_ok()
    return _run


# ── 1. FILE_MODULES constant ───────────────────────────────────────────────


def test_file_modules_has_16_entries():
    """FILE_MODULES is a frozenset of 8 plain + 8 ansible.builtin names."""
    assert isinstance(FILE_MODULES, frozenset)
    assert len(FILE_MODULES) == 16

    for name in _PLAIN_NAMES:
        assert name in FILE_MODULES
        fq = f"ansible.builtin.{name}"
        assert fq in FILE_MODULES


def test_file_modules_rejects_unknown():
    """FILE_MODULES does not contain non-file module names."""
    assert "pip" not in FILE_MODULES
    assert "ansible.builtin.pip" not in FILE_MODULES
    assert "shell" not in FILE_MODULES


# ── 2. init captures git SHA ───────────────────────────────────────────────


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_init_captures_head_sha(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")

    tracker = FileChangeTracker(_REPO)

    assert tracker._git_sha_before == _HEAD_SHA


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_init_none_sha_when_head_missing(mock_run):
    mock_run.return_value = _fake_run_ok(returncode=1)

    tracker = FileChangeTracker(_REPO)

    assert tracker._git_sha_before is None


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_init_stores_repo_root(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")

    tracker = FileChangeTracker(_REPO)

    assert tracker._repo_root == _REPO


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_init_empty_file_events(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")

    tracker = FileChangeTracker(_REPO)

    assert tracker._file_events == []


# ── 3. event_handler filters only runner_on_ok ─────────────────────────────


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_rejects_non_runner_on_ok(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({"event": "runner_on_failed", "event_data": {}})
    tracker.event_handler({"event": "runner_on_skipped", "event_data": {}})
    tracker.event_handler({"event": "runner_on_start", "event_data": {}})

    assert tracker._file_events == []


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_rejects_missing_event_key(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({})
    tracker.event_handler({"event_data": {}})

    assert tracker._file_events == []


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_rejects_non_dict_outer_event(mock_run):
    """Malformed callback payloads must be ignored instead of raising."""
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler(None)
    tracker.event_handler([])

    assert tracker._file_events == []


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_rejects_non_string_task_name(mock_run):
    """Ansible callback task metadata can be malformed; reject it safely."""
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({"event": "runner_on_ok", "event_data": {"task": None}})
    tracker.event_handler({"event": "runner_on_ok", "event_data": {"task": 42}})

    assert tracker._file_events == []


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_rejects_non_dict_event_data(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({"event": "runner_on_ok", "event_data": None})

    assert tracker._file_events == []


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_rejects_non_dict_res(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {"task": "copy copy file", "res": None},
    })

    assert tracker._file_events == []


# ── 4. event_handler filters only FILE_MODULES actions ─────────────────────


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_rejects_non_file_module_task(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {
            "task": "ansible.builtin.pip Install packages",
            "host": "localhost",
            "res": {"dest": "/tmp/x", "changed": True},
        },
    })

    assert tracker._file_events == []


# ── 5. event_handler extracts dest, changed, checksum ──────────────────────


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_extracts_res_fields(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {
            "task": "copy Copy config file",
            "host": "web-01",
            "res": {
                "dest": "/etc/app/config.yml",
                "changed": True,
                "checksum": "abc123",
                "src": "/tmp/source.yml",
                "diff": {"before": "", "after": "..."},
                "extra": "ignored",
            },
        },
    })

    assert len(tracker._file_events) == 1
    entry = tracker._file_events[0]
    assert entry["task"] == "copy Copy config file"
    assert entry["host"] == "web-01"
    assert entry["dest"] == "/etc/app/config.yml"
    assert entry["changed"] is True
    assert entry["checksum"] == "abc123"
    assert entry["src"] == "/tmp/source.yml"
    assert "diff" in entry
    assert "extra" not in entry


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_handles_partial_res_fields(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {
            "task": "template Deploy template",
            "host": "db-01",
            "res": {"dest": "/etc/app/db.yml", "changed": False},
        },
    })

    entry = tracker._file_events[0]
    assert entry["dest"] == "/etc/app/db.yml"
    assert entry["changed"] is False
    assert "checksum" not in entry
    assert "src" not in entry


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_accepts_all_file_modules(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    for name in _PLAIN_NAMES:
        tracker._file_events.clear()
        tracker.event_handler({
            "event": "runner_on_ok",
            "event_data": {
                "task": f"{name} some action {name}",
                "host": "h",
                "res": {"dest": "/x", "changed": True},
            },
        })
        assert len(tracker._file_events) == 1, f"module {name} not captured"

    for name in _PLAIN_NAMES:
        tracker._file_events.clear()
        fq_name = f"ansible.builtin.{name}"
        tracker.event_handler({
            "event": "runner_on_ok",
            "event_data": {
                "task": f"{fq_name} some action {name}",
                "host": "h",
                "res": {"dest": "/x", "changed": True},
            },
        })
        assert len(tracker._file_events) == 1, f"module {fq_name} not captured"


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_event_handler_accumulates_multiple_events(mock_run):
    mock_run.return_value = _fake_run_ok(_HEAD_SHA + "\n")
    tracker = FileChangeTracker(_REPO)

    for i in range(3):
        tracker.event_handler({
            "event": "runner_on_ok",
            "event_data": {
                "task": "copy action",
                "host": f"host-{i}",
                "res": {"dest": f"/file-{i}", "changed": True},
            },
        })

    assert len(tracker._file_events) == 3


# ── 6. get_changed_files ───────────────────────────────────────────────────


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_get_changed_files_range_when_sha_known(mock_run):
    sha = _HEAD_SHA
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "diff", "--name-status", sha, "HEAD"), _fake_run_ok(
            "M\tfile_a.txt\nA\tfile_b.txt\n"
        )),
    ])

    tracker = FileChangeTracker(_REPO)
    result = tracker.get_changed_files()

    assert "file_a.txt" in result
    assert "file_b.txt" in result


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_get_changed_files_working_when_sha_none(mock_run):
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(returncode=1)),
        (("git", "diff", "--name-status"), _fake_run_ok(
            "D\tremoved.txt\n"
        )),
    ])

    tracker = FileChangeTracker(_REPO)
    result = tracker.get_changed_files()

    assert "removed.txt" in result


# ── 7. build_agent_context ─────────────────────────────────────────────────


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_build_agent_context_keys(mock_run):
    sha_before = _HEAD_SHA
    sha_after = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    diff_output = "diff --git a/x b/x\n..."

    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha_before + "\n")),
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha_after + "\n")),
        (("git", "diff", sha_before, "HEAD"), _fake_run_ok(diff_output)),
    ])

    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {
            "task": "copy action",
            "host": "h",
            "res": {"dest": "/x", "changed": True, "checksum": "c1"},
        },
    })

    ctx = tracker.build_agent_context()

    assert isinstance(ctx, dict)
    assert "playbook_summary" in ctx
    assert "git_state" in ctx
    assert "file_details" in ctx
    assert "git_diff" in ctx


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_build_agent_context_playbook_summary(mock_run):
    sha = _HEAD_SHA
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "diff", sha, "HEAD"), _fake_run_ok("")),
    ])

    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {
            "task": "copy a", "host": "h",
            "res": {"dest": "/a", "changed": True},
        },
    })
    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {
            "task": "template b", "host": "h",
            "res": {"dest": "/b", "changed": False},
        },
    })

    ctx = tracker.build_agent_context()

    summary = ctx["playbook_summary"]
    assert summary["file_events_count"] == 2
    assert len(summary["events"]) == 2
    assert summary["events"][0]["dest"] == "/a"
    assert summary["events"][1]["dest"] == "/b"


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_build_agent_context_git_state(mock_run):
    sha_before = "aaa"
    sha_after = "bbb"

    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha_before + "\n")),
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha_after + "\n")),
        (("git", "diff", sha_before, "HEAD"), _fake_run_ok("")),
    ])

    tracker = FileChangeTracker(_REPO)
    ctx = tracker.build_agent_context()

    gs = ctx["git_state"]
    assert gs["sha_before"] == sha_before
    assert gs["sha_after"] == sha_after


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_build_agent_context_git_state_none_shas(mock_run):
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(returncode=1)),
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(returncode=1)),
        (("git", "diff"), _fake_run_ok("")),
    ])

    tracker = FileChangeTracker(_REPO)
    ctx = tracker.build_agent_context()

    gs = ctx["git_state"]
    assert gs["sha_before"] is None
    assert gs["sha_after"] is None


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_build_agent_context_file_details(mock_run):
    sha = _HEAD_SHA
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "diff", sha, "HEAD"), _fake_run_ok("diff content")),
    ])

    tracker = FileChangeTracker(_REPO)

    tracker.event_handler({
        "event": "runner_on_ok",
        "event_data": {
            "task": "file Ensure perms",
            "host": "web",
            "res": {"dest": "/opt/app", "changed": True},
        },
    })

    ctx = tracker.build_agent_context()

    assert ctx["file_details"] == tracker._file_events
    assert isinstance(ctx["git_diff"], str)
    assert "diff content" in ctx["git_diff"]


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_build_agent_context_empty_events(mock_run):
    sha = _HEAD_SHA
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "diff", sha, "HEAD"), _fake_run_ok("")),
    ])

    tracker = FileChangeTracker(_REPO)
    ctx = tracker.build_agent_context()

    assert ctx["playbook_summary"]["file_events_count"] == 0
    assert ctx["playbook_summary"]["events"] == []
    assert ctx["file_details"] == []


# ── 8. get_git_diff ────────────────────────────────────────────────────────


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_get_git_diff_range(mock_run):
    sha = _HEAD_SHA
    diff_out = "diff --git a/x b/x\n--- a/x\n+++ b/x\n..."
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(sha + "\n")),
        (("git", "diff", sha, "HEAD"), _fake_run_ok(diff_out)),
    ])

    tracker = FileChangeTracker(_REPO)
    result = tracker.get_git_diff()

    assert result == diff_out


@patch("general_ludd.ansible.file_tracker.subprocess.run")
def test_get_git_diff_working(mock_run):
    diff_out = "diff --git a/y b/y\n..."
    mock_run.side_effect = _make_run_side_effect([
        (("git", "rev-parse", "--verify", "HEAD"), _fake_run_ok(returncode=1)),
        (("git", "diff"), _fake_run_ok(diff_out)),
    ])

    tracker = FileChangeTracker(_REPO)
    result = tracker.get_git_diff()

    assert result == diff_out
