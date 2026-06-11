from __future__ import annotations

import pytest

from general_ludd.worktree.core import WorktreeMonitor, WorktreeMonitorConfig


class TestWorktreeMonitorDaemonConstruction:
    def test_construct_with_config(self):
        config = WorktreeMonitorConfig(
            watch_paths=["/test/watch"],
            enabled=True,
        )
        monitor = WorktreeMonitor(config)
        assert monitor is not None

    def test_construct_with_config_and_todo_creator(self):
        config = WorktreeMonitorConfig(enabled=False)

        async def fake_create(data):
            return None

        monitor = WorktreeMonitor(config, todo_creator=fake_create)
        assert monitor is not None

    def test_construct_rejects_config_dir_kwarg(self):
        with pytest.raises(TypeError):
            WorktreeMonitor(config_dir="/some/path")
