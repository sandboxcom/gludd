"""Tests for Slurm CLI subcommands."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from general_ludd.cli import main


def _parse(args: list[str]):
    with patch.object(sys, "argv", ["gludd", *args]):
        main()


class TestSlurmStatusCLI:
    def test_slurm_status_available(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"available": True},
            )
            _parse(["slurm", "status"])
            out = capsys.readouterr().out
            assert "available" in out.lower() or "true" in out.lower()

    def test_slurm_status_not_available(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"available": False},
            )
            _parse(["slurm", "status"])
            out = capsys.readouterr().out
            assert "false" in out.lower() or "not" in out.lower()


class TestSlurmSubmitCLI:
    def test_slurm_submit(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"job_id": "42"},
            )
            _parse(["slurm", "submit", "--command", "echo hello"])
            out = capsys.readouterr().out
            assert "42" in out

    def test_slurm_submit_with_options(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"job_id": "99"},
            )
            _parse([
                "slurm", "submit",
                "--command", "train.py",
                "--job-name", "my-job",
                "--partition", "gpu",
            ])
            call_args = mock_post.call_args
            body = call_args.kwargs.get("json", call_args[1].get("json", {}) if len(call_args) > 1 else {})
            assert body.get("job_name") == "my-job"
            assert body.get("partition") == "gpu"

    def test_slurm_submit_error(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=503, text="Slurm not installed")
            with patch("sys.exit") as mock_exit:
                _parse(["slurm", "submit", "--command", "echo hello"])
                mock_exit.assert_called_with(1)


class TestSlurmJobStatusCLI:
    def test_slurm_job_status(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"job_id": "12345", "state": "COMPLETED", "exit_code": 0},
            )
            _parse(["slurm", "job", "12345"])
            out = capsys.readouterr().out
            assert "12345" in out
            assert "COMPLETED" in out

    def test_slurm_job_status_not_found(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404, text="not found")
            with patch("sys.exit") as mock_exit:
                _parse(["slurm", "job", "99999"])
                mock_exit.assert_called_with(1)


class TestSlurmCancelCLI:
    def test_slurm_cancel(self, capsys):
        with patch("httpx.delete") as mock_delete:
            mock_delete.return_value = MagicMock(
                status_code=200,
                json=lambda: {"cancelled": "12345"},
            )
            _parse(["slurm", "cancel", "12345"])
            out = capsys.readouterr().out
            assert "12345" in out

    def test_slurm_cancel_error(self):
        with patch("httpx.delete") as mock_delete:
            mock_delete.return_value = MagicMock(status_code=500, text="cancel failed")
            with patch("sys.exit") as mock_exit:
                _parse(["slurm", "cancel", "99999"])
                mock_exit.assert_called_with(1)


class TestSlurmListCLI:
    def test_slurm_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"jobs": [
                    {"job_id": "1", "state": "RUNNING"},
                    {"job_id": "2", "state": "COMPLETED"},
                ]},
            )
            _parse(["slurm", "list"])
            out = capsys.readouterr().out
            assert "1" in out
            assert "2" in out

    def test_slurm_list_empty(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"jobs": []},
            )
            _parse(["slurm", "list"])
            out = capsys.readouterr().out
            assert "no" in out.lower() or "0" in out
