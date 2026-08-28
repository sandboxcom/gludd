"""Unit tests for the onboard package's provider wiring.

``general_ludd.onboard.SUPPORTED_PROVIDERS`` / ``get_provider()`` used to
register ``_BaseStub`` subclasses that raised ``NotImplementedError`` on
every call, silently defeating `gludd onboard aws|gcp|azure` in
non-dry-run mode even though real, unit-tested implementations existed in
``onboard/aws.py``, ``onboard/gcp.py``, and ``onboard/azure.py``. These
tests pin the wiring: every supported provider must be the REAL
implementation, never the stub.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import general_ludd.onboard as onboard_pkg
from general_ludd.onboard import (
    SUPPORTED_PROVIDERS,
    AWSOnboardProvider,
    AzureOnboardProvider,
    GCPOnboardProvider,
    OnboardProvider,
    get_provider,
)
from general_ludd.onboard import aws as aws_onboard
from general_ludd.onboard import azure as azure_onboard
from general_ludd.onboard import gcp as gcp_onboard

PROVIDERS = ("aws", "gcp", "azure")


# ---------------------------------------------------------------------------
# get_provider() returns the REAL implementation, not a stub.
# ---------------------------------------------------------------------------


class TestGetProviderReturnsRealImplementation:
    def test_unknown_provider_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="Unknown onboard provider 'missing'"):
            get_provider("missing")

    def test_aws_wraps_real_impl(self) -> None:
        # AWSOnboardProvider wraps (composition, not inheritance — see
        # onboard/__init__.py docstring) the real aws.AWSOnboardProvider.
        provider = get_provider("aws")
        assert isinstance(provider, AWSOnboardProvider)
        assert isinstance(provider._impl, aws_onboard.AWSOnboardProvider)

    def test_gcp_is_real_impl(self) -> None:
        provider = get_provider("gcp")
        assert isinstance(provider, gcp_onboard.GCPOnboardProvider)

    def test_azure_is_real_impl(self) -> None:
        provider = get_provider("azure")
        assert isinstance(provider, azure_onboard.AzureOnboardProvider)

    @pytest.mark.parametrize("name", PROVIDERS)
    def test_registered_class_conforms_to_protocol(self, name: str) -> None:
        provider = get_provider(name)
        assert isinstance(provider, OnboardProvider)
        assert provider.name == name

    def test_supported_providers_exports_match_get_provider(self) -> None:
        assert SUPPORTED_PROVIDERS["aws"] is AWSOnboardProvider
        assert SUPPORTED_PROVIDERS["gcp"] is GCPOnboardProvider
        assert SUPPORTED_PROVIDERS["azure"] is AzureOnboardProvider


class TestGetProviderKwargPassthrough:
    """`gludd onboard --project/--subscription` must reach the providers.

    Regression for the gap where cli.py defined the flags but get_provider()
    instantiated every provider with zero constructor args, silently
    no-op'ing the flags (env-var fallback only).
    """

    def test_gcp_receives_project_id(self) -> None:
        provider = get_provider("gcp", project_id="p1-from-cli")
        assert isinstance(provider, GCPOnboardProvider)
        assert provider.project_id == "p1-from-cli"
        assert "p1-from-cli" in provider.create_role_instructions()

    def test_azure_receives_subscription_id(self) -> None:
        provider = get_provider("azure", subscription_id="sub-from-cli")
        assert isinstance(provider, AzureOnboardProvider)
        assert provider.subscription_id == "sub-from-cli"
        assert "sub-from-cli" in provider.create_role_instructions()

    def test_aws_ignores_cloud_specific_kwargs(self) -> None:
        # AWS takes neither kwarg; passing them must not raise.
        provider = get_provider("aws", project_id="p1", subscription_id="sub-1")
        assert provider.name == "aws"

    def test_irrelevant_kwarg_not_forwarded_cross_provider(self) -> None:
        # subscription_id is Azure-only; GCP must not choke on it (and
        # vice versa).
        gcp = get_provider("gcp", subscription_id="sub-1")
        assert gcp.name == "gcp"
        azure = get_provider("azure", project_id="p1")
        assert azure.name == "azure"

    def test_none_values_preserve_env_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Explicit None must NOT be forwarded — the provider's own env-var
        # fallback has to keep working.
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-from-env")
        provider = get_provider("gcp", project_id=None)
        assert isinstance(provider, GCPOnboardProvider)
        assert provider.project_id == "proj-from-env"

    def test_kwarg_filter_covers_accepted_present_and_none_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The audited constructor allowlist filters both value branches."""

        captured: dict[str, str] = {}

        class _Provider:
            name = "coverage"

            def __init__(self, **kwargs: str) -> None:
                captured.update(kwargs)

        monkeypatch.setitem(SUPPORTED_PROVIDERS, "coverage", _Provider)
        monkeypatch.setitem(
            onboard_pkg._PROVIDER_INIT_KWARGS,
            "coverage",
            frozenset({"project_id", "subscription_id"}),
        )

        provider = get_provider(
            "coverage", project_id="project", subscription_id=None
        )

        assert provider.name == "coverage"
        assert captured == {"project_id": "project"}


# ---------------------------------------------------------------------------
# Guidance methods return real, non-empty content with no network access.
# ---------------------------------------------------------------------------


class TestGuidanceMethodsAreLive:
    @pytest.mark.parametrize("name", PROVIDERS)
    def test_create_role_instructions_nonempty(self, name: str) -> None:
        text = get_provider(name).create_role_instructions()
        assert isinstance(text, str)
        assert len(text.strip()) > 0
        assert "terraform apply" in text.lower()

    @pytest.mark.parametrize("name", PROVIDERS)
    def test_token_acquisition_guide_nonempty(self, name: str) -> None:
        text = get_provider(name).token_acquisition_guide()
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    def test_gcp_instructions_use_placeholder_without_project_id(self) -> None:
        # No project_id supplied and no env var set -> falls back to a
        # placeholder rather than raising or emitting "None".
        text = GCPOnboardProvider().create_role_instructions()
        assert "<PROJECT_ID>" in text
        assert "None" not in text

    def test_azure_instructions_use_placeholder_without_subscription_id(self) -> None:
        text = AzureOnboardProvider().create_role_instructions()
        assert "<SUBSCRIPTION_ID>" in text
        assert "None" not in text


# ---------------------------------------------------------------------------
# validate_token_and_role works against a mocked SDK client (no network).
# ---------------------------------------------------------------------------


class TestValidateTokenAndRoleWithFakeClient:
    def test_aws_validate_with_fake_boto3_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_sts = MagicMock()
        fake_sts.get_caller_identity.return_value = {
            "UserId": "AIDAEXAMPLE",
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:role/gludd-compute-operator",
        }
        monkeypatch.setattr(
            "general_ludd.onboard.aws._build_boto3_client",
            lambda service, region: fake_sts,
        )

        provider = get_provider("aws")
        ok, details = provider.validate_token_and_role(
            token="ignored",
            role_arn="arn:aws:iam::123456789012:role/gludd-compute-operator",
            region="us-east-1",
        )
        assert ok is True
        assert details["role_matches_expected"] is True

    def test_aws_validate_missing_boto3_is_non_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(service: str, region: str) -> Any:
            raise ImportError("boto3 unavailable")

        monkeypatch.setattr("general_ludd.onboard.aws._build_boto3_client", _raise)

        provider = get_provider("aws")
        ok, details = provider.validate_token_and_role(
            token="x", role_arn="arn:aws:iam::1:role/x", region="us-east-1"
        )
        assert ok is False
        assert "boto3" in details["detail"].lower()

    def test_gcp_validate_with_fake_discovery_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_key = tmp_path / "key.json"
        fake_key.write_text(
            json.dumps(
                {
                    "type": "service_account",
                    "project_id": "proj-123",
                    "client_email": "gludd-compute-operator@proj-123.iam.gserviceaccount.com",
                }
            )
        )
        sa_email = "gludd-compute-operator@proj-123.iam.gserviceaccount.com"

        fake_discovery = MagicMock()
        fake_compute = MagicMock()
        fake_compute.instances.list.return_value.execute.return_value = {"items": []}
        fake_discovery.build.return_value = fake_compute

        policy = {
            "bindings": [
                {"role": r, "members": [f"serviceAccount:{sa_email}"]}
                for r in gcp_onboard.EXPECTED_ROLES
            ] + [{
                "role": (
                    "projects/proj-123/roles/"
                    f"{gcp_onboard.CUSTOM_ROLE_SUFFIX}"
                ),
                "members": [f"serviceAccount:{sa_email}"],
            }]
        }
        policy["bindings"].append(
            {
                "role": f"projects/proj-123/roles/{gcp_onboard.CUSTOM_ROLE_SUFFIX}",
                "members": [f"serviceAccount:{sa_email}"],
            }
        )
        monkeypatch.setattr(gcp_onboard, "_build_gcp_client", lambda **kw: fake_discovery)
        monkeypatch.setattr(gcp_onboard, "_get_iam_policy", lambda client, project_id: policy)

        provider = get_provider("gcp")
        ok, details = provider.validate_token_and_role(
            token=str(fake_key), role_arn=sa_email, region="us-east-1"
        )
        assert ok is True
        assert details["missing"] == []

    def test_gcp_validate_unresolvable_project_is_non_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No token_path/project_id resolvable anywhere -> the module-level
        # function raises; the adapter must convert that to (False, {...}).
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
        provider = GCPOnboardProvider(project_id=None)
        ok, details = provider.validate_token_and_role(
            token="", role_arn="", region="us-east-1"
        )
        assert ok is False
        assert "detail" in details

    def test_azure_validate_with_fake_compute_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_compute = MagicMock()
        fake_compute.virtual_machines.list.return_value = MagicMock(next=MagicMock())
        all_assignments = [
            {"role_definition_name": r, "principal_id": "principal-1"}
            for r in azure_onboard.EXPECTED_ROLES
        ]
        monkeypatch.setattr(
            azure_onboard, "_build_azure_client", lambda **kw: fake_compute
        )
        monkeypatch.setattr(
            azure_onboard, "_get_role_assignments", lambda sub, principal: all_assignments
        )

        # subscription_id must be supplied at construction (mirrors the
        # generic --subscription CLI arg) since the CLI's generic
        # (token, role_arn, region) call has no subscription-id slot.
        provider = AzureOnboardProvider(subscription_id="00000000-0000-0000-0000-000000000000")
        ok, details = provider.validate_token_and_role(
            token="unused", role_arn="principal-1", region="eastus"
        )
        assert ok is True
        assert details["missing"] == []

    def test_azure_validate_missing_subscription_is_non_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
        provider = AzureOnboardProvider(subscription_id=None)
        ok, details = provider.validate_token_and_role(
            token="", role_arn="", region="eastus"
        )
        assert ok is False
        assert "detail" in details


# ---------------------------------------------------------------------------
# Regression: no provider in SUPPORTED_PROVIDERS may raise NotImplementedError.
# ---------------------------------------------------------------------------


class TestNoProviderIsAStub:
    @pytest.mark.parametrize("name", PROVIDERS)
    def test_create_role_instructions_never_not_implemented(self, name: str) -> None:
        provider = get_provider(name)
        try:
            provider.create_role_instructions()
        except NotImplementedError:
            pytest.fail(f"provider '{name}' is still a stub (create_role_instructions)")

    @pytest.mark.parametrize("name", PROVIDERS)
    def test_token_acquisition_guide_never_not_implemented(self, name: str) -> None:
        provider = get_provider(name)
        try:
            provider.token_acquisition_guide()
        except NotImplementedError:
            pytest.fail(f"provider '{name}' is still a stub (token_acquisition_guide)")

    @pytest.mark.parametrize("name", PROVIDERS)
    def test_validate_token_and_role_never_not_implemented(
        self, name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Block any real network/SDK call by making the per-provider client
        # builder raise, so this only exercises the non-raising contract —
        # never a live AWS/GCP/Azure call.
        def _no_network(*_args: object, **_kwargs: object) -> Any:
            raise RuntimeError("no network in tests")

        monkeypatch.setattr("general_ludd.onboard.aws._build_boto3_client", _no_network)
        monkeypatch.setattr(gcp_onboard, "_build_gcp_client", _no_network)
        monkeypatch.setattr(azure_onboard, "_build_azure_client", _no_network)

        provider = get_provider(name)
        try:
            # Deliberately garbage inputs — we only care that the failure
            # mode is a handled (False, {...}) result, never NotImplementedError.
            result = provider.validate_token_and_role("x", "y", "z")
        except NotImplementedError:
            pytest.fail(f"provider '{name}' is still a stub (validate_token_and_role)")
        else:
            assert isinstance(result, tuple)
            assert len(result) == 2
            ok, details = result
            assert isinstance(ok, bool)
            assert isinstance(details, dict)
