"""Endpoint tests for ``general_ludd.routers.security`` (STS token lifecycle +
permission-spec admin endpoints — the most security-sensitive router).

Before this file, the router's only direct test contact besides the generic
mount-smoke loop (``tests/unit/test_router_registration.py``) was:
  * ``tests/unit/test_escalation_request.py`` — deep coverage of the
    escalation-request auto-approve/pending/approve/deny *decision logic*.
  * ``tests/integration/test_security_endpoints.py`` — full-daemon happy-path
    + PSK-401 smoke for ``/admin/sts/issue`` plus a handful of others.

This file fills the remaining gaps at the unit level (bare FastAPI +
``security.register()``, no daemon/DB):
  * every 400/404/409/403 error branch on every endpoint
  * ``GET /admin/perm/spec`` (the list endpoint) — previously UNTESTED anywhere
  * ``PUT /admin/perm/spec/{agent_type}`` happy path + file round-trip
  * ``GET/POST /admin/sts/*`` edge cases (empty list, unknown-token revoke,
    audit filters including the ``since``/``capability`` filters)
  * the "no DB session factory configured" fallback for escalation HumanTodo
    filing (silently returns ``human_todo_id=None`` + logs a warning — the
    common case for a daemon not yet wired to the DB)
  * approve/deny 404 (unknown id) and 409 (already-resolved) responses
  * authz posture: every endpoint is rejected identically to sibling
    ``/admin/*`` routers when the daemon's central PSK middleware is in play
    (401 missing-PSK, 503 fail-closed-no-PSK-configured, 200 with a valid PSK)

Router docstring note (unchanged by this file): the security router does NOT
re-check the PSK itself — it relies entirely on the daemon's
``auth_and_stats_middleware``. The ``TestAuthzPosture`` class below is the one
place in this file that exercises the REAL daemon app (``create_daemon_app``)
rather than a bare ``FastAPI()`` + ``register()``, because that posture can
only be observed through the middleware that actually enforces it.
"""
from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from general_ludd.routers import security as sec
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)

# ---------------------------------------------------------------------------
# Fixture YAML
# ---------------------------------------------------------------------------

# A primary-style issuer spec: file:repo rw + net egress to two LLM hosts.
# Deliberately has NO secret:openbao capability (used to exercise the
# "resource not held by issuer at all" branch of is_subset/PermissionDenied).
_ISSUER_SPEC_YAML = """\
version: 1
agent_type: primary
max_sts_ttl_seconds: 3600
capabilities:
  - resource: file:repo
    actions: ["read", "write"]
    constraints:
      path_prefix: "/repo/"
  - resource: net:egress:llm_api
    actions: ["connect"]
    constraints:
      allowed_hosts: ["api.anthropic.com", "api.openai.com"]
denied: []
"""

# A valid strict-subset request (narrower path, narrower host set).
_VALID_SUBJECT_YAML = """\
version: 1
agent_type: subagent
max_sts_ttl_seconds: 1800
capabilities:
  - resource: file:repo
    actions: ["read"]
    constraints:
      path_prefix: "/repo/sub/"
denied: []
"""

# Requests an action (delete) the issuer never granted -> not a subset.
_OVERPRIV_SUBJECT_YAML = """\
version: 1
agent_type: subagent
capabilities:
  - resource: file:repo
    actions: ["read", "write", "delete"]
    constraints:
      path_prefix: "/repo/"
denied: []
"""

# A syntactically valid spec that FAILS PermissionSpecParser.validate():
# file: resource with no path_prefix constraint.
_SPEC_MISSING_CONSTRAINT_YAML = """\
version: 1
agent_type: build
capabilities:
  - resource: file:repo
    actions: ["read"]
    constraints: {}
denied: []
"""

# YAML that fails to parse at all (tab cannot start a token).
_MALFORMED_YAML = "\tnot: valid"


def _valid_perm_spec_yaml(agent_type: str) -> str:
    return (
        "version: 1\n"
        f"agent_type: {agent_type}\n"
        "capabilities:\n"
        "  - resource: file:repo\n"
        '    actions: ["read"]\n'
        "    constraints:\n"
        '      path_prefix: "/repo/"\n'
        "denied: []\n"
    )


def _minimal_current_spec_yaml() -> str:
    return (
        "version: 1\n"
        "agent_type: sts_token\n"
        "subject: sts_token\n"
        "capabilities: []\n"
        "denied: []\n"
        "max_sts_ttl_seconds: 1800\n"
    )


def _escalation_body(
    *,
    agent_id: str = "agent-Z",
    requested: list[dict[str, Any]] | None = None,
    reason: str = "need additional access",
    alternatives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "current_spec_yaml": _minimal_current_spec_yaml(),
        "requested_additional_capabilities": requested
        or [
            {
                "resource": "file:repo",
                "actions": ["write"],
                "constraints": {"path_prefix": "/repo/"},
            }
        ],
        "reason": reason,
        "alternatives_tried": (
            alternatives
            if alternatives is not None
            else [{"approach": f"approach-{i}"} for i in range(3)]
        ),
    }


def _build_app(
    tmp_path: Any,
    *,
    issuer_spec: PermissionSpec | None = None,
    human_spec: PermissionSpec | None = None,
) -> FastAPI:
    """Build a bare FastAPI app with only the security router wired.

    Mirrors the scaffolding of ``tests/unit/test_escalation_request.py``:
    app.state carries the collaborators the router pulls via ``_get_issuer``/
    ``_get_issuer_spec``/``_get_human_spec``/``_perms_dir`` instead of a live
    daemon. ``_config_dir`` is a pytest tmp_path so perm-spec file writes are
    isolated per test.
    """
    app = FastAPI()
    app.state._config_dir = str(tmp_path)
    if issuer_spec is not None:
        app.state._sts_issuer_spec = issuer_spec
    if human_spec is not None:
        app.state._human_spec = human_spec
    sec.register(app, {})

    class _InjectAdminCapabilities(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Any:
            request.state.auth_spec = PermissionSpec(
                agent_type="security-router-test",
                capabilities=[
                    Capability(resource="admin:sts", actions=["revoke"]),
                    Capability(resource="admin:permissions", actions=["write"]),
                ],
                subject=PermissionSubject.HUMAN,
            )
            return await call_next(request)

    app.add_middleware(_InjectAdminCapabilities)
    return app


@pytest.fixture
def issuer_spec() -> PermissionSpec:
    return PermissionSpecParser.parse(_ISSUER_SPEC_YAML)


@pytest.fixture
def app(tmp_path: Any, issuer_spec: PermissionSpec) -> FastAPI:
    return _build_app(tmp_path, issuer_spec=issuer_spec)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /admin/sts/issue
# ---------------------------------------------------------------------------


class TestStsIssue:
    def test_happy_path_returns_token_and_audits(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/issue",
            json={
                "subject_agent_id": "agent-1",
                "requested_spec_yaml": _VALID_SUBJECT_YAML,
                "ttl_seconds": 300,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_id"]
        assert body["spec_yaml"] == _VALID_SUBJECT_YAML
        assert body["issued_at"] <= body["expires_at"]

        audit = client.get("/admin/sts/audit").json()["events"]
        assert any(
            e["token_id"] == body["token_id"] and e["event"] == "issued"
            for e in audit
        )

    @pytest.mark.parametrize("missing_field", ["subject_agent_id", "requested_spec_yaml"])
    def test_missing_required_field_400(self, client: TestClient, missing_field: str) -> None:
        body = {
            "subject_agent_id": "agent-1",
            "requested_spec_yaml": _VALID_SUBJECT_YAML,
        }
        body[missing_field] = ""
        resp = client.post("/admin/sts/issue", json=body)
        assert resp.status_code == 400
        assert "required" in resp.json()["error"]

    def test_malformed_yaml_400(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-1", "requested_spec_yaml": _MALFORMED_YAML},
        )
        assert resp.status_code == 400
        assert "invalid requested_spec_yaml" in resp.json()["error"]

    def test_spec_validation_failure_400(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/issue",
            json={
                "subject_agent_id": "agent-1",
                "requested_spec_yaml": _SPEC_MISSING_CONSTRAINT_YAML,
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "spec validation failed"
        assert resp.json()["details"]

    def test_overprivileged_request_403(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/issue",
            json={
                "subject_agent_id": "agent-1",
                "requested_spec_yaml": _OVERPRIV_SUBJECT_YAML,
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_falls_back_to_default_primary_spec_when_no_issuer_override(
        self, tmp_path: Any
    ) -> None:
        """With no ``app.state._sts_issuer_spec`` set, ``_get_issuer_spec``
        falls back to ``default_spec("primary")`` (secret:openbao read on
        secret/data/gludd/*)."""
        app = _build_app(tmp_path)  # no issuer_spec override
        client = TestClient(app)
        subject_yaml = (
            "version: 1\n"
            "agent_type: subagent\n"
            "capabilities:\n"
            "  - resource: secret:openbao\n"
            '    actions: ["read"]\n'
            "    constraints:\n"
            '      openbao_paths: ["secret/data/gludd/*"]\n'
            "denied: []\n"
        )
        resp = client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-1", "requested_spec_yaml": subject_yaml},
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# GET /admin/sts/active
# ---------------------------------------------------------------------------


class TestStsActive:
    def test_empty_when_none_issued(self, client: TestClient) -> None:
        resp = client.get("/admin/sts/active")
        assert resp.status_code == 200
        assert resp.json() == {"tokens": []}

    def test_lists_multiple_tokens_with_expected_fields(self, client: TestClient) -> None:
        issued_ids = []
        for i in range(2):
            resp = client.post(
                "/admin/sts/issue",
                json={
                    "subject_agent_id": f"agent-{i}",
                    "requested_spec_yaml": _VALID_SUBJECT_YAML,
                },
            )
            issued_ids.append(resp.json()["token_id"])

        active = client.get("/admin/sts/active").json()["tokens"]
        assert {t["token_id"] for t in active} == set(issued_ids)
        for t in active:
            assert set(t.keys()) == {
                "token_id", "issuer_agent_id", "subject_agent_id",
                "issued_at", "expires_at", "last_used_at", "use_count",
            }
            assert t["use_count"] == 0
            assert t["last_used_at"] is None


# ---------------------------------------------------------------------------
# POST /admin/sts/revoke
# ---------------------------------------------------------------------------


class TestStsRevoke:
    def test_missing_token_id_400(self, client: TestClient) -> None:
        resp = client.post("/admin/sts/revoke", json={})
        assert resp.status_code == 400
        assert "token_id" in resp.json()["error"]

    def test_unknown_token_404(self, client: TestClient) -> None:
        resp = client.post("/admin/sts/revoke", json={"token_id": "does-not-exist"})
        assert resp.status_code == 404

    def test_happy_path_removes_from_active_and_audits_expiry(
        self, client: TestClient
    ) -> None:
        issue = client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-1", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        ).json()
        token_id = issue["token_id"]

        resp = client.post("/admin/sts/revoke", json={"token_id": token_id})
        assert resp.status_code == 200
        assert resp.json() == {"revoked": True, "token_id": token_id}

        active_ids = [t["token_id"] for t in client.get("/admin/sts/active").json()["tokens"]]
        assert token_id not in active_ids

        audit = client.get("/admin/sts/audit").json()["events"]
        assert any(e["token_id"] == token_id and e["event"] == "expired" for e in audit)

    def test_double_revoke_second_call_404(self, client: TestClient) -> None:
        issue = client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-1", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        ).json()
        token_id = issue["token_id"]
        first = client.post("/admin/sts/revoke", json={"token_id": token_id})
        assert first.status_code == 200
        second = client.post("/admin/sts/revoke", json={"token_id": token_id})
        assert second.status_code == 404


# ---------------------------------------------------------------------------
# GET /admin/sts/audit
# ---------------------------------------------------------------------------


class TestStsAudit:
    def test_no_filter_returns_all_events(self, client: TestClient) -> None:
        client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-A", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        )
        client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-B", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        )
        events = client.get("/admin/sts/audit").json()["events"]
        assert len(events) == 2

    def test_agent_id_filter(self, client: TestClient) -> None:
        client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-A", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        )
        client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-B", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        )
        events = client.get("/admin/sts/audit", params={"agent_id": "agent-A"}).json()["events"]
        assert len(events) == 1
        assert events[0]["subject_agent_id"] == "agent-A"

    def test_since_filter_excludes_past_events(self, client: TestClient) -> None:
        client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-A", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        )
        events = client.get(
            "/admin/sts/audit", params={"since": time.time() + 100}
        ).json()["events"]
        assert events == []

    def test_capability_filter_matches_nothing_for_issue_events(
        self, client: TestClient
    ) -> None:
        """``StsAuditLog.record_issue`` always writes ``capability=None`` (only
        ``record_use`` — never invoked by this router — sets a capability), so
        filtering issue-only audit history by a capability string is always
        empty. Documents the actual (surprising-if-unaware) behavior rather
        than asserting a hoped-for one."""
        client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-A", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        )
        events = client.get(
            "/admin/sts/audit", params={"capability": "file:repo"}
        ).json()["events"]
        assert events == []


# ---------------------------------------------------------------------------
# GET /admin/perm/spec/{agent_type}
# ---------------------------------------------------------------------------


class TestPermSpecGet:
    def test_returns_default_spec_when_no_file_exists(self, client: TestClient) -> None:
        resp = client.get("/admin/perm/spec/build")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_type"] == "build"
        assert "build" in data["spec_yaml"]

    def test_unknown_agent_type_falls_back_to_subagent_default(
        self, client: TestClient
    ) -> None:
        """``default_spec`` maps unrecognized agent types to the most
        restrictive (subagent) default rather than erroring."""
        resp = client.get("/admin/perm/spec/totally-unknown-type")
        assert resp.status_code == 200
        assert "capabilities: []" in resp.json()["spec_yaml"]

    def test_returns_existing_file_content_verbatim(
        self, client: TestClient, tmp_path: Any
    ) -> None:
        perms_dir = tmp_path / "permissions"
        perms_dir.mkdir(parents=True, exist_ok=True)
        raw = "agent_type: custom\ncapabilities: []\n"
        (perms_dir / "custom.yml").write_text(raw)
        resp = client.get("/admin/perm/spec/custom")
        assert resp.status_code == 200
        assert resp.json()["spec_yaml"] == raw


# ---------------------------------------------------------------------------
# PUT /admin/perm/spec/{agent_type}
# ---------------------------------------------------------------------------


class TestPermSpecPut:
    def test_missing_spec_yaml_400(self, client: TestClient) -> None:
        resp = client.put("/admin/perm/spec/build", json={})
        assert resp.status_code == 400
        assert "spec_yaml" in resp.json()["error"]

    def test_parse_error_400(self, client: TestClient) -> None:
        resp = client.put(
            "/admin/perm/spec/build", json={"spec_yaml": _MALFORMED_YAML}
        )
        assert resp.status_code == 400
        assert "parse error" in resp.json()["error"]

    def test_validation_failure_400(self, client: TestClient) -> None:
        resp = client.put(
            "/admin/perm/spec/build",
            json={"spec_yaml": _SPEC_MISSING_CONSTRAINT_YAML},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation failed"
        assert resp.json()["details"]

    def test_happy_path_writes_file_and_roundtrips_via_get(
        self, client: TestClient, tmp_path: Any
    ) -> None:
        valid_yaml = _valid_perm_spec_yaml("build")
        resp = client.put("/admin/perm/spec/build", json={"spec_yaml": valid_yaml})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "saved"
        assert body["agent_type"] == "build"

        written = (tmp_path / "permissions" / "build.yml").read_text()
        assert written == valid_yaml

        get_resp = client.get("/admin/perm/spec/build")
        assert get_resp.json()["spec_yaml"] == valid_yaml


# ---------------------------------------------------------------------------
# GET /admin/perm/spec (list) — previously untested anywhere
# ---------------------------------------------------------------------------


class TestPermSpecList:
    def test_empty_when_no_specs_saved(self, client: TestClient) -> None:
        resp = client.get("/admin/perm/spec")
        assert resp.status_code == 200
        assert resp.json() == {"agent_types": []}

    def test_lists_saved_agent_types_sorted(self, client: TestClient) -> None:
        client.put(
            "/admin/perm/spec/zeta", json={"spec_yaml": _valid_perm_spec_yaml("zeta")}
        )
        client.put(
            "/admin/perm/spec/alpha", json={"spec_yaml": _valid_perm_spec_yaml("alpha")}
        )
        resp = client.get("/admin/perm/spec")
        assert resp.status_code == 200
        assert resp.json()["agent_types"] == ["alpha", "zeta"]


# ---------------------------------------------------------------------------
# POST /admin/perm/escalation-request — gap-filling only.
# Auto-approval/pending decision logic + the ">= 3 distinct alternatives"
# validation is exhaustively covered by tests/unit/test_escalation_request.py;
# this class adds the missing-field 400s and the no-session-factory fallback.
# ---------------------------------------------------------------------------


class TestEscalationRequest:
    @pytest.mark.parametrize(
        "field,empty_value",
        [
            ("agent_id", ""),
            ("current_spec_yaml", ""),
            ("requested_additional_capabilities", []),
            ("reason", ""),
        ],
    )
    def test_missing_required_field_400(
        self, client: TestClient, field: str, empty_value: Any
    ) -> None:
        body = _escalation_body()
        body[field] = empty_value
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 400
        assert "required" in resp.json()["error"]

    def test_no_session_factory_returns_none_human_todo_id_and_logs(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The default app fixture sets no ``app.state._session_factory``.
        A pending escalation must still be recorded (never dropped) but the
        HumanTodo filing is a documented no-op with a warning log — this is
        the actual behavior for a daemon not wired to the DB, and is the
        "genuinely hard to test the alternative without a live DB" case
        called out in the task; the DB-backed happy path for this same filing
        step is already covered end-to-end by
        tests/integration/test_permission_human_todo_e2e.py.
        """
        # secret:openbao is in the default human-operator spec but NOT in the
        # issuer_spec fixture (_ISSUER_SPEC_YAML) -> not a subset of the agent
        # spec -> pending (not auto-approved).
        body = _escalation_body(
            requested=[
                {
                    "resource": "secret:openbao",
                    "actions": ["read"],
                    "constraints": {"openbao_paths": ["secret/data/gludd/*"]},
                }
            ]
        )
        with caplog.at_level(logging.WARNING, logger="general_ludd.routers.security"):
            resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "pending"
        assert data["human_todo_id"] is None
        assert any(
            "no session factory" in rec.getMessage() for rec in caplog.records
        ), "expected a warning that the escalation will not surface a HumanTodo"

        # The row is still recorded and visible even though no HumanTodo fired.
        listed = client.get("/admin/perm/escalations?status=pending").json()["items"]
        assert len(listed) == 1
        assert listed[0]["id"] == data["id"]


# ---------------------------------------------------------------------------
# GET /admin/perm/escalations + /admin/perm/escalations/history
# (status/agent_id filters are covered by test_escalation_request.py; this
# adds the unfiltered "return everything" path.)
# ---------------------------------------------------------------------------


class TestEscalationListAndHistory:
    def test_list_no_filter_returns_all(self, client: TestClient) -> None:
        client.post("/admin/perm/escalation-request", json=_escalation_body(agent_id="agent-1"))
        client.post("/admin/perm/escalation-request", json=_escalation_body(agent_id="agent-2"))
        items = client.get("/admin/perm/escalations").json()["items"]
        assert len(items) == 2
        assert [i["id"] for i in items] == sorted(i["id"] for i in items)

    def test_history_no_filter_returns_all(self, client: TestClient) -> None:
        client.post("/admin/perm/escalation-request", json=_escalation_body(agent_id="agent-1"))
        client.post("/admin/perm/escalation-request", json=_escalation_body(agent_id="agent-2"))
        items = client.get("/admin/perm/escalations/history").json()["items"]
        assert len(items) == 2


# ---------------------------------------------------------------------------
# POST /admin/perm/escalations/{id}/approve
# ---------------------------------------------------------------------------


class TestEscalationApprove:
    def test_unknown_id_404(self, client: TestClient) -> None:
        resp = client.post("/admin/perm/escalations/999999/approve", json={"reason": "x"})
        assert resp.status_code == 404

    def test_permission_denied_when_effective_spec_exceeds_issuer_403(
        self, client: TestClient
    ) -> None:
        """secret:openbao is granted by the (default) human spec but the
        issuer_spec fixture never grants it at all. Approval intersects the
        request with the human spec (keeping secret:openbao) then asks the
        issuer to mint a token for that effective spec — which the issuer
        must refuse since it never held that capability. No mocking needed:
        this is the router's real PermissionDeniedError -> 403 mapping."""
        body = _escalation_body(
            requested=[
                {
                    "resource": "secret:openbao",
                    "actions": ["read"],
                    "constraints": {"openbao_paths": ["secret/data/gludd/*"]},
                }
            ]
        )
        create = client.post("/admin/perm/escalation-request", json=body)
        assert create.json()["status"] == "pending"
        esc_id = create.json()["id"]

        resp = client.post(
            f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "ok", "human_reviewer": "alice"}
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"
        # Row must remain pending — a rejected approval is not a partial state.
        row = client.get("/admin/perm/escalations?status=pending").json()["items"]
        assert any(r["id"] == esc_id for r in row)

    def test_happy_path_then_409_on_repeat(self, client: TestClient) -> None:
        """net:egress:llm_api is held by the issuer_spec fixture but the
        default human-operator spec only has the DIFFERENT resource name
        net:egress:any — a resource-name mismatch, not a scope mismatch — so
        the request is pending (not auto), and the human-intersected
        effective spec ends up with zero capabilities for this resource
        (empty is trivially a subset of anything), so the mint succeeds."""
        body = _escalation_body(
            requested=[
                {
                    "resource": "net:egress:llm_api",
                    "actions": ["connect"],
                    "constraints": {"allowed_hosts": ["api.openai.com"]},
                }
            ]
        )
        create = client.post("/admin/perm/escalation-request", json=body)
        assert create.json()["status"] == "pending"
        esc_id = create.json()["id"]

        first = client.post(
            f"/admin/perm/escalations/{esc_id}/approve",
            json={"reason": "approved", "human_reviewer": "alice"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "approved"
        assert first.json()["sts_token_id"]

        second = client.post(
            f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "again"}
        )
        assert second.status_code == 409
        assert "already approved" in second.json()["error"]


# ---------------------------------------------------------------------------
# POST /admin/perm/escalations/{id}/deny
# ---------------------------------------------------------------------------


class TestEscalationDeny:
    def test_unknown_id_404(self, client: TestClient) -> None:
        resp = client.post("/admin/perm/escalations/999999/deny", json={"reason": "no"})
        assert resp.status_code == 404

    @pytest.mark.parametrize("bad_reason", [None, "", "   "])
    def test_missing_or_blank_reason_400(
        self, client: TestClient, bad_reason: str | None
    ) -> None:
        create = client.post("/admin/perm/escalation-request", json=_escalation_body())
        # Force a pending escalation regardless of auto-approval outcome for
        # the default body by using a resource outside both default specs.
        if create.json()["status"] != "pending":
            create = client.post(
                "/admin/perm/escalation-request",
                json=_escalation_body(
                    requested=[
                        {"resource": "agent:spawn", "actions": ["invoke"], "constraints": {}}
                    ]
                ),
            )
        esc_id = create.json()["id"]
        body: dict[str, Any] = {}
        if bad_reason is not None:
            body["reason"] = bad_reason
        resp = client.post(f"/admin/perm/escalations/{esc_id}/deny", json=body)
        assert resp.status_code == 400

    def test_happy_path_then_409_on_repeat(self, client: TestClient) -> None:
        create = client.post(
            "/admin/perm/escalation-request",
            json=_escalation_body(
                requested=[
                    {"resource": "agent:spawn", "actions": ["invoke"], "constraints": {}}
                ]
            ),
        )
        assert create.json()["status"] == "pending"
        esc_id = create.json()["id"]

        first = client.post(
            f"/admin/perm/escalations/{esc_id}/deny",
            json={"reason": "not needed", "human_reviewer": "bob"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "denied"

        second = client.post(
            f"/admin/perm/escalations/{esc_id}/deny", json={"reason": "again"}
        )
        assert second.status_code == 409
        assert "already denied" in second.json()["error"]


# ---------------------------------------------------------------------------
# _sync_escalation_from_human_todo — not an HTTP endpoint, but the state-
# transition invariant that keeps a HumanTodo resolution and its linked
# escalation row consistent (fired from human_todos.py's PATCH handler).
# ---------------------------------------------------------------------------


class TestSyncEscalationFromHumanTodo:
    @pytest.mark.parametrize(
        "todo_status,expected_esc_status",
        [("done", "approved"), ("dismissed", "denied")],
    )
    def test_propagates_resolution_to_pending_row(
        self, app: FastAPI, todo_status: str, expected_esc_status: str
    ) -> None:
        import asyncio

        row: dict[str, Any] = {
            "id": 7,
            "agent_id": "agent-X",
            "status": "pending",
            "human_reviewer": None,
            "decided_at": None,
            "decided_reason": None,
        }
        app.state._escalation_store = [row]

        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app,
                "human-todo-id-1",
                tags=["escalation:7"],
                status=todo_status,
                human_resolver="carol",
                human_resolution="resolved via human-todo queue",
            )
        )
        assert row["status"] == expected_esc_status
        assert row["human_reviewer"] == "carol"
        assert row["decided_reason"] == "resolved via human-todo queue"
        assert row["decided_at"] is not None

    def test_noop_when_row_already_terminal(self, app: FastAPI) -> None:
        import asyncio

        row: dict[str, Any] = {
            "id": 8,
            "agent_id": "agent-X",
            "status": "approved",
            "human_reviewer": "someone-else",
            "decided_at": "2026-01-01T00:00:00+00:00",
            "decided_reason": "already handled",
        }
        app.state._escalation_store = [row]

        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app,
                "human-todo-id-2",
                tags=["escalation:8"],
                status="dismissed",
                human_resolver="carol",
                human_resolution="should not apply",
            )
        )
        # Only rows still "pending" are touched — an already-resolved row
        # (e.g. resolved directly via the escalation endpoints) is untouched.
        assert row["status"] == "approved"
        assert row["human_reviewer"] == "someone-else"


# ---------------------------------------------------------------------------
# Authz posture — the security router relies entirely on the daemon's
# central PSK middleware (see the router module docstring). These tests
# exercise the REAL daemon app so the posture can actually be observed:
# every /admin/* path here must be rejected identically to any other
# sibling admin router (401 missing-PSK, 503 fail-closed-no-PSK-configured).
# ---------------------------------------------------------------------------


class TestAuthzPosture:
    _ENDPOINTS: ClassVar[list[tuple[str, str, dict[str, Any] | None]]] = [
        ("POST", "/admin/sts/issue", {"subject_agent_id": "a", "requested_spec_yaml": "x"}),
        ("GET", "/admin/sts/active", None),
        ("POST", "/admin/sts/revoke", {"token_id": "x"}),
        ("GET", "/admin/sts/audit", None),
        ("GET", "/admin/perm/spec/build", None),
        ("PUT", "/admin/perm/spec/build", {"spec_yaml": "x"}),
        ("GET", "/admin/perm/spec", None),
        ("POST", "/admin/perm/escalation-request", {}),
        ("GET", "/admin/perm/escalations", None),
        ("GET", "/admin/perm/escalations/history", None),
        ("POST", "/admin/perm/escalations/1/approve", {}),
        ("POST", "/admin/perm/escalations/1/deny", {}),
    ]

    @pytest.mark.parametrize("method,path,body", _ENDPOINTS)
    def test_missing_psk_rejected_401_on_every_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> None:
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)
        monkeypatch.setenv("GLUDD_AUTH_PSK", "unit-test-psk")
        from general_ludd.daemon import create_daemon_app

        daemon_app = create_daemon_app()
        daemon_client = TestClient(daemon_app)
        resp = daemon_client.request(method, path, json=body)
        assert resp.status_code == 401, resp.text
        assert resp.json()["error"] == "unauthorized"

    def test_fails_closed_503_when_no_psk_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default-secure posture: with GLUDD_AUTH_PSK unset (and no explicit
        GLUDD_ALLOW_NO_AUTH opt-out), every non-public path is REFUSED rather
        than silently left open — the same fail-closed behavior every other
        admin router gets from the shared middleware."""
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)
        from general_ludd.daemon import create_daemon_app

        daemon_app = create_daemon_app()
        daemon_client = TestClient(daemon_app)
        resp = daemon_client.get("/admin/sts/active")
        assert resp.status_code == 503
        assert resp.json()["error"] == "auth_required"

    def test_valid_psk_reaches_the_router(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)
        monkeypatch.setenv("GLUDD_AUTH_PSK", "unit-test-psk")
        from general_ludd.daemon import create_daemon_app

        daemon_app = create_daemon_app()
        daemon_client = TestClient(daemon_app)
        resp = daemon_client.get(
            "/admin/sts/active", headers={"Authorization": "Bearer unit-test-psk"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"tokens": []}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
