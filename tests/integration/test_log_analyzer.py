"""Integration tests for the log_analyzer Ansible role and Python module.

Tests role invocation via AnsibleRunnerAdapter with the FQCN
``general_ludd.operations.log_analyzer``, plus end-to-end behaviours
through the Python module: ingestion, clustering, COT logging, reports,
empty logs, and malformed logs.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.log_analyzer import analyze

_PLAYBOOK_NAME = "log_analyzer.yml"
_ROLE_TASKS = (
    Path(__file__).resolve().parent.parent.parent
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "operations"
    / "roles"
    / "log_analyzer"
    / "tasks"
    / "main.yml"
)


def _has_ansible() -> bool:
    return shutil.which("ansible-playbook") is not None


pytestmark = pytest.mark.skipif(
    not _has_ansible(), reason="ansible-playbook not installed"
)


def _run_role(log_glob: str, output_dir: str) -> dict[str, Any]:
    adapter = AnsibleRunnerAdapter()
    pb = Path(__file__).resolve().parent.parent.parent / "playbooks" / _PLAYBOOK_NAME
    if _PLAYBOOK_NAME not in adapter.list_playbooks():
        adapter.register_playbook(_PLAYBOOK_NAME, str(pb))
    log_path = Path(log_glob)
    return adapter.run_playbook(
        _PLAYBOOK_NAME,
        extravars={
            "log_analyzer_glob": log_glob,
            "log_analyzer_output_dir": output_dir,
            "log_analyzer_error_rate_threshold": "0.3",
            "log_analyzer_min_cluster_size": "2",
            # Keep the role's discovery pass inside the isolated fixture.  Its
            # operator defaults intentionally include host paths such as
            # /var/log/gludd, which need not exist on a test worker and make
            # ansible.builtin.find fail before the analyzer is invoked.
            "log_source_dirs": str(log_path.parent),
            "daemon_log_glob": log_path.name,
            "systemd_service_name": "",
        },
    )


class TestRoleInvocation:
    def test_role_timestamp_does_not_depend_on_gathered_facts(self) -> None:
        tasks = _ROLE_TASKS.read_text()
        assert "ansible_date_time" not in tasks
        assert "_log_analyzer_started_at" in tasks
        assert "now(utc=true)" in tasks

    def test_role_runs_successfully(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test.log").write_text("INFO everything ok\n")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "*.log"), str(out_dir))
        assert result.get("rc", 1) == 0, f"role failed: {result}"
        events = result.get("events", [])
        assert any(event.get("event") == "runner_on_ok" for event in events)
        assert not any(event.get("event") == "runner_on_failed" for event in events)

    def test_role_creates_output_directory(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test.log").write_text("INFO ok\n")
        out_dir = tmp_path / "output"
        _run_role(str(log_dir / "*.log"), str(out_dir))
        assert out_dir.is_dir()

    def test_explicit_paths_override_missing_legacy_defaults(
        self, tmp_path: Path
    ) -> None:
        """Explicit glob/output inputs must make a fresh Linux run self-contained."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test.log").write_text("INFO ok\n")
        out_dir = tmp_path / "output"
        missing_source = tmp_path / "missing-source"
        unused_artifact_dir = tmp_path / "unused" / "nested"
        adapter = AnsibleRunnerAdapter()
        pb = (
            Path(__file__).resolve().parent.parent.parent
            / "playbooks"
            / _PLAYBOOK_NAME
        )
        if _PLAYBOOK_NAME not in adapter.list_playbooks():
            adapter.register_playbook(_PLAYBOOK_NAME, str(pb))

        result = adapter.run_playbook(
            _PLAYBOOK_NAME,
            extravars={
                "log_source_dirs": str(missing_source),
                "artifact_dir": str(unused_artifact_dir),
                "log_analyzer_glob": str(log_dir / "*.log"),
                "log_analyzer_output_dir": str(out_dir),
                "log_analyzer_error_rate_threshold": "0.3",
                "log_analyzer_min_cluster_size": "2",
            },
        )

        assert result.get("rc", 1) == 0, f"role failed: {result}"
        assert (out_dir / "log_analysis_result.json").is_file()
        assert not unused_artifact_dir.exists()


class TestLogIngestion:
    def test_ingests_log_content(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        content = "ERROR [db] timeout\nINFO [api] served\n"
        (log_dir / "app.log").write_text(content)
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "app.log"), str(out_dir))
        assert result.get("rc", 1) == 0
        json_path = out_dir / "log_analysis_result.json"
        assert json_path.exists(), f"missing result JSON, got: {sorted(out_dir.iterdir())}"
        data = json.loads(json_path.read_text())
        assert data["files_analysed"] >= 1
        assert data["total_lines"] >= 2


class TestErrorClustering:
    def test_clusters_repeated_errors(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        lines = ["ERROR [db] connection timeout\n" for _ in range(10)]
        (log_dir / "errors.log").write_text("".join(lines))
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "errors.log"), str(out_dir))
        assert result.get("rc", 1) == 0
        json_path = out_dir / "log_analysis_result.json"
        if json_path.exists():
            data = json.loads(json_path.read_text())
            clusters = data.get("error_clusters", [])
            assert len(clusters) >= 1, f"expected clusters, got: {data}"

    def test_no_clusters_when_no_errors(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "clean.log").write_text("INFO ok\nINFO ok\nINFO ok\n")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "clean.log"), str(out_dir))
        assert result.get("rc", 1) == 0
        json_path = out_dir / "log_analysis_result.json"
        if json_path.exists():
            data = json.loads(json_path.read_text())
            assert len(data.get("error_clusters", [])) == 0


class TestCOTLogging:
    def test_cot_log_written(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_text("ERROR [api] timeout\n")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "app.log"), str(out_dir))
        assert result.get("rc", 1) == 0
        cot = out_dir / "log_analysis_cot.log"
        assert cot.exists(), f"COT log missing; got: {sorted(out_dir.iterdir())}"
        legacy_cot = out_dir / "log_analyzer_cot.log"
        assert legacy_cot.exists(), "legacy COT artifact must remain available"
        assert legacy_cot.read_bytes() == cot.read_bytes()


class TestReportOutput:
    def test_json_report_generated(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_text("INFO ok\nERROR fail\n")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "app.log"), str(out_dir))
        assert result.get("rc", 1) == 0
        json_path = out_dir / "log_analysis_result.json"
        if json_path.exists():
            data = json.loads(json_path.read_text())
            assert "verdict" in data

    def test_markdown_report_generated(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_text("INFO ok\n")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "app.log"), str(out_dir))
        assert result.get("rc", 1) == 0
        md_path = out_dir / "log_analysis_report.md"
        assert md_path.exists(), f"MD report missing; got: {sorted(out_dir.iterdir())}"


class TestEmptyLogs:
    def test_handles_no_matching_files(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "*.log"), str(out_dir))
        assert result.get("rc", 1) == 0

    def test_handles_empty_log_file(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "empty.log").write_text("")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "empty.log"), str(out_dir))
        assert result.get("rc", 1) == 0


class TestMalformedLogs:
    def test_handles_corrupt_file(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "corrupt.log").write_bytes(b"\x00\x01\x02\xff\xfe\n")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "corrupt.log"), str(out_dir))
        assert result.get("rc", 1) == 0

    def test_handles_binary_garbage(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "binary.log").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "binary.log"), str(out_dir))
        assert result.get("rc", 1) == 0


class TestMultiFileIngestion:
    def test_ingests_multiple_log_files(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_text("ERROR [app] crash\n")
        (log_dir / "db.log").write_text("CRITICAL [db] corrupt\n")
        (log_dir / "sys.log").write_text("INFO all good\n")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = _run_role(str(log_dir / "*.log"), str(out_dir))
        assert result.get("rc", 1) == 0
        json_path = out_dir / "log_analysis_result.json"
        if json_path.exists():
            data = json.loads(json_path.read_text())
            assert data["files_analysed"] == 3


class TestPythonModuleIntegration:
    def test_empty_logs_graceful(self, tmp_path: Path) -> None:
        (tmp_path / "input.log").write_text("")
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out))
        assert result["verdict"] == "clean"
        assert result["files_analysed"] == 1

    def test_malformed_log_handled(self, tmp_path: Path) -> None:
        (tmp_path / "corrupt.log").write_text("\x00\x01\x02garbage\n")
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out))
        assert result["verdict"] == "clean"

    def test_repeated_errors_clustered(self, tmp_path: Path) -> None:
        lines = ["ERROR [db] connection timeout\n" for _ in range(20)]
        (tmp_path / "errors.log").write_text("".join(lines))
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out), error_threshold=0.0, min_cluster_size=3)
        assert result["verdict"] == "anomalies_detected"
        assert cast("int", result["cluster_count"]) >= 1
        clusters = cast("list[dict[str, object]]", result["error_clusters"])
        assert any(cluster["category"] == "db" for cluster in clusters)

    def test_cot_log_written_by_module(self, tmp_path: Path) -> None:
        (tmp_path / "app.log").write_text("ERROR [api] timeout\n")
        out = tmp_path / "output"
        out.mkdir()
        analyze(str(tmp_path), "*.log", str(out))
        assert (out / "log_analysis_cot.log").exists()

    def test_json_and_md_reports(self, tmp_path: Path) -> None:
        (tmp_path / "app.log").write_text("ERROR [db] timeout\nINFO ok\n")
        out = tmp_path / "output"
        out.mkdir()
        analyze(str(tmp_path), "*.log", str(out))
        json_path = out / "log_analysis_result.json"
        md_path = out / "log_analysis_report.md"
        assert json_path.exists()
        assert md_path.exists()
        data = json.loads(json_path.read_text())
        assert "verdict" in data
