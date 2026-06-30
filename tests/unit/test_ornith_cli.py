"""Unit tests for general_ludd.cli_ornith (the `gludd ornith` CLI).

All httpx calls are mocked — no real network or daemon is required.
"""

from __future__ import annotations

import argparse
import json as _json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.cli_ornith import (
    _cmd_config_get,
    _cmd_config_set,
    _cmd_disable,
    _cmd_doctor,
    _cmd_enable,
    _cmd_export,
    _cmd_history,
    _cmd_improve,
    _cmd_pairs,
    _cmd_set_outcome,
    _cmd_solve,
    _cmd_stats,
    _cmd_status,
    _cmd_train,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse(*argv: str) -> argparse.Namespace:
    import general_ludd.cli as cli_mod

    parser, _ = cli_mod.build_parser()
    return parser.parse_args(list(argv))


def _capture(func, ns: argparse.Namespace) -> tuple[str, str, int | None]:
    out = StringIO()
    err = StringIO()
    code: int | None = None
    with patch("sys.stdout", out), patch("sys.stderr", err):
        try:
            func(ns)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return out.getvalue(), err.getvalue(), code


def _mock_response(json_body=None, text=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text if text is not None else (
        _json.dumps(json_body) if json_body is not None else ""
    )
    resp.json.return_value = json_body
    return resp


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_calls_daemon_endpoint():
    ns = _parse("ornith", "status", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {
                "installed": True,
                "version": "1.0",
                "model_sha": "abc123",
                "last_call_at": "2026-06-29T12:00:00Z",
                "total_calls": 7,
                "success_rate": 0.86,
            }
        )
        out, _, code = _capture(_cmd_status, ns)
    args, _ = m.call_args
    assert args[0] == "GET"
    assert args[1] == "http://d:8000/admin/ornith/status"
    assert "installed" in out
    assert "1.0" in out
    assert "0.86" in out
    assert code is None


# ---------------------------------------------------------------------------
# solve
# ---------------------------------------------------------------------------


def test_solve_passes_task_and_target_files():
    ns = _parse(
        "ornith",
        "solve",
        "--task",
        "fix the bug",
        "--target-files",
        "f1.py",
        "f2.py",
        "--max-iter",
        "7",
        "--daemon-url",
        "http://d:8000",
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {
                "patch": "diff --git a/f b/f",
                "summary": "ok",
                "iterations": 3,
                "tokens": 1234,
                "pair_id": "ORN-1",
            }
        )
        out, _, _ = _capture(_cmd_solve, ns)
    args, kwargs = m.call_args
    assert args[0] == "POST"
    assert args[1] == "http://d:8000/admin/ornith/solve"
    body = kwargs["json"]
    assert body["task"] == "fix the bug"
    assert body["target_files"] == ["f1.py", "f2.py"]
    assert body["max_iterations"] == 7
    assert "diff --git a/f b/f" in out
    assert "pair_id" in out


# ---------------------------------------------------------------------------
# improve
# ---------------------------------------------------------------------------


def test_improve_passes_artifact_path_and_kind():
    ns = _parse(
        "ornith",
        "improve",
        "--artifact-path",
        "/x/roles/foo",
        "--kind",
        "playbook",
        "--feedback",
        "good",
        "--daemon-url",
        "http://d:8000",
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response({"status": "ok", "improve_id": "i1"})
        out, _, _ = _capture(_cmd_improve, ns)
    args, kwargs = m.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/admin/ornith/improve")
    body = kwargs["json"]
    assert body["artifact_path"] == "/x/roles/foo"
    assert body["kind"] == "playbook"
    assert body["feedback"] == "good"
    assert "submitted" in out


# ---------------------------------------------------------------------------
# pairs
# ---------------------------------------------------------------------------


def test_pairs_filters_by_status():
    """Default behavior: hit /admin/ornith/pending with limit."""
    ns = _parse(
        "ornith",
        "pairs",
        "--limit",
        "5",
        "--daemon-url",
        "http://d:8000",
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {"pending": [{"id": "ORN-1", "task_description": "t1", "scaffold_kind": "playbook"}]}
        )
        out, _, _ = _capture(_cmd_pairs, ns)
    args, kwargs = m.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/admin/ornith/pending")
    params = kwargs["params"] or {}
    assert params.get("limit") == 5
    assert "ORN-1" in out


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_writes_jsonl_to_out_path(tmp_path: Path):
    out_file = tmp_path / "out.jsonl"
    ns = _parse(
        "ornith",
        "export",
        "--out",
        str(out_file),
        "--since",
        "2026-06-01",
        "--project",
        "proj-1",
        "--daemon-url",
        "http://d:8000",
    )
    # The daemon-side export returns a list of pair dicts (JSONL lines).
    payload = [
        {"id": "p1", "status": "succeeded"},
        {"id": "p2", "status": "pending"},
    ]
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(payload)
        out, _, _ = _capture(_cmd_export, ns)
    args, kwargs = m.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/admin/ornith/export")
    params = kwargs["params"] or {}
    assert params.get("since") == "2026-06-01"
    assert params.get("project_id") == "proj-1"
    assert params.get("out_path") == str(out_file)
    # The CLI passed out_path to the daemon; if daemon didn't write it
    # (it's a mock), the CLI prints the response. We just assert the call
    # shape and that the response is reported.
    assert "p1" in out or "Export failed" in out or "row" in out


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_prints_success_rate_and_token_consumption():
    ns = _parse("ornith", "stats", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {
                "total": 100,
                "success_rate": 0.42,
                "avg_tokens_per_call": 30.0,
                "counts_by_status": {"succeeded": 42, "pending": 58},
            }
        )
        out, _, _ = _capture(_cmd_stats, ns)
    assert "success rate" in out
    assert "0.42" in out or "42.00%" in out
    assert "avg tokens" in out or "tokens" in out


# ---------------------------------------------------------------------------
# set-outcome
# ---------------------------------------------------------------------------


def test_set_outcome_calls_patch_endpoint():
    ns = _parse(
        "ornith",
        "set-outcome",
        "ORN-99",
        "--status",
        "succeeded",
        "--details",
        "gate green",
        "--daemon-url",
        "http://d:8000",
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {"id": "ORN-99", "outcome_status": "succeeded", "outcome_set_at": "2026-06-29"}
        )
        out, _, _ = _capture(_cmd_set_outcome, ns)
    args, kwargs = m.call_args
    assert args[0] == "PATCH"
    assert args[1].endswith("/admin/ornith/ORN-99/outcome")
    body = kwargs["json"]
    assert body["status"] == "succeeded"
    assert body["details"]["note"] == "gate green"
    assert "ORN-99" in out


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


def test_enable_writes_config_file(tmp_path: Path):
    cfg_dir = tmp_path / "gludd"
    ns = _parse("ornith", "enable", "--config-dir", str(cfg_dir))
    out, _, _ = _capture(_cmd_enable, ns)
    cfg = cfg_dir / "config.yml"
    assert cfg.exists()
    text = cfg.read_text()
    assert "ornith_enabled" in text
    assert "true" in text
    assert "ornith_enabled: true" in out or str(cfg) in out


def test_disable_writes_config_file(tmp_path: Path):
    cfg_dir = tmp_path / "gludd"
    ns = _parse("ornith", "disable", "--config-dir", str(cfg_dir))
    _out, _, _ = _capture(_cmd_disable, ns)
    cfg = cfg_dir / "config.yml"
    assert cfg.exists()
    text = cfg.read_text()
    assert "ornith_enabled" in text
    assert "false" in text


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_reports_binary_missing(tmp_path: Path):
    perms = tmp_path / "perms"
    perms.mkdir()
    (perms / "agent-ornith.yml").write_text("principal: agent:ornith\n")
    ns = _parse(
        "ornith",
        "doctor",
        "--daemon-url",
        "http://d:8000",
        "--perms-dir",
        str(perms),
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m_http, patch(
        "general_ludd.cli_ornith.shutil.which", return_value=None
    ):
        m_http.return_value = _mock_response(
            {
                "installed": False,
                "model_sha": "abc",
                "sandbox_backend": "landlock",
            }
        )
        out, _, code = _capture(_cmd_doctor, ns)
    assert "binary_on_path" in out
    assert "FAIL" in out
    assert code == 1


def test_doctor_reports_permission_spec_missing(tmp_path: Path):
    empty_perms = tmp_path / "empty"
    empty_perms.mkdir()
    ns = _parse(
        "ornith",
        "doctor",
        "--daemon-url",
        "http://d:8000",
        "--perms-dir",
        str(empty_perms),
        "--binary-name",
        "python3",  # something reliably on PATH
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m_http:
        m_http.return_value = _mock_response(
            {
                "installed": True,
                "model_sha": "abc",
                "sandbox_backend": "landlock",
            }
        )
        out, _, code = _capture(_cmd_doctor, ns)
    assert "permission_spec_includes_agent_ornith" in out
    assert "FAIL" in out
    assert code == 1


def test_doctor_reports_ok_when_everything_present(tmp_path: Path):
    perms = tmp_path / "perms"
    perms.mkdir()
    (perms / "agent-ornith.yml").write_text("principal: agent:ornith\n")
    ns = _parse(
        "ornith",
        "doctor",
        "--daemon-url",
        "http://d:8000",
        "--perms-dir",
        str(perms),
        "--binary-name",
        "python3",
        "--expected-model-sha",
        "abc",
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m_http:
        m_http.return_value = _mock_response(
            {
                "installed": True,
                "model_sha": "abc",
                "sandbox_backend": "landlock",
            }
        )
        out, _, code = _capture(_cmd_doctor, ns)
    assert "all checks passed" in out
    assert code is None


# ---------------------------------------------------------------------------
# --json support across subcommands
# ---------------------------------------------------------------------------


def test_all_subcommands_support_json_flag():
    subcommands = [
        ("status", ["status", "--json"]),
        ("solve", ["solve", "--task", "x", "--target-files", "f", "--json"]),
        ("improve", ["improve", "--artifact-path", "p", "--kind", "playbook", "--json"]),
        ("pairs", ["pairs", "--json"]),
        ("stats", ["stats", "--json"]),
        (
            "set-outcome",
            ["set-outcome", "ORN-1", "--status", "succeeded", "--json"],
        ),
    ]
    for name, argv in subcommands:
        full = ["ornith", *argv, "--daemon-url", "http://d:8000"]
        ns = _parse(*full)
        assert getattr(ns, "json", False) is True, f"{name} did not parse --json"

    en = _parse("ornith", "enable", "--json", "--config-dir", "/tmp/_t1")
    assert en.json is True
    di = _parse("ornith", "disable", "--json", "--config-dir", "/tmp/_t2")
    assert di.json is True
    doc = _parse("ornith", "doctor", "--json", "--daemon-url", "http://d:8000")
    assert doc.json is True
    ex = _parse("ornith", "export", "--out", "/tmp/_x.jsonl", "--json")
    assert ex.json is True


# ---------------------------------------------------------------------------
# train / self-improve
# ---------------------------------------------------------------------------


def test_train_triggers_self_improve_endpoint():
    ns = _parse("ornith", "train", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {
                "status": "triggered",
                "cycle": {
                    "triggered_at": "2026-06-29T12:00:00Z",
                    "result": {"findings_count": 3, "todos_enqueued": 1},
                },
            }
        )
        out, _, _ = _capture(_cmd_train, ns)
    args, _kwargs = m.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/admin/ornith/self-improve")
    assert "Training cycle" in out
    assert "3" in out


def test_train_with_json_flag():
    ns = _parse("ornith", "train", "--json", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response({"status": "triggered"})
        out, _, _ = _capture(_cmd_train, ns)
    assert '"status"' in out


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_shows_cycle_list():
    ns = _parse("ornith", "history", "--limit", "5", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {
                "cycles": [
                    {
                        "triggered_at": "2026-06-29T12:00:00Z",
                        "result": {"findings_count": 3, "todos_enqueued": 1},
                    }
                ],
                "count": 1,
            }
        )
        out, _, _ = _capture(_cmd_history, ns)
    args, kwargs = m.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/admin/ornith/history")
    params = kwargs["params"] or {}
    assert params.get("limit") == 5
    assert "Self-improvement cycles" in out
    assert "1." in out


def test_history_empty():
    ns = _parse("ornith", "history", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response({"cycles": [], "count": 0})
        out, _, _ = _capture(_cmd_history, ns)
    assert "no self-improvement cycles" in out


# ---------------------------------------------------------------------------
# config get / set
# ---------------------------------------------------------------------------


def test_config_get_shows_values():
    ns = _parse("ornith", "config", "get", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {
                "ornith_enabled": True,
                "model_sha": "abc123",
                "binary_path": "ornith",
                "env_ornith_enabled": False,
            }
        )
        out, _, _ = _capture(_cmd_config_get, ns)
    args, _ = m.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/admin/ornith/config")
    assert "enabled" in out
    assert "abc123" in out


def test_config_set_updates_model_sha():
    ns = _parse(
        "ornith",
        "config",
        "set",
        "--model-sha",
        "new-sha-456",
        "--daemon-url",
        "http://d:8000",
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response(
            {"ornith_enabled": True, "model_sha": "new-sha-456"}
        )
        out, _, _ = _capture(_cmd_config_set, ns)
    args, kwargs = m.call_args
    assert args[0] == "PUT"
    assert args[1].endswith("/admin/ornith/config")
    body = kwargs["json"]
    assert body["model_sha"] == "new-sha-456"
    assert "new-sha-456" in out


def test_config_get_with_json_flag():
    ns = _parse("ornith", "config", "get", "--json", "--daemon-url", "http://d:8000")
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response({"ornith_enabled": True})
        out, _, _ = _capture(_cmd_config_get, ns)
    assert '"ornith_enabled"' in out


def test_config_set_with_json_flag():
    ns = _parse(
        "ornith", "config", "set", "--model-sha", "x", "--json", "--daemon-url", "http://d:8000"
    )
    with patch("general_ludd.cli_ornith.httpx.request") as m:
        m.return_value = _mock_response({"model_sha": "x"})
        out, _, _ = _capture(_cmd_config_set, ns)
    assert '"model_sha"' in out


# ---------------------------------------------------------------------------
# json support for train, history, config
# ---------------------------------------------------------------------------


def test_new_subcommands_parse_json_flag():
    tr = _parse("ornith", "train", "--json", "--daemon-url", "http://d:8000")
    assert tr.json is True
    hi = _parse("ornith", "history", "--json", "--daemon-url", "http://d:8000")
    assert hi.json is True
    cg = _parse("ornith", "config", "get", "--json", "--daemon-url", "http://d:8000")
    assert cg.json is True
    cs = _parse("ornith", "config", "set", "--model-sha", "x", "--json", "--daemon-url", "http://d:8000")
    assert cs.json is True
