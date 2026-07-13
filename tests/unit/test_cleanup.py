"""Unit tests for sandbox cleanup manager."""

from __future__ import annotations

import subprocess
import time
from unittest.mock import patch

from general_ludd.sandbox.cleanup import CleanupManager, CleanupRecord


class TestCleanupRecord:
    def test_creation(self) -> None:
        record = CleanupRecord(resource_type="docker_container", resource_id="abc123", cleaned_at=time.time(), reason="explicit")
        assert record.resource_type == "docker_container"
        assert record.resource_id == "abc123"
        assert record.reason == "explicit"
        assert record.success is True

    def test_failed_record(self) -> None:
        record = CleanupRecord(resource_type="kubernetes_pod", resource_id="pod-xyz", cleaned_at=time.time(), reason="explicit", success=False)
        assert record.success is False


class TestCleanupManager:
    def test_track_resource(self) -> None:
        mgr = CleanupManager()
        mgr.track("docker_container", "abc123")
        assert mgr.pending_count() == 1

    def test_track_multiple(self) -> None:
        mgr = CleanupManager()
        mgr.track("docker_container", "a")
        mgr.track("docker_container", "b")
        mgr.track("kubernetes_pod", "c")
        assert mgr.pending_count() == 3

    def test_cleanup_resource_docker(self) -> None:
        mgr = CleanupManager()
        mgr.track("docker_container", "abc")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            result = mgr.cleanup_resource("docker_container", "abc")
            assert result is True
            assert mgr.history_count() == 1
            assert mgr.pending_count() == 0

    def test_cleanup_resource_unknown_type(self) -> None:
        mgr = CleanupManager()
        mgr.track("unknown_type", "xyz")
        assert mgr.cleanup_resource("unknown_type", "xyz") is False

    def test_cleanup_all(self) -> None:
        mgr = CleanupManager()
        mgr.track("docker_container", "a")
        mgr.track("docker_container", "b")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            count = mgr.cleanup_all()
            assert count == 2
            assert mgr.pending_count() == 0

    def test_cleanup_all_empty(self) -> None:
        mgr = CleanupManager()
        assert mgr.cleanup_all() == 0

    def test_history_count(self) -> None:
        mgr = CleanupManager()
        assert mgr.history_count() == 0
        mgr.track("docker_container", "a")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            mgr.cleanup_resource("docker_container", "a")
        assert mgr.history_count() == 1

    def test_last_cleanup(self) -> None:
        mgr = CleanupManager()
        assert mgr.last_cleanup() is None
        mgr.track("docker_container", "last")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            mgr.cleanup_resource("docker_container", "last")
        record = mgr.last_cleanup()
        assert record is not None
        assert record.resource_id == "last"

    def test_cleanup_docker_containers(self) -> None:
        mgr = CleanupManager()
        with patch.object(subprocess, "run") as mock_run:
            def run_side_effect(*args, **kwargs):
                cmd = args[0]
                if "ps" in cmd:
                    return subprocess.CompletedProcess(args=[], returncode=0, stdout="cont1\ncont2\n", stderr="")
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            mock_run.side_effect = run_side_effect
            count = mgr.cleanup_docker_containers()
            assert count == 2
            assert mgr.history_count() == 2

    def test_cleanup_docker_containers_empty(self) -> None:
        mgr = CleanupManager()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            count = mgr.cleanup_docker_containers()
            assert count == 0

    def test_cleanup_kubernetes_resources(self) -> None:
        mgr = CleanupManager()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="pod/a deleted\npod/b deleted\n", stderr="")
            count = mgr.cleanup_kubernetes_resources()
            assert count == 2

    def test_cleanup_kubernetes_resources_failure(self) -> None:
        mgr = CleanupManager()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
            count = mgr.cleanup_kubernetes_resources()
            assert count == 0

    def test_track_duplicate(self) -> None:
        mgr = CleanupManager()
        mgr.track("docker_container", "same")
        mgr.track("docker_container", "same")
        assert mgr.pending_count() == 1

    def test_cleanup_failed_marks_history(self) -> None:
        mgr = CleanupManager()
        mgr.track("docker_container", "fail-me")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("docker", 1)
            result = mgr.cleanup_resource("docker_container", "fail-me")
            assert result is False
            record = mgr.last_cleanup()
            assert record is not None
            assert record.success is False
            assert record.resource_id == "fail-me"
