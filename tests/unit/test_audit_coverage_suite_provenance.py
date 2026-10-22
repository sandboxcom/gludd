"""Suite provenance through the real audit parser, CLI and native runner."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Literal, Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/audit_coverage.py"
STAMP = "20261022T003000Z"


class AuditAPI(Protocol):
    def parse_coverage_json(
        self, json_path: str, threshold: float, source_path: str,
        per_file_threshold: float = 75.0,
    ) -> tuple[dict[str, object], list[str], bool]:
        """Evaluate generic JSON coverage without inferring the test suite."""
        ...

    def parse_coverage_xml(
        self, xml_path: str, threshold: float, source_path: str,
        per_file_threshold: float = 75.0,
    ) -> tuple[dict[str, object], list[str], bool]:
        """Evaluate generic Cobertura coverage without inferring the test suite."""
        ...

    def main(self) -> None:
        """Run the real CLI selection and evidence writer."""
        ...


class FixedClock:
    @staticmethod
    def now(tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 22, 0, 30, tzinfo=UTC).astimezone(tz)


def _payload(source: Path, *, covered: int = 4, context: str = "") -> dict[str, object]:
    return {
        "meta": {"branch_coverage": True, "show_contexts": True},
        "e2e_branch_coverage": 100.0,
        "e2e_branch_totals": {"scope": "tests/e2e", "coverage_percent": 100.0},
        "files": {
            str(source / "branchy.py"): {
                "summary": {
                    "num_statements": 2, "covered_lines": 2,
                    "num_branches": 4, "covered_branches": covered,
                },
                "contexts": {"1": [context]},
            },
        },
    }


@dataclass
class NativeRunner:
    root: Path
    source: Path
    covered: int = 4
    pytest_status: int = 0
    report_status: int = 0
    timeout: bool = False
    malformed_stale: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(argv))
        assert kwargs["cwd"] == self.root
        assert isinstance(kwargs["env"], dict)
        raw = self.root / ".gate-logs" / f"coverage-data-{STAMP}.json"
        # A pre-existing artifact must not survive a failed native attempt as
        # current measured evidence. Inject it at the real subprocess boundary.
        raw.write_text(
            "{" if self.malformed_stale else json.dumps(_payload(self.source, covered=self.covered)),
            encoding="utf-8",
        )
        if argv[1:3] == ["-m", "pytest"]:
            assert "--cov-branch" in argv and "--cov-context=test" in argv
            assert "--cov-append" in argv and "--cov-fail-under=0" in argv
            assert argv[argv.index("-n") + 1] == "2"
            assert Path(argv[3]).is_relative_to(self.root / "tests/e2e")
            if self.timeout:
                raise subprocess.TimeoutExpired(argv, 1)
            return subprocess.CompletedProcess(argv, self.pytest_status)
        assert argv[1:4] == ["-m", "coverage", "json"]
        assert "--show-contexts" in argv
        if self.report_status == 0:
            Path(argv[-1]).write_text(
                json.dumps(_payload(self.source, covered=self.covered)), encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, self.report_status)


@dataclass
class AuditHarness:
    api: AuditAPI
    root: Path
    source: Path
    runner: NativeRunner
    monkeypatch: pytest.MonkeyPatch

    def invoke(self, *arguments: str) -> tuple[int, dict[str, object]]:
        output = self.root / "result.json"
        self.monkeypatch.setattr(
            "sys.argv", ["audit_coverage.py", f"--source={self.source}", f"--json-out={output}", *arguments],
        )
        with pytest.raises(SystemExit) as result:
            self.api.main()
        assert isinstance(result.value.code, int)
        report = json.loads(output.read_text(encoding="utf-8"))
        assert isinstance(report, dict)
        return result.value.code, report


@pytest.fixture
def audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AuditHarness:
    root = tmp_path / "workspace"
    source = root / "src/general_ludd"
    source.mkdir(parents=True)
    for name in ("test_first.py", "nested/test_second.py"):
        path = root / "tests/e2e" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_synthetic():\n    assert True\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("audit_suite_provenance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(root / "scripts/audit_coverage.py"))
    monkeypatch.setattr(module, "datetime", FixedClock)
    monkeypatch.setenv("GLUDD_PROJECT_NAMESPACE", "gludd-audit-suite-fixture")
    runner = NativeRunner(root, source)
    monkeypatch.setattr(module.subprocess, "run", runner)
    return AuditHarness(cast(AuditAPI, module), root, source, runner, monkeypatch)


def _assert_generic(report: dict[str, object]) -> None:
    assert report["line_coverage"] == 100.0
    assert report["branch_coverage"] == 100.0
    assert report["total_branches"] == report["covered_branches"] == 4
    assert report["threshold"] == report["branch_threshold"] == 85.0
    assert report["per_file_thresholds"] == {"line": 75.0, "branch": 75.0}
    assert "e2e_branch_coverage" not in report
    assert "e2e_branch_totals" not in report


@pytest.mark.parametrize("suite_context", ["", "tests/unit/test_unit.py|run", "tests/e2e/test_named.py|run"])
def test_imported_json_context_never_proves_e2e_execution(
    audit: AuditHarness, suite_context: str,
) -> None:
    path = audit.root / "input.json"
    path.write_text(json.dumps(_payload(audit.source, context=suite_context)), encoding="utf-8")
    report, under, passed = audit.api.parse_coverage_json(str(path), 85, str(audit.source))
    _assert_generic(report)
    assert under == [] and passed
    assert audit.runner.calls == []


def _xml(source: Path) -> str:
    return f"""<coverage><packages><package name="fixture"><classes>
<class filename="{source / 'branchy.py'}"><lines>
<line number="1" hits="1" branch="true" condition-coverage="100% (4/4)"/>
<line number="2" hits="1"/>
</lines></class></classes></package></packages></coverage>"""


def test_imported_cobertura_never_proves_e2e_execution(audit: AuditHarness) -> None:
    path = audit.root / "input.xml"
    path.write_text(_xml(audit.source), encoding="utf-8")
    report, under, passed = audit.api.parse_coverage_xml(str(path), 85, str(audit.source))
    _assert_generic(report)
    assert under == [] and passed
    assert audit.runner.calls == []


@pytest.mark.parametrize("input_format", ["json", "xml"])
def test_imported_cli_does_not_launch_or_claim_native_e2e(
    audit: AuditHarness, input_format: Literal["json", "xml"],
) -> None:
    path = audit.root / f"input.{input_format}"
    path.write_text(
        json.dumps(_payload(audit.source)) if input_format == "json" else _xml(audit.source),
        encoding="utf-8",
    )
    original = path.read_bytes()
    status, report = audit.invoke(f"--{input_format}-file={path}")
    assert status == 0 and report["passed"] is True
    _assert_generic(report)
    assert path.read_bytes() == original
    assert report["shards"] == []
    assert audit.runner.calls == []


@pytest.mark.parametrize("covered_branches", [4, 3], ids=["full", "below-aggregate-floor"])
def test_native_completed_suite_owns_e2e_measurement(
    audit: AuditHarness, covered_branches: int,
) -> None:
    audit.runner.covered = covered_branches
    status, report = audit.invoke()
    percent = 100.0 * covered_branches / 4
    assert status == (0 if covered_branches == 4 else 1)
    assert report["passed"] is (covered_branches == 4)
    assert report["e2e_branch_coverage"] == report["branch_coverage"] == percent
    assert report["e2e_branch_totals"] == {
        "scope": "tests/e2e", "total": 4, "covered": covered_branches,
        "missing": 4 - covered_branches, "coverage_percent": percent,
        "total_branches": 4, "covered_branches": covered_branches,
    }
    assert report["shards"] == [
        {"path": "tests/e2e/nested/test_second.py", "status": "passed", "returncode": 0},
        {"path": "tests/e2e/test_first.py", "status": "passed", "returncode": 0},
    ]
    progress_path = audit.root / ".gate-logs" / f"coverage-data-{STAMP}.json.progress.json"
    progress = json.loads(progress_path.read_text())
    assert progress["status"] == "completed" and progress["complete"] is True
    assert progress["counts"]["passed"] == 2
    assert len(audit.runner.calls) == 3
    assert not list(audit.root.glob(".coverage.audit.*"))


@pytest.mark.parametrize("stale_malformed", [False, True], ids=["old-percentages", "old-malformed"])
@pytest.mark.parametrize(
    ("pytest_code", "report_code", "native_timeout", "expected"),
    [(19, 0, False, 19), (0, 17, False, 17), (0, 0, True, 124)],
    ids=["pytest-failure", "coverage-json-failure", "pytest-timeout"],
)
def test_failed_native_attempt_never_reads_or_certifies_stale_measurements(
    audit: AuditHarness, capsys: pytest.CaptureFixture[str],
    stale_malformed: bool, pytest_code: int, report_code: int,
    native_timeout: bool, expected: int,
) -> None:
    audit.runner.pytest_status = pytest_code
    audit.runner.report_status = report_code
    audit.runner.timeout = native_timeout
    audit.runner.malformed_stale = stale_malformed
    status, report = audit.invoke()
    assert status == expected and report["passed"] is False
    assert report["pytest_exit_code"] == expected
    for key in ("line_coverage", "branch_coverage", "e2e_branch_coverage", "e2e_branch_totals"):
        assert key not in report
    progress_path = audit.root / ".gate-logs" / f"coverage-data-{STAMP}.json.progress.json"
    progress = json.loads(progress_path.read_text())
    assert progress["status"] == "failed" and progress["complete"] is False
    output = capsys.readouterr()
    assert "Coverage audit complete" not in output.out
    assert "Coverage audit failed" in output.err
    assert not list(audit.root.glob(".coverage.audit.*"))
