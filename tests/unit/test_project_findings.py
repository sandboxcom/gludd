"""Tests for the SAST findings parser (semgrep/bandit JSON → structured findings)."""

from __future__ import annotations

from general_ludd.project_runner import ProjectCommandRunner, ProjectProfile
from general_ludd.project_runner.findings import parse_findings, summarize_findings

SEMGREP_JSON = (
    '{"results": [{"check_id": "python.lang.security.audit.dangerous-exec", '
    '"path": "src/x.py", "start": {"line": 42}, '
    '"extra": {"severity": "ERROR", "message": "exec is dangerous"}}]}'
)
BANDIT_JSON = (
    '{"results": [{"test_id": "B602", "filename": "src/y.py", "line_number": 10, '
    '"issue_severity": "HIGH", "issue_text": "subprocess with shell=True"}]}'
)


def test_parse_semgrep():
    f = parse_findings("semgrep", SEMGREP_JSON)
    assert len(f) == 1
    assert "ERROR" in f[0]
    assert "src/x.py:42" in f[0]
    assert "dangerous-exec" in f[0]
    assert "exec is dangerous" in f[0]


def test_parse_bandit():
    f = parse_findings("bandit", BANDIT_JSON)
    assert len(f) == 1
    assert "HIGH" in f[0]
    assert "src/y.py:10" in f[0]
    assert "B602" in f[0]


def test_unknown_tool_returns_empty():
    assert parse_findings("pytest", SEMGREP_JSON) == []
    assert parse_findings("", SEMGREP_JSON) == []


def test_malformed_or_empty_json_never_raises():
    assert parse_findings("semgrep", "{not valid json") == []
    assert parse_findings("semgrep", "") == []
    assert parse_findings("semgrep", "not json at all") == []
    assert parse_findings("bandit", "[]") == []  # not a dict


def test_truncated_json_is_soft():
    # The runner keeps only a stdout tail, so a large JSON doc may be truncated;
    # that must degrade to [] rather than crash.
    assert parse_findings("bandit", BANDIT_JSON[:40]) == []


def test_semgrep_missing_fields_soft():
    # Records missing start/extra still parse without raising.
    doc = '{"results": [{"check_id": "r1", "path": "a.py"}]}'
    f = parse_findings("semgrep", doc)
    assert len(f) == 1
    assert "a.py" in f[0] and "r1" in f[0]


def test_summarize_counts_by_severity():
    f = ["HIGH a:1 R — m", "HIGH b:2 R — m", "LOW c:3 R — m"]
    assert summarize_findings(f) == {"HIGH": 2, "LOW": 1}


def test_summarize_empty():
    assert summarize_findings([]) == {}


def test_runner_leaves_findings_empty_for_non_sast_command(tmp_path):
    # A normal (non-SAST) check must not get spurious findings.
    prof = ProjectProfile(commands={"test": "true"}, allowed_exec=["true"])
    res = ProjectCommandRunner(tmp_path, prof).run("test")
    assert res.findings == []
