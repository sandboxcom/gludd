"""Task #25 — per-project secret isolation is WIRED into the job secret path.

Regression guard for the dead-code gap: ``daemon._LazyProjectSecrets.for_project``
(and the ``ProjectSecretsManager`` path-scoping it builds) previously had ZERO
callers on the job-execution path, so every project resolved its model
credential/api-base aliases through the SHARED base manager. These tests prove
the wiring now engages at the gateway resolve site — ``ModelGateway`` resolves a
job's aliases through ``resolver.for_project(project_id)`` — and that the
no-project / non-project-aware fallback is non-breaking.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.secrets.project_secrets import ProjectSecretsManager


class _FakeBase:
    """A KV-capable base secrets manager keyed by SCOPED path.

    ``read_secret(path)`` serves the per-project scoped store (the path a
    ``ProjectSecretsManager`` produces: ``projects/<id>/<alias>``); ``resolve``
    is the SHARED/base fallback (fail-closed: only allow-listed shared aliases).
    """

    def __init__(self) -> None:
        # SAME alias name "API_KEY" → DIFFERENT scoped value per project.
        self._kv: dict[str, dict[str, str]] = {
            "projects/proj-a/API_KEY": {"value": "key-A"},
            "projects/proj-b/API_KEY": {"value": "key-B"},
            # An alias that ONLY exists under proj-b's scope.
            "projects/proj-b/B_ONLY": {"value": "b-only-secret"},
        }
        self._shared: dict[str, str] = {"API_KEY": "shared-key"}

    def read_secret(self, path: str) -> dict[str, str] | None:
        return self._kv.get(path)

    def resolve(self, alias_name: str) -> str | None:
        return self._shared.get(alias_name)


class _ProjectAwareResolver:
    """Mirrors ``daemon._LazyProjectSecrets``: plain ``resolve`` + ``for_project``."""

    def __init__(self, base: Any) -> None:
        self._base = base

    def resolve(self, alias_name: str) -> str | None:
        result = self._base.resolve(alias_name)
        return result if isinstance(result, str) else None

    def for_project(self, project_id: str) -> ProjectSecretsManager:
        return ProjectSecretsManager(base_manager=self._base, project_id=project_id)


def _make_gateway(captured: dict[str, Any], resolver: Any, *, alias: str = "API_KEY") -> ModelGateway:
    """Build a gateway whose stubbed provider records the api_key it receives."""

    class _FakeProvider:
        def __init__(self, **kwargs: Any) -> None:
            captured["api_key"] = kwargs.get("api_key")

        def invoke(self, messages: Any) -> Any:
            class _Resp:
                def __init__(self) -> None:
                    self.content = "ok"
                    self.usage_metadata = {"input_tokens": 1, "output_tokens": 1}

            return _Resp()

    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = _FakeProvider

    profile = ModelProfile(
        model_profile_id="test",
        provider="openai",
        credential_alias=alias,
        model_name="test-model",
    )
    return ModelGateway(
        profiles=[profile],
        secrets_manager=resolver,
        provider_registry=registry,
    )


def _call(gateway: ModelGateway, captured: dict[str, Any], project_id: str | None) -> str | None:
    gateway.call_model(
        "test",
        messages=[{"role": "user", "content": "hi"}],
        work_type="code",
        project_id=project_id,
    )
    return captured["api_key"]


class TestPerProjectSecretIsolationEngages:
    def test_each_project_resolves_only_its_own_scoped_secret(self) -> None:
        # The SAME alias "API_KEY" maps to a DIFFERENT scoped value per project;
        # each job must resolve ONLY its own project's value.
        captured: dict[str, Any] = {}
        gateway = _make_gateway(captured, _ProjectAwareResolver(_FakeBase()))
        assert _call(gateway, captured, "proj-a") == "key-A"
        assert _call(gateway, captured, "proj-b") == "key-B"

    def test_cross_project_alias_is_denied(self) -> None:
        # An alias that exists ONLY under proj-b's scope must NOT resolve for
        # proj-a: the scoped read misses and the fail-closed base fallback (which
        # does not carry "B_ONLY") returns None. proj-b still gets its value.
        captured: dict[str, Any] = {}
        gateway = _make_gateway(
            captured, _ProjectAwareResolver(_FakeBase()), alias="B_ONLY"
        )
        assert _call(gateway, captured, "proj-a") is None
        assert _call(gateway, captured, "proj-b") == "b-only-secret"

    def test_project_none_falls_back_to_shared_base(self) -> None:
        # Regression: a non-project job (project_id=None) resolves through the
        # shared base exactly as before — non-breaking.
        captured: dict[str, Any] = {}
        gateway = _make_gateway(captured, _ProjectAwareResolver(_FakeBase()))
        assert _call(gateway, captured, None) == "shared-key"

    def test_non_project_aware_resolver_still_resolves(self) -> None:
        # Regression: when the injected resolver has NO ``for_project`` (e.g. a
        # plain EnvSecretsManager while projects are inactive), a job with a
        # project_id still resolves through the base — never crashes.
        captured: dict[str, Any] = {}

        class _PlainResolver:
            def resolve(self, alias_name: str) -> str | None:
                return {"API_KEY": "plain-key"}.get(alias_name)  # pragma: allowlist secret

        gateway = _make_gateway(captured, _PlainResolver())
        assert _call(gateway, captured, "proj-a") == "plain-key"


class TestResolverForProjectHelper:
    def test_scopes_when_resolver_is_project_aware(self) -> None:
        gateway = _make_gateway({}, _ProjectAwareResolver(_FakeBase()))
        scoped = gateway._resolver_for_project("proj-a")
        assert isinstance(scoped, ProjectSecretsManager)
        assert scoped.resolve("API_KEY") == "key-A"

    def test_returns_base_when_project_id_none(self) -> None:
        resolver = _ProjectAwareResolver(_FakeBase())
        gateway = _make_gateway({}, resolver)
        assert gateway._resolver_for_project(None) is resolver

    def test_returns_base_when_resolver_not_project_aware(self) -> None:
        class _PlainResolver:
            def resolve(self, alias_name: str) -> str | None:
                return None

        resolver = _PlainResolver()
        gateway = _make_gateway({}, resolver)
        assert gateway._resolver_for_project("proj-a") is resolver

    def test_malformed_project_id_fails_closed_to_base(self) -> None:
        # A project_id containing '/' or '..' makes ProjectSecrets.__init__ raise
        # ValueError; the gateway must fall back to the base rather than crash.
        resolver = _ProjectAwareResolver(_FakeBase())
        gateway = _make_gateway({}, resolver)
        assert gateway._resolver_for_project("proj/../other") is resolver


@pytest.mark.parametrize("param", ["project_id"])
def test_invoke_model_for_generation_accepts_project_id(param: str) -> None:
    import inspect

    from general_ludd.models.job_invocation import invoke_model_for_generation

    sig = inspect.signature(invoke_model_for_generation)
    assert param in sig.parameters
