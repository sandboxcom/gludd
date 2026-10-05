"""End-to-end integration of the human-permission-escalation flow.

Walks the four flows described in AGENTS.md "Human Permission Subjects +
Intersection Policy" and "Human Todo System":

  Flow 1 — Subagent gets min(user, agent) permissions.
            EventLoop._resolve_permission_spec must call
            PermissionSpecParser.intersection(human_spec, agent_spec) and
            consult ``default_human_role``.

  Flow 2 — Agent files an escalation request.
            POST /admin/perm/escalation-request must:
              * require alternatives_tried with >=3 distinct entries,
              * auto-approve when requested ⊆ human ∩ agent (STS minted),
              * create a HumanTodo(category=permission_escalation) when the
                request is outside the intersection (the wiring this test
                pins — the prior code set status=pending and stopped there).

  Flow 3 — Human resolves the escalation.
            The escalation endpoint and the human-todo endpoint must both
            propagate the decision: approve/deny on the escalation resolves
            the linked HumanTodo; done/dismissed on the HumanTodo updates the
            escalation row.

  Flow 4 — Agent resumes with human input.
            When a human-todo linked to a parent agent todo resolves, the
            resolution text must be reachable on the next dispatch as
            ``human_input`` (EventLoop helper).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import HumanTodoRepository, TodoRepository
from general_ludd.schemas.todo import TodoStatus

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}


async def _make_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


# ---------------------------------------------------------------------------
# Flow 1 — Intersection resolver
# ---------------------------------------------------------------------------


class TestFlow1Intersection:
    @pytest.mark.asyncio
    async def test_resolve_permission_spec_intersects_human_and_agent(self):
        """EventLoop._resolve_permission_spec consults default_human_role and
        returns PermissionSpecParser.intersection(human_spec, agent_spec)."""
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.security.permissions import (
            PermissionSpecParser,
            default_human_spec,
        )

        config = {
            "default_human_role": "human-viewer",
            "queues": [
                {
                    "name": "core",
                    "permission_spec": {
                        "capabilities": [
                            {
                                "resource": "file:repo",
                                "actions": ["read", "write"],
                                "constraints": {"path_prefix": "/repo/"},
                            },
                            # Agent can connect to any host, but the human
                            # viewer is restricted to LLM APIs. The intersection
                            # must NARROW this to LLM APIs only.
                            {
                                "resource": "net:egress:any",
                                "actions": ["connect"],
                                "constraints": {"allowed_hosts": ["*"]},
                            },
                        ],
                    },
                }
            ],
        }
        loop = EventLoop(config=config)

        class _T:
            todo_id = "TODO-1"
            queue = "core"

        spec = loop._resolve_permission_spec(_T())
        assert spec is not None, "expected a resolved spec"

        # Sanity: the human-viewer default has only file:repo.read.
        human = default_human_spec("human-viewer")
        agent_caps_resources = {c.resource for c in spec.capabilities}
        # The intersection must be a SUBSET of the human spec — never wider.
        is_sub = PermissionSpecParser.is_subset(
            spec,
            human,
        )
        assert is_sub, (
            f"effective spec must be a subset of human spec; "
            f"got resources={agent_caps_resources}"
        )
        # And specifically: the file:repo write action the agent had must be
        # dropped (human-viewer only has read).
        file_cap = next(
            (c for c in spec.capabilities if c.resource == "file:repo"), None
        )
        if file_cap is not None:
            assert "write" not in set(file_cap.actions), (
                "human-viewer cannot write file:repo; intersection must drop it"
            )

    @pytest.mark.asyncio
    async def test_default_human_role_defaults_to_human_operator(self):
        """When default_human_role is unset, the resolver falls back to
        human-operator (never to None / unmuted agent scope)."""
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.security.permissions import (
            PermissionSpecParser,
            default_human_spec,
        )

        config = {
            "queues": [
                {
                    "name": "core",
                    "permission_spec": {
                        "capabilities": [
                            {
                                "resource": "file:repo",
                                "actions": ["read", "write"],
                                "constraints": {"path_prefix": "/repo/"},
                            },
                        ],
                    },
                }
            ],
        }
        loop = EventLoop(config=config)

        class _T:
            todo_id = "TODO-2"
            queue = "core"

        spec = loop._resolve_permission_spec(_T())
        assert spec is not None
        operator = default_human_spec("human-operator")
        assert PermissionSpecParser.is_subset(spec, operator)


# ---------------------------------------------------------------------------
# Flow 2 — Escalation request
# ---------------------------------------------------------------------------


def _escalation_payload(*, requested_caps, alternatives_count=3) -> dict:
    alts = [
        {"approach": f"approach-{i}", "outcome": f"failed-{i}"}
        for i in range(alternatives_count)
    ]
    return {
        "agent_id": "agent-7",
        "current_spec_yaml": (
            "version: 1\n"
            "agent_type: agent-7\n"
            "capabilities:\n"
            "  - resource: file:repo\n"
            "    actions: ['read']\n"
            "    constraints: {path_prefix: '/repo/'}\n"
        ),
        "requested_additional_capabilities": requested_caps,
        "reason": "need broader access to complete the task",
        "alternatives_tried": alts,
    }


class TestFlow2EscalationRequest:
    @pytest.mark.asyncio
    async def test_alternatives_lt_3_rejected(self, monkeypatch):
        engine, _f, client, _app = await _make_app(monkeypatch)
        try:
            payload = _escalation_payload(
                requested_caps=[
                    {
                        "resource": "file:repo",
                        "actions": ["write"],
                        "constraints": {"path_prefix": "/repo/"},
                    }
                ],
                alternatives_count=2,
            )
            resp = await client.post(
                "/admin/perm/escalation-request", json=payload, headers=AUTH
            )
            assert resp.status_code == 422, resp.text
            assert "alternatives_tried" in resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_auto_approval_when_requested_within_intersection(
        self, monkeypatch
    ):
        engine, _f, client, app = await _make_app(monkeypatch)
        try:
            # Make both issuer and human spec WIDE on the secret:openbao
            # family so the narrower requested cap is a strict subset of both.
            from general_ludd.security.permissions import (
                Capability,
                PermissionSpec,
                PermissionSubject,
            )

            wide = PermissionSpec(
                agent_type="wide-issuer",
                subject=PermissionSubject.HUMAN,
                capabilities=[
                    Capability(
                        resource="secret:openbao",
                        actions=["read"],
                        constraints={
                            "openbao_paths": [
                                "secret/data/gludd/*",
                                "secret/data/gludd/build/*",
                            ]
                        },
                    ),
                ],
            )
            app.state._sts_issuer_spec = wide
            app.state._human_spec = wide
            payload = _escalation_payload(
                requested_caps=[
                    {
                        "resource": "secret:openbao",
                        "actions": ["read"],
                        "constraints": {
                            "openbao_paths": ["secret/data/gludd/build/*"]
                        },
                    }
                ],
            )
            resp = await client.post(
                "/admin/perm/escalation-request", json=payload, headers=AUTH
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["status"] == "auto_approved", body
            assert body["sts_token_id"] is not None, body
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_pending_files_human_todo_permission_escalation(
        self, monkeypatch
    ):
        """When the request is OUTSIDE the human ∩ agent intersection, the
        escalation endpoint must file a HumanTodo(category=permission_escalation)
        so the human can see it in their queue."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            # Narrow the human+agent so the requested cap is OUTSIDE.
            from general_ludd.security.permissions import (
                Capability,
                PermissionSpec,
                PermissionSubject,
            )

            narrow = PermissionSpec(
                agent_type="narrow-issuer",
                subject=PermissionSubject.AGENT,
                capabilities=[
                    Capability(
                        resource="file:repo",
                        actions=["read"],
                        constraints={"path_prefix": "/repo/"},
                    ),
                ],
            )
            app.state._sts_issuer_spec = narrow
            app.state._human_spec = narrow

            payload = _escalation_payload(
                requested_caps=[
                    # Requesting net:egress — outside the narrow spec.
                    {
                        "resource": "net:egress:any",
                        "actions": ["connect"],
                        "constraints": {"allowed_hosts": ["evil.example.com"]},
                    }
                ],
            )
            resp = await client.post(
                "/admin/perm/escalation-request", json=payload, headers=AUTH
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["status"] == "pending", body
            esc_id = body["id"]
            assert body.get("human_todo_id"), (
                "pending escalation must surface a HumanTodo id"
            )

            # The human-todo queue MUST contain a permission_escalation entry
            # linked to this escalation.
            resp2 = await client.get(
                "/api/human-todos",
                params={"category": "permission_escalation"},
            )
            assert resp2.status_code == 200
            rows = resp2.json()
            matching = [r for r in rows if r.get("title", "").find(f"#{esc_id}") >= 0]
            assert matching, (
                f"expected a human-todo for escalation #{esc_id}; got {rows}"
            )
            ht = matching[0]
            assert ht["category"] == "permission_escalation"
            assert ht["status"] == "open"
        finally:
            await client.aclose()
            await engine.dispose()


# ---------------------------------------------------------------------------
# Flow 3 — Human resolves the escalation
# ---------------------------------------------------------------------------


async def _seed_pending_escalation(client, app) -> tuple[int, str]:
    """File an out-of-intersection escalation; return (esc_id, human_todo_id)."""
    from general_ludd.security.permissions import (
        Capability,
        PermissionSpec,
        PermissionSubject,
    )

    narrow = PermissionSpec(
        agent_type="narrow-issuer",
        subject=PermissionSubject.AGENT,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read"],
                constraints={"path_prefix": "/repo/"},
            ),
        ],
    )
    app.state._sts_issuer_spec = narrow
    app.state._human_spec = narrow

    payload = _escalation_payload(
        requested_caps=[
            {
                "resource": "net:egress:any",
                "actions": ["connect"],
                "constraints": {"allowed_hosts": ["evil.example.com"]},
            }
        ],
    )
    resp = await client.post(
        "/admin/perm/escalation-request", json=payload, headers=AUTH
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    return body["id"], body["human_todo_id"]


class TestFlow3HumanResolves:
    @pytest.mark.asyncio
    async def test_perm_approve_resolves_linked_human_todo(self, monkeypatch):
        engine, _f, client, app = await _make_app(monkeypatch)
        try:
            # Widen the spec so the approve path can mint the STS.
            from general_ludd.security.permissions import default_spec

            esc_id, ht_id = await _seed_pending_escalation(client, app)
            # Now widen so approval can succeed (the approve endpoint re-
            # intersects with human; we need the requested cap inside it).
            app.state._sts_issuer_spec = default_spec("primary")
            app.state._human_spec = default_spec("primary")

            resp = await client.post(
                f"/admin/perm/escalations/{esc_id}/approve",
                json={"reason": "ok", "human_reviewer": "shawn"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "approved"

            # The linked HumanTodo MUST be resolved to done.
            resp2 = await client.get(
                "/api/human-todos", params={"category": "permission_escalation"}
            )
            rows = resp2.json()
            matching = [r for r in rows if r["id"] == ht_id]
            assert matching, f"human-todo {ht_id} not found in {rows}"
            assert matching[0]["status"] == "done"
            assert matching[0]["human_resolver"] == "shawn"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_perm_deny_dismisses_linked_human_todo(self, monkeypatch):
        engine, _f, client, app = await _make_app(monkeypatch)
        try:
            esc_id, ht_id = await _seed_pending_escalation(client, app)
            resp = await client.post(
                f"/admin/perm/escalations/{esc_id}/deny",
                json={"reason": "not allowed", "human_reviewer": "shawn"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "denied"

            resp2 = await client.get(
                "/api/human-todos", params={"category": "permission_escalation"}
            )
            rows = resp2.json()
            matching = [r for r in rows if r["id"] == ht_id]
            assert matching, f"human-todo {ht_id} not found"
            assert matching[0]["status"] == "dismissed"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_human_todo_done_marks_escalation_approved(self, monkeypatch):
        engine, _f, client, app = await _make_app(monkeypatch)
        try:
            esc_id, ht_id = await _seed_pending_escalation(client, app)
            # Human resolves via the human-todo endpoint.
            resp = await client.patch(
                f"/api/human-todos/{ht_id}",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                    "human_resolution": "approved with caveat",
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            # The escalation row MUST reflect the resolution.
            resp2 = await client.get("/admin/perm/escalations", headers=AUTH)
            rows = resp2.json()["items"]
            matching = [r for r in rows if r["id"] == esc_id]
            assert matching, f"escalation {esc_id} not found"
            assert matching[0]["status"] == "approved"
            assert matching[0]["decided_reason"] == "approved with caveat"
        finally:
            await client.aclose()
            await engine.dispose()


# ---------------------------------------------------------------------------
# Flow 4 — Agent resumes with human_input
# ---------------------------------------------------------------------------


class TestFlow4HumanInputInjection:
    @pytest.mark.asyncio
    async def test_resolve_human_input_returns_resolution_text(self):
        """EventLoop helper returns the most-recent resolved HumanTodo's
        ``human_resolution`` for a given parent_agent_todo_id."""
        from general_ludd.event_loop.loop import EventLoop

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            loop = EventLoop(config={}, session=factory)

            # Seed a parent agent todo.
            async with factory() as session:
                todo_repo = TodoRepository(session)
                await todo_repo.create(
                    todo_data={
                        "todo_id": "TODO-FLOW4",
                        "title": "blocked work",
                        "status": TodoStatus.BLOCKED_ON_HUMAN.value,
                    }
                )
                ht_repo = HumanTodoRepository(session)
                await ht_repo.create(
                    agent_id="a1",
                    title="need input",
                    body="explain",
                    category="input_request",
                    parent_agent_todo_id="TODO-FLOW4",
                )
                await session.commit()

            # Nothing resolved yet → no human_input.
            assert await loop._resolve_human_input_for_todo("TODO-FLOW4") is None

            # Resolve the human-todo with a resolution text.
            async with factory() as session:
                ht_repo = HumanTodoRepository(session)
                rows = await ht_repo.list_all()
                ht = rows[0]
                await ht_repo.mark_done(ht.id, "shawn", "THE ANSWER IS 42")
                await session.commit()

            # Now the helper MUST surface that text.
            got = await loop._resolve_human_input_for_todo("TODO-FLOW4")
            assert got == "THE ANSWER IS 42"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_resolve_human_input_none_when_open(self):
        from general_ludd.event_loop.loop import EventLoop

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            loop = EventLoop(config={}, session=factory)

            async with factory() as session:
                ht_repo = HumanTodoRepository(session)
                await ht_repo.create(
                    agent_id="a1",
                    title="open need",
                    body="x",
                    category="input_request",
                    parent_agent_todo_id="TODO-OPEN",
                )
                await session.commit()

            assert (
                await loop._resolve_human_input_for_todo("TODO-OPEN") is None
            )
        finally:
            await engine.dispose()
