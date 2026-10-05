"""Branch coverage for permission CLI mutation and daemon-backed handlers."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

import general_ludd.cli_perm as perm


class _Response:
    """Minimal httpx response double."""

    def __init__(
        self,
        body: object,
        *,
        status_code: int = 200,
        invalid_json: bool = False,
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.text = "response text"
        self.invalid_json = invalid_json

    def json(self) -> object:
        if self.invalid_json:
            raise ValueError("invalid JSON")
        return self.body


def _args(**kwargs: object) -> argparse.Namespace:
    """Return a typed namespace for direct command-handler calls."""
    return argparse.Namespace(**kwargs)


def test_parser_validation_adapter_covers_available_and_broken_parsers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional parser is authoritative when valid and fail-safe otherwise."""
    class ListParser:
        def validate(self, spec: dict[str, object]) -> list[str]:
            del spec
            return ["parser error"]

    class OtherParser:
        def validate(self, spec: dict[str, object]) -> object:
            del spec
            return True

    class BrokenParser:
        def validate(self, spec: dict[str, object]) -> object:
            del spec
            raise RuntimeError("broken parser")

    module: object = SimpleNamespace(PermissionSpecParser=ListParser)

    def import_module(name: str) -> object:
        del name
        return module

    monkeypatch.setattr("importlib.import_module", import_module)
    spec: dict[str, object] = {"agent_type": "agent"}
    assert perm.validate_spec(spec) == ["parser error"]

    module = SimpleNamespace(PermissionSpecParser=None)
    assert perm._try_parser_validate(spec) is None

    module = SimpleNamespace(PermissionSpecParser=OtherParser)
    assert perm._try_parser_validate(spec) == []

    module = SimpleNamespace(PermissionSpecParser=BrokenParser)
    assert perm._try_parser_validate(spec) is None

    def fail_import(name: str) -> object:
        del name
        raise ImportError("missing")

    monkeypatch.setattr("importlib.import_module", fail_import)
    assert perm._try_parser_validate(spec) is None


def test_http_dispatch_covers_methods_errors_and_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every supported verb carries PSK auth and failures remain observable."""
    response = _Response({"ok": True})
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def request(*args: object, **kwargs: object) -> _Response:
        calls.append((args, kwargs))
        return response

    for name in ("get", "post", "delete", "put", "patch", "request"):
        monkeypatch.setattr(f"general_ludd.cli_perm.httpx.{name}", request)

    for method in ("GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS"):
        assert perm._http_with_psk(
            method,
            "http://daemon/endpoint",
            psk="secret",
            json_body={"x": 1},
        ) == {"ok": True}
    assert all(call[1]["headers"] == {"Authorization": "Bearer secret"} for call in calls)

    response = _Response(None, invalid_json=True)
    assert perm._http_with_psk("GET", "http://d", psk=None) == {
        "text": "response text"
    }

    response = _Response(None, status_code=403)
    with pytest.raises(SystemExit, match="1"):
        perm._http_with_psk("GET", "http://d", psk=None)
    assert "403" in capsys.readouterr().err

    def fail_request(*args: object, **kwargs: object) -> _Response:
        del args, kwargs
        raise OSError("network down")

    monkeypatch.setattr("general_ludd.cli_perm.httpx.get", fail_request)
    with pytest.raises(SystemExit, match="1"):
        perm._http_with_psk("GET", "http://d", psk=None)
    assert "network down" in capsys.readouterr().err


def test_grant_and_deny_create_specs_and_reject_invalid_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Grant and deny persist valid specs while validation blocks bad writes."""
    base = {
        "config_dir": str(tmp_path),
        "agent_type": "builder",
        "resource": "repo",
        "actions": "read, write",
    }
    perm._cmd_perm_grant(_args(**base, constraints=["branch=main"]))
    perm._cmd_perm_deny(_args(**base))
    spec = perm.SpecStore(tmp_path).load("builder")
    assert spec is not None
    assert spec["capabilities"][0]["constraints"] == {"branch": "main"}
    assert spec["denied"][0]["resource"] == "repo"
    assert "Granted" in capsys.readouterr().out

    monkeypatch.setattr(perm, "validate_spec", lambda spec: ["invalid spec"])
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_grant(_args(**base, constraints=[]))
    assert "Validation failed" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_deny(_args(**base))
    assert "invalid spec" in capsys.readouterr().err


def test_revoke_covers_missing_unchanged_abort_and_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Revocation is fail-closed and requires confirmation unless pre-approved."""
    common = {
        "config_dir": str(tmp_path),
        "agent_type": "runner",
        "resource": "repo",
    }
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_revoke(_args(**common, yes=True))
    assert "No spec" in capsys.readouterr().err

    store = perm.SpecStore(tmp_path)
    store.save("runner", {"agent_type": "runner", "capabilities": []})
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_revoke(_args(**common, yes=True))
    assert "No capability" in capsys.readouterr().err

    store.save(
        "runner",
        {
            "agent_type": "runner",
            "capabilities": [{"resource": "repo", "actions": ["read"]}],
        },
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    perm._cmd_perm_revoke(_args(**common, yes=False))
    assert "Aborted" in capsys.readouterr().out

    perm._cmd_perm_revoke(_args(**common, yes=True))
    assert "Revoked" in capsys.readouterr().out
    saved = store.load("runner")
    assert saved is not None and saved["capabilities"] == []


def test_edit_restores_invalid_changes_and_accepts_valid_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Editor failures and invalid content restore the last valid specification."""
    store = perm.SpecStore(tmp_path)
    original = {"agent_type": "editor", "capabilities": []}
    store.save("editor", original)
    path = store.spec_path("editor")
    args = _args(config_dir=str(tmp_path), agent_type="editor", editor="editor")

    monkeypatch.setattr("general_ludd.cli_perm.subprocess.call", lambda command: 2)
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_edit(args)
    assert "exited with code 2" in capsys.readouterr().err

    def write_list(command: list[str]) -> int:
        del command
        path.write_text("- invalid\n")
        return 0

    monkeypatch.setattr("general_ludd.cli_perm.subprocess.call", write_list)
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_edit(args)
    assert perm.SpecStore(tmp_path).load("editor") == original

    def delete_agent_type(command: list[str]) -> int:
        del command
        path.write_text("capabilities: []\n")
        return 0

    monkeypatch.setattr("general_ludd.cli_perm.subprocess.call", delete_agent_type)
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_edit(args)
    assert "original file restored" in capsys.readouterr().err

    def write_valid(command: list[str]) -> int:
        del command
        path.write_text("agent_type: editor\ncapabilities: []\nmax_sts_ttl: 30\n")
        return 0

    monkeypatch.setattr("general_ludd.cli_perm.subprocess.call", write_valid)
    perm._cmd_perm_edit(args)
    assert "Updated" in capsys.readouterr().out


def test_diff_human_output_covers_unique_and_changed_capabilities(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human diff reports unique resources and action mismatches."""
    store = perm.SpecStore(tmp_path)
    store.save(
        "a",
        {
            "agent_type": "a",
            "capabilities": [
                {"resource": "shared", "actions": ["read"]},
                {"resource": "only-a", "actions": ["write"]},
            ],
        },
    )
    store.save(
        "b",
        {
            "agent_type": "b",
            "capabilities": [
                {"resource": "shared", "actions": ["read", "write"]},
                {"resource": "only-b", "actions": ["read"]},
            ],
        },
    )
    perm._cmd_perm_diff(
        _args(config_dir=str(tmp_path), agent_type_a="a", agent_type_b="b", json=False)
    )
    output = capsys.readouterr().out
    assert "only-a" in output
    assert "only-b" in output
    assert "Action differences" in output


def test_sts_handlers_cover_empty_table_json_and_token_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """STS list, issue, inspect, and revoke support all presentation modes."""
    payload: object = {"tokens": []}

    def fake_http(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return payload

    monkeypatch.setattr(perm, "_http_with_psk", fake_http)
    common = {"daemon_url": "http://d", "psk": "secret"}
    list_args = _args(**common, agent_id="agent", active_only=True, json=False)
    perm._cmd_perm_sts_list(list_args)
    assert "No STS tokens" in capsys.readouterr().out

    payload = {
        "tokens": [
            {"token_id": "token-1", "subject": "agent", "expires_at": "later"}
        ]
    }
    perm._cmd_perm_sts_list(list_args)
    assert "token-1" in capsys.readouterr().out
    list_args.json = True
    perm._cmd_perm_sts_list(list_args)
    assert "tokens" in capsys.readouterr().out

    missing = tmp_path / "missing.yml"
    with pytest.raises(SystemExit, match="1"):
        perm._cmd_perm_sts_issue(
            _args(
                **common,
                spec_yaml=str(missing),
                subject_agent_id="agent",
                ttl=None,
                json=False,
            )
        )

    spec = tmp_path / "spec.yml"
    spec.write_text("agent_type: agent\n")
    issue_args = _args(
        **common,
        spec_yaml=str(spec),
        subject_agent_id="agent",
        ttl=60,
        json=False,
    )
    payload = {"token": "signed", "expires_at": "later"}
    perm._cmd_perm_sts_issue(issue_args)
    assert "signed" in capsys.readouterr().out
    issue_args.json = True
    perm._cmd_perm_sts_issue(issue_args)
    assert "token" in capsys.readouterr().out

    payload = {"event": "issued"}
    perm._cmd_perm_sts_inspect(_args(**common, token_id="token-1"))
    perm._cmd_perm_sts_revoke(_args(**common, token_id="token-1"))
    assert capsys.readouterr().out.count("issued") == 2


def test_audit_and_escalation_handlers_cover_all_presentations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit and escalation commands render empty, tabular, and JSON results."""
    payload: object = {"events": []}

    def fake_http(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return payload

    monkeypatch.setattr(perm, "_http_with_psk", fake_http)
    common = {"daemon_url": "http://d", "psk": "secret"}
    audit_args = _args(
        **common,
        agent_id="agent",
        since="today",
        capability="deploy",
        json=False,
    )
    perm._cmd_perm_audit(audit_args)
    assert "No audit events" in capsys.readouterr().out
    payload = {
        "events": [
            {
                "time": "now",
                "issuer": "admin",
                "subject": "agent",
                "capability": "deploy",
                "target": "prod",
                "event": "allowed",
            }
        ]
    }
    perm._cmd_perm_audit(audit_args)
    assert "allowed" in capsys.readouterr().out
    audit_args.json = True
    perm._cmd_perm_audit(audit_args)
    assert "events" in capsys.readouterr().out

    list_args = _args(**common, status="pending", json=False)
    payload = {"items": []}
    perm._cmd_perm_escalations_list(list_args)
    assert "No escalation requests" in capsys.readouterr().out
    payload = {
        "items": [
            {"id": 1, "agent_id": "agent", "status": "pending", "reason": "need deploy"}
        ]
    }
    perm._cmd_perm_escalations_list(list_args)
    assert "need deploy" in capsys.readouterr().out
    list_args.json = True
    perm._cmd_perm_escalations_list(list_args)
    assert "items" in capsys.readouterr().out

    approve_args = _args(
        **common,
        escalation_id=1,
        reason="approved",
        json=False,
    )
    payload = {"status": "approved", "sts_token_id": "token-1"}
    perm._cmd_perm_escalations_approve(approve_args)
    assert "token-1" in capsys.readouterr().out
    approve_args.json = True
    perm._cmd_perm_escalations_approve(approve_args)
    assert "approved" in capsys.readouterr().out

    deny_args = _args(**common, escalation_id=1, reason="unsafe", json=False)
    payload = {"status": "denied"}
    perm._cmd_perm_escalations_deny(deny_args)
    assert "denied" in capsys.readouterr().out
    deny_args.json = True
    perm._cmd_perm_escalations_deny(deny_args)
    assert '"status": "denied"' in capsys.readouterr().out

    history_args = _args(**common, agent_id="agent", json=False)
    payload = {"items": []}
    perm._cmd_perm_escalations_history(history_args)
    assert "No escalation history" in capsys.readouterr().out
    payload = {
        "items": [
            {"id": 1, "agent_id": "agent", "status": "approved", "created_at": "now"}
        ]
    }
    perm._cmd_perm_escalations_history(history_args)
    assert "approved" in capsys.readouterr().out
    history_args.json = True
    perm._cmd_perm_escalations_history(history_args)
    assert "items" in capsys.readouterr().out
