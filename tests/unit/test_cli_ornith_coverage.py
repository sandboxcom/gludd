"""Branch coverage for Ornith CLI error and presentation paths."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import general_ludd.cli_ornith as ornith


class _Response:
    """Minimal httpx response double with configurable JSON behavior."""

    def __init__(
        self,
        body: object = None,
        *,
        status_code: int = 200,
        json_error: bool = False,
    ) -> None:
        self._body = body
        self.status_code = status_code
        self.text = "response text"
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error:
            raise ValueError("invalid JSON")
        return self._body


def _args(**kwargs: object) -> argparse.Namespace:
    """Build a typed namespace for direct command-handler tests."""
    return argparse.Namespace(**kwargs)


def test_http_transport_fails_closed_and_handles_empty_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Transport, status, and response-decoding failures remain observable."""
    def raise_transport(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("network unavailable")

    monkeypatch.setattr("general_ludd.cli_ornith.httpx.request", raise_transport)
    with pytest.raises(SystemExit, match="1"):
        ornith._http("GET", "http://daemon/status")
    assert "network unavailable" in capsys.readouterr().err

    monkeypatch.setattr(
        "general_ludd.cli_ornith.httpx.request",
        lambda *args, **kwargs: _Response(status_code=503),
    )
    with pytest.raises(SystemExit, match="1"):
        ornith._http("GET", "http://daemon/status")
    assert "503" in capsys.readouterr().err

    monkeypatch.setattr(
        "general_ludd.cli_ornith.httpx.request",
        lambda *args, **kwargs: _Response(json_error=True),
    )
    assert ornith._http("GET", "http://daemon/status") is None


def test_pairs_covers_status_json_and_empty_presentations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both status-summary and empty-pending presentations are stable."""
    payload: object = {"counts_by_status": {"failed": 2}}

    def fake_http(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return payload

    monkeypatch.setattr(ornith, "_http", fake_http)
    ornith._cmd_pairs(
        _args(status="failed", limit=3, daemon_url="http://d", json=False)
    )
    assert "failed" in capsys.readouterr().out

    ornith._cmd_pairs(
        _args(status="failed", limit=3, daemon_url="http://d", json=True)
    )
    assert "counts_by_status" in capsys.readouterr().out

    payload = {"pending": []}
    ornith._cmd_pairs(_args(status=None, limit=3, daemon_url="http://d", json=False))
    assert "no pending" in capsys.readouterr().out

    ornith._cmd_pairs(_args(status=None, limit=3, daemon_url="http://d", json=True))
    assert "pending" in capsys.readouterr().out


def test_export_stats_and_outcome_fallback_presentations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Export, stats, and outcome handlers cover JSON and empty responses."""
    payload: object = {"row_count": 4, "path": "/tmp/export.jsonl"}

    def fake_http(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return payload

    monkeypatch.setattr(ornith, "_http", fake_http)
    export_args = _args(
        since=None,
        project=None,
        out=None,
        daemon_url="http://d",
        json=False,
    )
    ornith._cmd_export(export_args)
    assert "4 row" in capsys.readouterr().out
    export_args.json = True
    ornith._cmd_export(export_args)
    assert "row_count" in capsys.readouterr().out

    payload = None
    export_args.json = False
    ornith._cmd_export(export_args)
    assert "no response" in capsys.readouterr().out

    stats_args = _args(daemon_url="http://d", json=True)
    ornith._cmd_stats(stats_args)
    assert "null" in capsys.readouterr().out
    stats_args.json = False
    ornith._cmd_stats(stats_args)
    assert capsys.readouterr().out == ""

    outcome_args = _args(
        details=None,
        status="failed",
        daemon_url="http://d",
        pair_id="pair-1",
        json=True,
    )
    ornith._cmd_set_outcome(outcome_args)
    assert "null" in capsys.readouterr().out
    outcome_args.json = False
    ornith._cmd_set_outcome(outcome_args)
    assert "set-outcome failed" in capsys.readouterr().out


def test_status_solve_and_improve_empty_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty response bodies do not fabricate status or task results."""
    payload: object = None

    def fake_http(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return payload

    monkeypatch.setattr(ornith, "_http", fake_http)

    status_args = _args(daemon_url="http://d", json=False)
    ornith._cmd_status(status_args)
    assert "no status" in capsys.readouterr().out
    status_args.json = True
    ornith._cmd_status(status_args)
    assert "null" in capsys.readouterr().out

    solve_args = _args(
        task="task",
        target_files=[],
        max_iter=None,
        daemon_url="http://d",
        json=False,
    )
    ornith._cmd_solve(solve_args)
    assert "no response" in capsys.readouterr().out
    solve_args.json = True
    ornith._cmd_solve(solve_args)
    assert "null" in capsys.readouterr().out

    improve_args = _args(
        artifact_path="artifact",
        kind="role",
        feedback=None,
        daemon_url="http://d",
        json=False,
    )
    ornith._cmd_improve(improve_args)
    assert "no response" in capsys.readouterr().out
    improve_args.json = True
    ornith._cmd_improve(improve_args)
    assert "null" in capsys.readouterr().out


def test_config_writes_recover_from_invalid_yaml_and_report_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invalid prior YAML is replaced atomically with the requested setting."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "config.yml"
    config_path.write_text("invalid: [yaml\n")

    ornith._cmd_enable(_args(config_dir=str(config_dir), json=True))
    assert '"ornith_enabled": true' in capsys.readouterr().out
    assert "ornith_enabled: true" in config_path.read_text()

    ornith._cmd_disable(_args(config_dir=str(config_dir), json=True))
    assert '"ornith_enabled": false' in capsys.readouterr().out
    assert "ornith_enabled: false" in config_path.read_text()


def test_doctor_json_covers_unreachable_missing_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor emits structured failures when daemon metadata is unavailable."""
    perms = tmp_path / "permissions"
    perms.mkdir()
    (perms / "broken.yml").write_text("invalid: [yaml\n")

    def unavailable(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise SystemExit(1)

    monkeypatch.setattr(ornith, "_http", unavailable)
    monkeypatch.setattr("general_ludd.cli_ornith.shutil.which", lambda name: None)

    with pytest.raises(SystemExit, match="1"):
        ornith._cmd_doctor(
            _args(
                daemon_url="http://d",
                perms_dir=str(perms),
                binary_name="ornith",
                expected_model_sha=None,
                json=True,
            )
        )
    output = capsys.readouterr().out
    assert '"healthy": false' in output
    assert "no model_sha reported" in output


def test_training_history_and_config_empty_branches(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Remaining command handlers render empty and alternate result shapes."""
    payload: object = {"cycle": {"triggered_at": "now", "result": {"ok": True}}}

    def fake_http(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return payload

    monkeypatch.setattr(ornith, "_http", fake_http)
    train_args = _args(daemon_url="http://d", timeout=5.0, json=False)
    ornith._cmd_train(train_args)
    assert "result" in capsys.readouterr().out

    payload = None
    ornith._cmd_train(train_args)
    assert "no response" in capsys.readouterr().out

    history_args = _args(daemon_url="http://d", limit=3, json=True)
    ornith._cmd_history(history_args)
    assert "null" in capsys.readouterr().out
    history_args.json = False
    ornith._cmd_history(history_args)
    assert "no history" in capsys.readouterr().out

    config_get_args = _args(daemon_url="http://d", json=False)
    ornith._cmd_config_get(config_get_args)
    assert "no config" in capsys.readouterr().out

    config_set_args = _args(daemon_url="http://d", model_sha=None, json=False)
    ornith._cmd_config_set(config_set_args)
    assert capsys.readouterr().out == ""
