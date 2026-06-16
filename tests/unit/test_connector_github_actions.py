"""Unit tests for the GitHub Actions observability connector.

All HTTP is exercised through an INJECTED mock transport — no real network is
ever touched. The transport contract is ``http_get(url, headers) -> (status, json)``.
"""

from __future__ import annotations

import pytest

from general_ludd.connectors.github_actions import GitHubActionsSource

# ---------------------------------------------------------------------------
# Canned payloads
# ---------------------------------------------------------------------------

_RUNS_PAYLOAD = {
    "total_count": 3,
    "workflow_runs": [
        {
            "id": 101,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "aaa111",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-06-12T10:00:00Z",
            "path": ".github/workflows/ci.yml",
        },
        {
            "id": 102,
            "name": "CI",
            "head_branch": "feature/x",
            "head_sha": "bbb222",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "updated_at": "2026-06-12T11:30:00Z",
            "path": ".github/workflows/ci.yml",
        },
        {
            "id": 103,
            "name": "Release",
            "head_branch": "main",
            "head_sha": "ccc333",
            "event": "push",
            "status": "in_progress",
            "conclusion": None,
            "updated_at": None,
            "path": ".github/workflows/release.yml",
        },
    ],
}

_JOBS_PAYLOAD = {
    "total_count": 1,
    "jobs": [
        {"id": 9001, "name": "build", "conclusion": "failure"},
    ],
}


class _MockTransport:
    """Records calls and returns canned (status, json) responses by URL substring."""

    def __init__(self, status: int = 200, *, runs=_RUNS_PAYLOAD, jobs=_JOBS_PAYLOAD):
        self.status = status
        self.runs = runs
        self.jobs = jobs
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, object]:
        self.calls.append((url, headers))
        if "/jobs" in url:
            return self.status, self.jobs
        return self.status, self.runs


def _make_source(transport=None, *, config_extra=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("GITHUB_TOKEN", "tok-secret")
    config = {"repo": "owner/name", "token_env": "GITHUB_TOKEN"}
    if config_extra:
        config.update(config_extra)
    return GitHubActionsSource(config, http_get=transport or _MockTransport())


# ---------------------------------------------------------------------------
# Contract / construction
# ---------------------------------------------------------------------------


def test_kind_class_attr_is_pipeline():
    assert GitHubActionsSource.KIND == "pipeline"


def test_name_attr_set(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    assert isinstance(src.name, str)
    assert "owner/name" in src.name


def test_default_base_url(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    assert src.base_url == "https://api.github.com"


def test_token_read_from_env_not_hardcoded(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok-xyz")
    transport = _MockTransport()
    src = GitHubActionsSource({"repo": "owner/name", "token_env": "GITHUB_TOKEN"}, http_get=transport)
    src.query({})
    # Authorization header must carry the env-sourced token.
    _, headers = transport.calls[0]
    assert headers.get("Authorization") == "Bearer tok-xyz"


# ---------------------------------------------------------------------------
# query() normalization
# ---------------------------------------------------------------------------


def test_query_normalizes_records(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    records = src.query({})
    assert isinstance(records, list)
    assert len(records) == 3

    first = records[0]
    assert set(first) == {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert first["kind"] == "pipeline"
    assert first["value"] is None
    assert first["source"] == src.name
    assert first["level_or_status"] == "success"
    assert "CI" in first["message"] and "main" in first["message"]
    assert first["labels"] == {
        "run_id": 101,
        "head_sha": "aaa111",
        "event": "push",
        "workflow": ".github/workflows/ci.yml",
    }
    assert first["raw"]["id"] == 101


def test_query_ts_parsed_to_epoch(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    records = src.query({})
    # 2026-06-12T10:00:00Z -> known epoch
    assert records[0]["ts"] == pytest.approx(1781258400.0)
    # null updated_at -> None
    assert records[2]["ts"] is None


def test_query_status_mapping_failure(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    records = src.query({})
    assert records[1]["level_or_status"] == "failure"
    # in_progress run has no conclusion -> falls back to status
    assert records[2]["level_or_status"] == "in_progress"


def test_query_url_targets_repo_runs(monkeypatch):
    transport = _MockTransport()
    src = _make_source(transport=transport, monkeypatch=monkeypatch)
    src.query({})
    url, _ = transport.calls[0]
    assert url.startswith("https://api.github.com/repos/owner/name/actions/runs")


# ---------------------------------------------------------------------------
# spec filters
# ---------------------------------------------------------------------------


def test_query_limit_filter(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    records = src.query({"limit": 1})
    assert len(records) == 1


def test_query_branch_filter(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    records = src.query({"branch": "main"})
    assert {r["labels"]["head_sha"] for r in records} == {"aaa111", "ccc333"}


def test_query_status_filter(monkeypatch):
    src = _make_source(monkeypatch=monkeypatch)
    records = src.query({"status": "failure"})
    assert len(records) == 1
    assert records[0]["labels"]["run_id"] == 102


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok_on_200(monkeypatch):
    src = _make_source(transport=_MockTransport(status=200), monkeypatch=monkeypatch)
    h = src.health()
    assert h["ok"] is True
    assert isinstance(h["detail"], str)


def test_health_not_ok_on_401(monkeypatch):
    src = _make_source(transport=_MockTransport(status=401), monkeypatch=monkeypatch)
    h = src.health()
    assert h["ok"] is False
    assert "401" in h["detail"]


def test_health_never_raises(monkeypatch):
    def _boom(url, headers):
        raise RuntimeError("network down")

    src = _make_source(transport=_boom, monkeypatch=monkeypatch)
    h = src.health()
    assert h["ok"] is False
    assert isinstance(h["detail"], str)


# ---------------------------------------------------------------------------
# fetch_failed_logs stub
# ---------------------------------------------------------------------------


def test_fetch_failed_logs_returns_jobs(monkeypatch):
    transport = _MockTransport()
    src = _make_source(transport=transport, monkeypatch=monkeypatch)
    jobs = src.fetch_failed_logs(102)
    assert isinstance(jobs, list)
    assert jobs[0]["id"] == 9001
    url, _ = transport.calls[-1]
    assert url.endswith("/actions/runs/102/jobs")


# ---------------------------------------------------------------------------
# SSRF guard — literal-host block (NO DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "http://localhost",
        "http://10.0.0.5",
        "http://172.16.4.4",
        "http://192.168.1.1",
        "http://169.254.169.254",
        "http://metadata.google.internal",
        "http://[::1]",
    ],
)
def test_internal_base_url_rejected(monkeypatch, bad_url):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    with pytest.raises(ValueError):
        GitHubActionsSource(
            {"repo": "owner/name", "base_url": bad_url, "token_env": "GITHUB_TOKEN"},
            http_get=_MockTransport(),
        )


def test_public_base_url_allowed(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    src = GitHubActionsSource(
        {"repo": "owner/name", "base_url": "https://ghe.example.com", "token_env": "GITHUB_TOKEN"},
        http_get=_MockTransport(),
    )
    assert src.base_url == "https://ghe.example.com"
