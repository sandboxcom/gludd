"""Integration tests for the security router (STS + permission-spec endpoints).

TDD: written BEFORE ``src/general_ludd/routers/security.py`` is registered.
Exercises the FULL daemon stack (``create_daemon_app`` + PSK middleware + the
new ``/admin/sts/*`` and ``/admin/perm/*`` endpoints) to prove the wiring is
correct and the PSK gate + subset-enforcement + audit flows work end-to-end.

The security router is PSK-gated by the daemon's central
``auth_and_stats_middleware`` (every ``/admin/*`` path is challenged unless a
valid ``Authorization: Bearer <psk>`` header is presented), so these tests
confirm both the 401-on-missing-PSK contract AND the happy-path behavior.
"""

from __future__ import annotations

import gc
import warnings
from typing import Any

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


def _config_dir(tmp_path: pytest.Path) -> str:
    """Write a minimal config dir pointing at a temp SQLite DB."""
    cdir = tmp_path / "config"
    cdir.mkdir(parents=True)
    db_path = tmp_path / "test.db"
    (cdir / "general-ludd.yml").write_text(f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n")
    return str(cdir)


def _psk_headers(psk: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {psk}"}


def test_repeated_daemon_lifespans_close_sqlite_connections(
    tmp_path: pytest.Path,
) -> None:
    """Daemon shutdown must not leave DB-API connections for cyclic GC."""
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        for index in range(2):
            config_dir = _config_dir(tmp_path / str(index))
            with _patch_runner():
                app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
                with TestClient(app):
                    pass
            del app
        gc.collect()

    leaked = [
        warning
        for warning in caught
        if warning.category is ResourceWarning
        and "unclosed database" in str(warning.message)
    ]
    assert not leaked, [str(warning.message) for warning in leaked]


# A primary-style issuer spec: broad caps so subset checks can narrow it.
_ISSUER_SPEC_YAML = """\
version: 1
agent_type: primary
parent_agent_id: null
max_sts_ttl_seconds: 3600
max_subagent_permissions: "same_or_fewer"
capabilities:
  - resource: file:tmp
    actions: ["read", "write"]
    constraints:
      path_prefix: "/tmp/gludd/"
  - resource: net:egress:llm_api
    actions: ["connect"]
    constraints:
      allowed_hosts: ["api.anthropic.com", "api.openai.com"]
  - resource: secret:openbao
    actions: ["read"]
    constraints:
      openbao_paths: ["secret/data/gludd/*"]
denied: []
"""

# A valid subset of the issuer (narrower path, subset of hosts).
_VALID_SUBJECT_YAML = """\
version: 1
agent_type: subagent
parent_agent_id: primary
max_sts_ttl_seconds: 1800
max_subagent_permissions: "same_or_fewer"
capabilities:
  - resource: file:tmp
    actions: ["read"]
    constraints:
      path_prefix: "/tmp/gludd/sub/"
  - resource: net:egress:llm_api
    actions: ["connect"]
    constraints:
      allowed_hosts: ["api.anthropic.com"]
denied: []
"""

# An OVER-privileged subject (asks for write the issuer only grants read on).
_OVER_PRIV_SUBJECT_YAML = """\
version: 1
agent_type: subagent
parent_agent_id: primary
max_sts_ttl_seconds: 1800
max_subagent_permissions: "same_or_fewer"
capabilities:
  - resource: secret:openbao
    actions: ["read", "write"]
    constraints:
      openbao_paths: ["secret/data/gludd/*"]
denied: []
"""


@pytest.fixture(autouse=True)
def _reset_daemon_state() -> Any:
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    yield
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


class TestStsIssue:
    def test_issue_sts_requires_admin_psk(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A request without a valid PSK Bearer token is rejected with 401."""
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/sts/issue",
                    json={
                        "subject_agent_id": "agent-7",
                        "requested_spec_yaml": _VALID_SUBJECT_YAML,
                        "ttl_seconds": 300,
                    },
                )
                assert resp.status_code == 401

    def test_issue_sts_returns_token_for_valid_subset_request(
        self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            _set_issuer(app, _ISSUER_SPEC_YAML)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/sts/issue",
                    json={
                        "subject_agent_id": "agent-7",
                        "requested_spec_yaml": _VALID_SUBJECT_YAML,
                        "ttl_seconds": 300,
                    },
                    headers=_psk_headers("test-secret-key"),
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert "token_id" in data
                assert "expires_at" in data
                assert data["token_id"]

    def test_issue_sts_rejects_overprivileged_request(
        self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            _set_issuer(app, _ISSUER_SPEC_YAML)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/sts/issue",
                    json={
                        "subject_agent_id": "agent-7",
                        "requested_spec_yaml": _OVER_PRIV_SUBJECT_YAML,
                        "ttl_seconds": 300,
                    },
                    headers=_psk_headers("test-secret-key"),
                )
                assert resp.status_code in (400, 403), resp.text
                assert "permission" in resp.text.lower() or "denied" in resp.text.lower()


class TestStsActiveAndRevoke:
    def test_get_active_lists_unexpired_tokens(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            _set_issuer(app, _ISSUER_SPEC_YAML)
            with TestClient(app) as client:
                # Issue one token first.
                issue = client.post(
                    "/admin/sts/issue",
                    json={
                        "subject_agent_id": "agent-7",
                        "requested_spec_yaml": _VALID_SUBJECT_YAML,
                        "ttl_seconds": 300,
                    },
                    headers=_psk_headers("test-secret-key"),
                )
                assert issue.status_code == 200
                token_id = issue.json()["token_id"]

                active = client.get(
                    "/admin/sts/active",
                    headers=_psk_headers("test-secret-key"),
                )
                assert active.status_code == 200
                tokens = active.json().get("tokens", [])
                token_ids = [t.get("token_id") for t in tokens]
                assert token_id in token_ids

    def test_revoke_expires_token_immediately(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            _set_issuer(app, _ISSUER_SPEC_YAML)
            with TestClient(app) as client:
                issue = client.post(
                    "/admin/sts/issue",
                    json={
                        "subject_agent_id": "agent-7",
                        "requested_spec_yaml": _VALID_SUBJECT_YAML,
                        "ttl_seconds": 300,
                    },
                    headers=_psk_headers("test-secret-key"),
                )
                token_id = issue.json()["token_id"]

                rev = client.post(
                    "/admin/sts/revoke",
                    json={"token_id": token_id},
                    headers=_psk_headers("test-secret-key"),
                )
                assert rev.status_code == 200

                # After revoke, /admin/sts/active must NOT list it.
                active = client.get(
                    "/admin/sts/active",
                    headers=_psk_headers("test-secret-key"),
                )
                token_ids = [t.get("token_id") for t in active.json().get("tokens", [])]
                assert token_id not in token_ids


class TestPermSpec:
    def test_get_perm_spec_returns_default(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            with TestClient(app) as client:
                resp = client.get(
                    "/admin/perm/spec/build",
                    headers=_psk_headers("test-secret-key"),
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert "spec_yaml" in data
                assert "build" in data["spec_yaml"]

    def test_put_perm_spec_validates_before_save(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        # An invalid spec: file: resource with NO path_prefix constraint.
        invalid_yaml = (
            "version: 1\n"
            "agent_type: build\n"
            "capabilities:\n"
            "  - resource: file:repo\n"
            '    actions: ["read"]\n'
            "    constraints: {}\n"
            "denied: []\n"
            "max_sts_ttl_seconds: 3600\n"
        )
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            with TestClient(app) as client:
                resp = client.put(
                    "/admin/perm/spec/build",
                    json={"spec_yaml": invalid_yaml},
                    headers=_psk_headers("test-secret-key"),
                )
                assert resp.status_code == 400, resp.text
                assert "path_prefix" in resp.text or "validation" in resp.text.lower()


class TestStsAudit:
    def test_audit_query_filters_by_agent_id(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        with _patch_runner():
            app = create_daemon_app(tick_interval=300.0, config_dir=_config_dir(tmp_path))
            _set_issuer(app, _ISSUER_SPEC_YAML)
            with TestClient(app) as client:
                # Issue a token as agent-7 so there's an audit event.
                client.post(
                    "/admin/sts/issue",
                    json={
                        "subject_agent_id": "agent-7",
                        "requested_spec_yaml": _VALID_SUBJECT_YAML,
                        "ttl_seconds": 300,
                    },
                    headers=_psk_headers("test-secret-key"),
                )

                resp = client.get(
                    "/admin/sts/audit",
                    params={"agent_id": "agent-7"},
                    headers=_psk_headers("test-secret-key"),
                )
                assert resp.status_code == 200, resp.text
                events = resp.json().get("events", [])
                assert len(events) >= 1
                for ev in events:
                    assert ev.get("subject_agent_id") == "agent-7" or ev.get("issuer_agent_id") == "agent-7"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_runner() -> Any:
    """Patch the Ansible runner adapter so create_daemon_app doesn't spawn."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock, patch

    @contextmanager
    def _cm():
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            yield

    return _cm()


def _set_issuer(app: Any, issuer_spec_yaml: str) -> None:
    """Install an StsIssuer + StsAuditLog bound to ``issuer_spec_yaml`` on app.state.

    The security router reads ``app.state._sts_issuer`` and
    ``app.state._sts_audit_log``. The issuer is constructed with the given
    primary-style spec so subset checks have something broad to narrow from.
    """
    from general_ludd.security.permissions import PermissionSpecParser
    from general_ludd.security.sts import StsAuditLog, StsIssuer

    issuer_spec = PermissionSpecParser.parse(issuer_spec_yaml)
    app.state._sts_issuer = StsIssuer()
    app.state._sts_issuer_spec = issuer_spec
    app.state._sts_audit_log = StsAuditLog()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
