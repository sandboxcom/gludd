"""Tests for `gludd perm` CLI subcommands.

Covers the file-backed permission-spec operations (list/show/grant/deny/revoke/
edit/validate/diff/project) and the HTTP-backed STS/audit operations (sts list/
issue/inspect/revoke, audit).  The parallel task that owns
``general_ludd.security.permissions`` and ``general_ludd.security.sts`` has not
landed yet, so these tests exercise the CLI through the public ``main()``
entrypoint against fixture config dirs and mocked httpx calls — exactly the
shape the daemon endpoints will expose when they arrive.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli import main


def _parse(args: list[str]) -> None:
    with patch.object(sys, "argv", ["gludd", *args]):
        main()


def _write_spec(config_dir: Path, agent_type: str, spec: dict) -> Path:
    import yaml

    perms_dir = config_dir / "permissions"
    perms_dir.mkdir(parents=True, exist_ok=True)
    path = perms_dir / f"{agent_type}.yml"
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return path


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cdir = tmp_path / "gludd-config"
    cdir.mkdir()
    _write_spec(
        cdir,
        "build",
        {
            "agent_type": "build",
            "capabilities": [
                {"resource": "file:repo", "actions": ["read", "write"], "constraints": {}},
            ],
            "denied": [{"resource": "net:public", "actions": ["bind"]}],
            "max_sts_ttl": 3600,
        },
    )
    _write_spec(
        cdir,
        "review",
        {
            "agent_type": "review",
            "capabilities": [
                {"resource": "file:repo", "actions": ["read"], "constraints": {}},
            ],
            "denied": [],
            "max_sts_ttl": 1800,
        },
    )
    return cdir


# ---------------------------------------------------------------------------
# File-backed subcommands
# ---------------------------------------------------------------------------


class TestPermList:
    def test_perm_list_prints_all_specs(self, capsys, config_dir):
        _parse(["perm", "list", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        assert "build" in out
        assert "review" in out

    def test_perm_list_filter_by_agent_type(self, capsys, config_dir):
        _parse(["perm", "list", "--agent-type", "build", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        assert "build" in out
        assert "review" not in out

    def test_perm_list_json(self, capsys, config_dir):
        _parse(["perm", "list", "--config-dir", str(config_dir), "--json"])
        out = capsys.readouterr().out
        import json

        data = json.loads(out)
        agent_types = {d["agent_type"] for d in data}
        assert agent_types == {"build", "review"}


class TestPermShow:
    def test_perm_show_prints_yaml(self, capsys, config_dir):
        _parse(["perm", "show", "build", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        assert "agent_type: build" in out
        assert "file:repo" in out

    def test_perm_show_json(self, capsys, config_dir):
        _parse(["perm", "show", "build", "--config-dir", str(config_dir), "--json"])
        out = capsys.readouterr().out
        import json

        data = json.loads(out)
        assert data["agent_type"] == "build"


class TestPermGrant:
    def test_perm_grant_adds_capability(self, capsys, config_dir):
        _parse([
            "perm", "grant", "review", "net:registry", "read",
            "--config-dir", str(config_dir),
        ])
        # now `show` should include it
        _parse(["perm", "show", "review", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        assert "net:registry" in out
        assert "read" in out

    def test_perm_grant_with_constraints(self, capsys, config_dir):
        _parse([
            "perm", "grant", "review", "file:tmp", "read,write",
            "--constraints", "path_prefix=/tmp",
            "--config-dir", str(config_dir),
        ])
        _parse(["perm", "show", "review", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        assert "path_prefix" in out


class TestPermDeny:
    def test_perm_deny_adds_to_denied_list(self, capsys, config_dir):
        _parse([
            "perm", "deny", "review", "exec:shell", "exec",
            "--config-dir", str(config_dir),
        ])
        _parse(["perm", "show", "review", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        assert "exec:shell" in out


class TestPermRevoke:
    def test_perm_revoke_removes_capability(self, capsys, config_dir):
        _parse([
            "perm", "revoke", "build", "file:repo",
            "--config-dir", str(config_dir),
            "--yes",
        ])
        # flush the revoke-message output before reading the spec
        capsys.readouterr()
        _parse(["perm", "show", "build", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        # capabilities list should be empty (file:repo was the only entry)
        assert "capabilities: []" in out


class TestPermEdit:
    def test_perm_edit_refuses_invalid_spec(self, capsys, config_dir, tmp_path):
        # Mock the editor to overwrite the file with a broken spec (missing agent_type).
        def _fake_editor(cmd: list[str]):
            # cmd[-1] is the file path
            target = cmd[-1]
            Path(target).write_text("denied: []\nmax_sts_ttl: 100\n")
            return 0

        with patch("subprocess.call", side_effect=_fake_editor):
            ec = _parse_returncode([
                "perm", "edit", "build",
                "--editor", "fakeed",
                "--config-dir", str(config_dir),
            ])
        assert ec == 1
        err = capsys.readouterr().err
        assert "validation" in err.lower() or "invalid" in err.lower() or "error" in err.lower()
        # original file unchanged
        import yaml

        original = yaml.safe_load((config_dir / "permissions" / "build.yml").read_text())
        assert original["agent_type"] == "build"


class TestPermValidate:
    def test_perm_validate_returns_zero_when_all_valid(self, config_dir):
        ec = _parse_returncode(["perm", "validate", "--config-dir", str(config_dir)])
        assert ec == 0

    def test_perm_validate_returns_one_on_invalid(self, config_dir):
        _write_spec(config_dir, "broken", {"agent_type": "", "capabilities": [], "denied": [], "max_sts_ttl": -1})
        ec = _parse_returncode(["perm", "validate", "--config-dir", str(config_dir)])
        assert ec == 1


class TestPermDiff:
    def test_perm_diff_highlights_asymmetry(self, capsys, config_dir):
        _parse(["perm", "diff", "build", "review", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        # build has write on file:repo, review does not
        assert "write" in out or "file:repo" in out


class TestPermProject:
    def test_perm_project_override_isolated_from_system_default(
        self, capsys, config_dir
    ):
        _parse([
            "perm", "project", "proj-a",
            "--set-default-agent-type", "build",
            "--config-dir", str(config_dir),
        ])
        # project override file should exist
        proj_path = config_dir / "permissions" / "projects" / "proj-a" / "build.yml"
        assert proj_path.exists()
        # system default unchanged
        _parse(["perm", "show", "build", "--config-dir", str(config_dir)])
        out = capsys.readouterr().out
        assert "file:repo" in out


# ---------------------------------------------------------------------------
# HTTP-backed subcommands (mock httpx)
# ---------------------------------------------------------------------------


class TestPermStsList:
    def test_perm_sts_list_calls_daemon_endpoint(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"tokens": [{"token_id": "t1", "subject": "agent-7"}]},
            )
            _parse([
                "perm", "sts", "list",
                "--daemon-url", "http://d:8000",
                "--psk", "secret",
            ])
            call = mock_get.call_args
            assert "/admin/sts/active" in call.args[0]
            out = capsys.readouterr().out
            assert "t1" in out

    def test_perm_sts_list_active_only_filter(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"tokens": []},
            )
            _parse([
                "perm", "sts", "list", "--active-only",
                "--daemon-url", "http://d:8000",
                "--psk", "secret",
            ])
            params = mock_get.call_args.kwargs.get("params", {})
            assert params.get("active_only") is True


class TestPermStsIssue:
    def test_perm_sts_issue_calls_daemon_with_psk(self, capsys, tmp_path):
        spec_yaml = tmp_path / "spec.yml"
        spec_yaml.write_text("agent_type: build\ncapabilities: []\ndenied: []\nmax_sts_ttl: 60\n")
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"token": "STS-TOKEN", "expires_at": "2026-01-01T00:00:00Z"},
            )
            _parse([
                "perm", "sts", "issue", "agent-7",
                "--spec-yaml", str(spec_yaml),
                "--ttl", "120",
                "--daemon-url", "http://d:8000",
                "--psk", "secret",
            ])
            headers = mock_post.call_args.kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer secret"
            body = mock_post.call_args.kwargs.get("json", {})
            assert body["subject_agent_id"] == "agent-7"
            assert body["ttl"] == 120
            out = capsys.readouterr().out
            assert "STS-TOKEN" in out


class TestPermStsInspect:
    def test_perm_sts_inspect_fetches_token_details(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"token_id": "t1", "audit": [{"event": "issued"}]},
            )
            _parse([
                "perm", "sts", "inspect", "t1",
                "--daemon-url", "http://d:8000",
                "--psk", "secret",
            ])
            params = mock_get.call_args.kwargs.get("params", {})
            assert params.get("token_id") == "t1"
            out = capsys.readouterr().out
            assert "t1" in out


class TestPermStsRevoke:
    def test_perm_sts_revoke_calls_delete(self, capsys):
        with patch("httpx.delete") as mock_del:
            mock_del.return_value = MagicMock(
                status_code=200,
                json=lambda: {"revoked": True},
            )
            _parse([
                "perm", "sts", "revoke", "t1",
                "--daemon-url", "http://d:8000",
                "--psk", "secret",
            ])
            url = mock_del.call_args.args[0]
            assert "t1" in url


class TestPermAudit:
    def test_perm_audit_filters_by_agent_id(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"events": [
                    {
                        "time": "2026-01-01", "issuer": "a", "subject": "b",
                        "capability": "read", "target": "f", "event": "issued",
                    },
                ]},
            )
            _parse([
                "perm", "audit", "--agent-id", "agent-7",
                "--daemon-url", "http://d:8000",
                "--psk", "secret",
            ])
            params = mock_get.call_args.kwargs.get("params", {})
            assert params.get("agent_id") == "agent-7"
            out = capsys.readouterr().out
            assert "issued" in out


# ---------------------------------------------------------------------------
# Scriptability
# ---------------------------------------------------------------------------


class TestNonInteractive:
    def test_perm_non_interactive_flag_supported(self, capsys, config_dir):
        """Every subcommand must work with --json for scripting (no prompts)."""
        for argv in [
            ["perm", "list", "--config-dir", str(config_dir), "--json"],
            ["perm", "show", "build", "--config-dir", str(config_dir), "--json"],
            ["perm", "validate", "--config-dir", str(config_dir), "--json"],
        ]:
            _parse(argv)
            out = capsys.readouterr().out
            import json

            json.loads(out)  # must be valid JSON


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _parse_returncode(argv: list[str]) -> int:
    """Run main() and return its exit code (without SystemExit propagating)."""
    with patch.object(sys, "argv", ["gludd", *argv]):
        try:
            main()
        except SystemExit as exc:
            return int(exc.code) if isinstance(exc.code, int) else 1
    return 0
