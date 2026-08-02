"""D-15 OpenBao scope, lease-bound, and terminal-revocation contracts."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pydantic import ValidationError

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import (
    AgentConfig,
    AgentPermission,
    AgentTask,
    AgentType,
)
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import AppRoleCreds, SecretAlias, SecretsManager
from general_ludd.secrets.openbao_scope import (
    OpenBaoPathScope,
    OpenBaoScopeDenied,
    OpenBaoScopeEvidence,
    OpenBaoScopeRequest,
)
from general_ludd.sts.injector import SubagentTokenInjector
from general_ludd.sts.minter import TokenMinter


@pytest.mark.parametrize(
    ("mount", "path"),
    [
        ("../secret", "data/tenants/acme/agents/a1/*"),
        ("secret/../sys", "data/tenants/acme/agents/a1/*"),
        ("sys", "data/tenants/acme/agents/a1/*"),
        ("secret", "../tenants/acme"),
        ("secret", "data/tenants/acme/../other"),
        ("secret", "%2e%2e/tenants/acme"),
        ("secret", "/data/tenants/acme"),
        ("secret", "data/*/acme"),
    ],
)
def test_scope_rejects_reserved_or_traversing_mounts_and_paths(
    mount: str,
    path: str,
) -> None:
    with pytest.raises(ValueError):
        OpenBaoPathScope(
            mount=mount,
            paths=(path,),
            capabilities=frozenset({"read"}),
        )


def test_secret_alias_rejects_mount_traversal_before_registration() -> None:
    with pytest.raises(ValueError, match="mount"):
        SecretAlias("model-key", "models/api-key", mount="secret/../sys")


def test_scope_intersection_selects_only_narrower_child_paths_and_actions() -> None:
    parent = OpenBaoPathScope(
        mount="secret",
        paths=("data/tenants/acme/*",),
        capabilities=frozenset({"read", "list", "update"}),
    )
    requested = OpenBaoPathScope(
        mount="secret",
        paths=("data/tenants/acme/agents/a1/*",),
        capabilities=frozenset({"read", "list", "delete"}),
    )

    granted = parent.intersect(requested)

    assert granted.paths == ("data/tenants/acme/agents/a1/*",)
    assert granted.capabilities == frozenset({"read", "list"})
    policy = granted.render_policy("gludd-agent-a1")
    assert 'path "secret/data/tenants/acme/agents/a1/*"' in policy
    assert "delete" not in policy
    assert "update" not in policy
    assert "agents/a2" not in policy
    assert 'path "sys/' not in policy


@pytest.mark.parametrize(
    "requested_path",
    (
        "data/tenants/acme/agents/a2/*",
        "data/tenants/other/agents/a1/*",
        "data/tenants/acme-parent-only",
    ),
)
def test_scope_intersection_denies_disjoint_sibling_and_parent_only_paths(
    requested_path: str,
) -> None:
    parent = OpenBaoPathScope(
        mount="secret",
        paths=("data/tenants/acme/agents/a1/*",),
        capabilities=frozenset({"read"}),
    )
    requested = OpenBaoPathScope(
        mount="secret",
        paths=(requested_path,),
        capabilities=frozenset({"read"}),
    )

    with pytest.raises(OpenBaoScopeDenied, match="no common path"):
        parent.intersect(requested)


def test_scope_evidence_is_typed_deterministic_and_contains_no_raw_identity_or_path() -> None:
    scope = OpenBaoPathScope(
        mount="secret",
        paths=("data/tenants/acme/agents/private-agent/*",),
        capabilities=frozenset({"read"}),
    )

    first = scope.evidence(event_type="scope_granted", subject_id="private-agent")
    second = scope.evidence(event_type="scope_granted", subject_id="private-agent")

    assert isinstance(first, OpenBaoScopeEvidence)
    assert first == second
    payload = first.as_dict()
    rendered = repr(first) + str(payload)
    assert payload["event_type"] == "scope_granted"
    assert payload["path_count"] == 1
    assert len(str(payload["scope_hash"])) == 32
    assert "private-agent" not in rendered
    assert "data/tenants" not in rendered
    assert "secret/" not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approle_secret_id_ttl_seconds", 0),
        ("approle_token_ttl_seconds", 0),
        ("approle_token_max_ttl_seconds", 0),
        ("approle_secret_id_num_uses", 0),
        ("approle_token_num_uses", 0),
        ("approle_token_num_uses", 100_001),
    ],
)
def test_openbao_config_rejects_unbounded_or_excessive_lease_limits(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        OpenBaoConfig(**{field: value})


def test_openbao_config_rejects_token_ttl_above_explicit_maximum() -> None:
    with pytest.raises(ValidationError, match="token TTL"):
        OpenBaoConfig(
            approle_token_ttl_seconds=601,
            approle_token_max_ttl_seconds=600,
        )


def test_scoped_approle_attaches_exact_policy_and_finite_limits() -> None:
    client = MagicMock()
    client.auth.approle.read_role_id.return_value = {"data": {"role_id": "r-1"}}
    client.auth.approle.generate_secret_id.return_value = {
        "data": {"secret_id": "s-private", "secret_id_accessor": "accessor-1"}
    }
    manager = SecretsManager(
        client=client,
        config=OpenBaoConfig(
            approle_secret_id_ttl_seconds=300,
            approle_token_ttl_seconds=600,
            approle_token_max_ttl_seconds=900,
            approle_secret_id_num_uses=1,
            approle_token_num_uses=7,
        ),
    )

    creds = manager.setup_approle(
        "agent-a1",
        policy_name="gludd-agent-a1",
        policy_hcl='path "secret/data/tenants/acme/agents/a1/*" {}',
    )

    assert creds == AppRoleCreds(role_id="r-1", secret_id="s-private")
    client.sys.create_or_update_policy.assert_called_once_with(
        name="gludd-agent-a1",
        policy='path "secret/data/tenants/acme/agents/a1/*" {}',
    )
    client.auth.approle.create_role.assert_called_once_with(
        "agent-a1",
        bind_secret_id=True,
        secret_id_ttl=300,
        secret_id_num_uses=1,
        token_ttl=600,
        token_max_ttl=900,
        token_explicit_max_ttl=900,
        token_num_uses=7,
        token_policies=["gludd-agent-a1"],
        token_no_default_policy=True,
    )


def test_scoped_approle_rolls_back_policy_when_role_creation_fails() -> None:
    client = MagicMock()
    client.auth.approle.create_role.side_effect = RuntimeError("backend down")
    manager = SecretsManager(client=client)

    with pytest.raises(RuntimeError, match="backend down"):
        manager.setup_approle(
            "agent-a1",
            policy_name="gludd-agent-a1",
            policy_hcl='path "secret/data/tenants/acme/agents/a1/*" {}',
        )

    client.sys.delete_policy.assert_called_once_with(name="gludd-agent-a1")


@pytest.mark.asyncio
async def test_token_minter_provisions_intersection_and_emits_redacted_evidence() -> None:
    manager = MagicMock()
    manager.setup_approle.return_value = AppRoleCreds("role-1", "secret-1")
    evidence_sink = MagicMock()
    minter = TokenMinter(manager, scope_evidence_sink=evidence_sink)
    request = OpenBaoScopeRequest(
        parent=OpenBaoPathScope(
            mount="secret",
            paths=("data/tenants/acme/*",),
            capabilities=frozenset({"read", "list"}),
        ),
        requested=OpenBaoPathScope(
            mount="secret",
            paths=("data/tenants/acme/agents/a1/*",),
            capabilities=frozenset({"read"}),
        ),
    )

    await minter.mint("a1", "parent-private", scope=request)

    kwargs = manager.setup_approle.call_args.kwargs
    assert manager.setup_approle.call_args.args == ("agent-a1",)
    assert kwargs["policy_name"].startswith("gludd-agent-")
    assert "secret/data/tenants/acme/agents/a1/*" in kwargs["policy_hcl"]
    evidence = evidence_sink.call_args.args[0]
    assert isinstance(evidence, OpenBaoScopeEvidence)
    assert "parent-private" not in repr(evidence)
    assert "agents/a1" not in repr(evidence)


@pytest.mark.asyncio
async def test_injector_revokes_for_every_terminal_state() -> None:
    revoker = AsyncMock()
    injector = SubagentTokenInjector(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        revoker=revoker,
    )

    for state in ("completed", "failed", "cancelled", "timed_out"):
        await injector.finalize("agent-a1", terminal_state=state)

    assert revoker.revoke.await_args_list == [
        call("agent-a1", terminal_state="completed"),
        call("agent-a1", terminal_state="failed"),
        call("agent-a1", terminal_state="cancelled"),
        call("agent-a1", terminal_state="timed_out"),
    ]


def _dispatcher() -> AgentDispatcher:
    registry = AgentRegistry()
    registry.register(
        AgentConfig(
            name="root",
            description="root",
            type=AgentType.PRIMARY,
            model_profile="local",
            permissions=AgentPermission(
                can_read=True,
                can_dispatch_subagents=True,
                allowed_subagents=["worker"],
            ),
        )
    )
    registry.register(
        AgentConfig(
            name="worker",
            description="worker",
            type=AgentType.SUBAGENT,
            model_profile="local",
            permissions=AgentPermission(can_read=True),
        )
    )
    return AgentDispatcher(registry)


def _task(task_id: str) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        description="bounded local test",
        agent_name="worker",
        prompt="test",
        invoker_name="root",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("executor_result", "expected_state"),
    [("ok", "completed"), (RuntimeError("failed"), "failed")],
)
async def test_dispatcher_revokes_after_success_and_failure(
    executor_result: str | BaseException,
    expected_state: str,
) -> None:
    dispatcher = _dispatcher()

    async def executor(_task: AgentTask) -> str:
        if isinstance(executor_result, BaseException):
            raise executor_result
        return executor_result

    dispatcher._executor = executor
    injector = MagicMock()
    injector.enrich = AsyncMock()
    injector.finalize = AsyncMock()
    dispatcher.set_sts_injector(injector)

    await dispatcher.dispatch_one(_task(f"task-{expected_state}"))

    injector.finalize.assert_awaited_once_with(
        f"task-{expected_state}",
        terminal_state=expected_state,
    )


@pytest.mark.asyncio
async def test_dispatcher_revokes_after_cancellation() -> None:
    dispatcher = _dispatcher()

    async def cancel(_task: AgentTask) -> str:
        raise asyncio.CancelledError

    dispatcher._executor = cancel
    injector = MagicMock()
    injector.enrich = AsyncMock()
    injector.finalize = AsyncMock()
    dispatcher.set_sts_injector(injector)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.dispatch_one(_task("task-cancelled"))

    injector.finalize.assert_awaited_once_with(
        "task-cancelled",
        terminal_state="cancelled",
    )
