"""Fail-closed tests for the paid Azure live-proof GitHub Environment."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from scripts import verify_azure_containerapp_environment as guard_cli

from general_ludd.azure_containerapp_environment_guard import (
    EnvironmentProtectionError,
    verify_environment_protection,
)

ROOT = Path(__file__).resolve().parents[2]
Mutator = Callable[[dict[str, object], dict[str, object]], None]


def _environment_payload() -> dict[str, object]:
    return {
        "id": 42,
        "name": "azure-containerapp-live",
        "can_admins_bypass": False,
        "protection_rules": [
            {
                "id": 7,
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [
                    {
                        "type": "Team",
                        "reviewer": {
                            "id": 123,
                            "slug": "cloud-operators",
                        },
                    }
                ],
            },
            {"id": 8, "type": "branch_policy"},
        ],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }


def _branch_policy_payload() -> dict[str, object]:
    policies = [
        {"id": 1, "name": "master"},
        {"id": 2, "name": "development"},
        {"id": 3, "name": "v*"},
    ]
    return {"total_count": len(policies), "branch_policies": policies}


def _reviewer_rule(environment: dict[str, object]) -> dict[str, object]:
    rules = environment["protection_rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    return rule


def _policy_list(policies: dict[str, object]) -> list[object]:
    policy_list = policies["branch_policies"]
    assert isinstance(policy_list, list)
    return policy_list


def _append_reviewer_rule(
    environment: dict[str, object],
    _policies: dict[str, object],
) -> None:
    rules = environment["protection_rules"]
    assert isinstance(rules, list)
    rules.append(_reviewer_rule(environment).copy())


def _invalidate_first_policy(
    _environment: dict[str, object],
    policies: dict[str, object],
) -> None:
    policy = _policy_list(policies)[0]
    assert isinstance(policy, dict)
    policy["name"] = None


def _duplicate_policy(
    _environment: dict[str, object],
    policies: dict[str, object],
) -> None:
    _policy_list(policies).append({"id": 4, "name": "master"})
    policies["total_count"] = 4


def test_verified_environment_receipt_excludes_reviewer_identity() -> None:
    receipt = verify_environment_protection(
        _environment_payload(),
        _branch_policy_payload(),
    )

    assert receipt.environment == "azure-containerapp-live"
    assert receipt.reviewer_count == 1
    assert receipt.branch_policies == ("development", "master", "v*")
    assert len(receipt.configuration_digest) == 64
    assert "cloud-operators" not in receipt.render()
    assert "mutation=false" in receipt.render()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda environment, _policies: environment.update(name="production"),
            "environment-name-mismatch",
        ),
        (
            lambda environment, _policies: environment.update(protection_rules=[]),
            "required-reviewers-rule-missing",
        ),
        (
            lambda environment, _policies: environment.update(
                protection_rules="invalid"
            ),
            "protection-rules-invalid",
        ),
        (
            lambda environment, _policies: environment.update(protection_rules=[None]),
            "protection-rules-invalid",
        ),
        (
            _append_reviewer_rule,
            "required-reviewers-rule-ambiguous",
        ),
        (
            lambda environment, _policies: _reviewer_rule(environment).update(
                prevent_self_review=False
            ),
            "prevent-self-review-disabled",
        ),
        (
            lambda environment, _policies: _reviewer_rule(environment).update(reviewers=[]),
            "reviewers-missing",
        ),
        (
            lambda environment, _policies: environment.update(
                can_admins_bypass=True
            ),
            "admin-bypass-enabled",
        ),
        (
            lambda environment, _policies: environment.update(
                deployment_branch_policy={
                    "protected_branches": True,
                    "custom_branch_policies": False,
                }
            ),
            "branch-policy-mode-invalid",
        ),
        (
            lambda _environment, policies: _policy_list(policies).append(
                {"id": 4, "name": "feature/*"}
            ),
            "branch-policy-count-mismatch",
        ),
        (
            lambda _environment, policies: policies.update(
                total_count=3,
                branch_policies=[
                    {"id": 1, "name": "master"},
                    {"id": 2, "name": "development"},
                    {"id": 3, "name": "release/*"},
                ],
            ),
            "branch-policy-set-mismatch",
        ),
        (
            _invalidate_first_policy,
            "branch-policy-list-invalid",
        ),
        (
            _duplicate_policy,
            "branch-policy-duplicate",
        ),
    ],
)
def test_environment_drift_is_rejected_with_bounded_reason(
    mutate: Mutator,
    code: str,
) -> None:
    environment = _environment_payload()
    policies = _branch_policy_payload()
    mutate(environment, policies)

    with pytest.raises(EnvironmentProtectionError, match=f"^{code}$"):
        verify_environment_protection(environment, policies)


def test_invalid_reviewer_shape_is_rejected() -> None:
    environment = _environment_payload()
    _reviewer_rule(environment)["reviewers"] = [
        {"type": "User", "reviewer": {"id": 0}}
    ]

    with pytest.raises(EnvironmentProtectionError, match=r"^reviewer-invalid$"):
        verify_environment_protection(environment, _branch_policy_payload())


@pytest.mark.parametrize(
    ("reviewers", "code"),
    [
        ([{"type": "App", "reviewer": {"id": 1}}], "reviewer-invalid"),
        (
            [
                {"type": "User", "reviewer": {"id": 1}},
                {"type": "User", "reviewer": {"id": 1}},
            ],
            "reviewer-duplicate",
        ),
    ],
)
def test_reviewer_identity_contract_is_fail_closed(
    reviewers: list[object],
    code: str,
) -> None:
    environment = _environment_payload()
    _reviewer_rule(environment)["reviewers"] = reviewers

    with pytest.raises(EnvironmentProtectionError, match=f"^{code}$"):
        verify_environment_protection(environment, _branch_policy_payload())


def test_live_adapter_performs_only_two_bounded_gets() -> None:
    calls: list[Sequence[str]] = []
    outputs = iter(
        [
            json.dumps(_environment_payload()),
            json.dumps(_branch_policy_payload()),
        ]
    )

    def runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, next(outputs), "")

    receipt = guard_cli.run_guard(
        repository="sandboxcom/gludd",
        environment="azure-containerapp-live",
        environment_json=None,
        branch_policies_json=None,
        validate_only=False,
        runner=runner,
    )

    assert receipt.reviewer_count == 1
    assert len(calls) == 2
    assert all(call[:4] == ["gh", "api", "--method", "GET"] for call in calls)
    assert all("POST" not in call and "PUT" not in call for call in calls)
    assert calls[0][-1] == (
        "repos/sandboxcom/gludd/environments/azure-containerapp-live"
    )
    assert calls[1][-1].endswith("deployment-branch-policies?per_page=100")


def test_adapter_censors_failed_api_output() -> None:
    secret_detail = "token=must-not-escape"

    def runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", secret_detail)

    with pytest.raises(
        EnvironmentProtectionError,
        match=r"^github-environment-lookup-failed$",
    ) as error:
        guard_cli.run_guard(
            repository="sandboxcom/gludd",
            environment="azure-containerapp-live",
            environment_json=None,
            branch_policies_json=None,
            validate_only=False,
            runner=runner,
        )

    assert secret_detail not in str(error.value)


def test_adapter_censors_api_timeout() -> None:
    secret_detail = "token=must-not-escape"

    def runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            cmd=argv,
            timeout=20,
            output=secret_detail,
        )

    with pytest.raises(
        EnvironmentProtectionError,
        match=r"^github-environment-lookup-failed$",
    ) as error:
        guard_cli.run_guard(
            repository="sandboxcom/gludd",
            environment="azure-containerapp-live",
            environment_json=None,
            branch_policies_json=None,
            validate_only=False,
            runner=runner,
        )

    assert secret_detail not in str(error.value)


def test_adapter_censors_non_utf8_compatible_api_output() -> None:
    def runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "\ud800", "")

    with pytest.raises(
        EnvironmentProtectionError,
        match=r"^github-environment-lookup-failed-invalid-json$",
    ):
        guard_cli.run_guard(
            repository="sandboxcom/gludd",
            environment="azure-containerapp-live",
            environment_json=None,
            branch_policies_json=None,
            validate_only=False,
            runner=runner,
        )


def test_live_adapter_censors_second_api_failure() -> None:
    calls = 0

    def runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(_environment_payload()),
                "",
            )
        return subprocess.CompletedProcess(argv, 1, "", "private failure detail")

    with pytest.raises(
        EnvironmentProtectionError,
        match=r"^github-branch-policy-lookup-failed$",
    ):
        guard_cli.run_guard(
            repository="sandboxcom/gludd",
            environment="azure-containerapp-live",
            environment_json=None,
            branch_policies_json=None,
            validate_only=False,
            runner=runner,
        )


def test_local_fixture_pair_is_verified(tmp_path: Path) -> None:
    environment_path = tmp_path / "environment.json"
    policies_path = tmp_path / "policies.json"
    environment_path.write_text(json.dumps(_environment_payload()), encoding="utf-8")
    policies_path.write_text(json.dumps(_branch_policy_payload()), encoding="utf-8")

    receipt = guard_cli.run_guard(
        repository="sandboxcom/gludd",
        environment="azure-containerapp-live",
        environment_json=environment_path,
        branch_policies_json=policies_path,
        validate_only=False,
    )

    assert receipt.branch_policies == ("development", "master", "v*")


@pytest.mark.parametrize(
    ("repository", "environment", "environment_json", "code"),
    [
        ("not-a-repository", "azure-containerapp-live", None, "repository-invalid"),
        ("sandboxcom/gludd", "production", None, "environment-name-mismatch"),
        (
            "sandboxcom/gludd",
            "azure-containerapp-live",
            Path("environment.json"),
            "fixture-pair-required",
        ),
    ],
)
def test_adapter_rejects_invalid_admission_inputs(
    repository: str,
    environment: str,
    environment_json: Path | None,
    code: str,
) -> None:
    with pytest.raises(EnvironmentProtectionError, match=f"^{code}$"):
        guard_cli.run_guard(
            repository=repository,
            environment=environment,
            environment_json=environment_json,
            branch_policies_json=None,
            validate_only=True,
        )


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("not-json", "environment-fixture-invalid-json"),
        ("[]", "environment-fixture-invalid-shape"),
    ],
)
def test_adapter_rejects_invalid_fixture_json(
    tmp_path: Path,
    content: str,
    code: str,
) -> None:
    environment_path = tmp_path / "environment.json"
    policies_path = tmp_path / "policies.json"
    environment_path.write_text(content, encoding="utf-8")
    policies_path.write_text(json.dumps(_branch_policy_payload()), encoding="utf-8")

    with pytest.raises(EnvironmentProtectionError, match=f"^{code}$"):
        guard_cli.run_guard(
            repository="sandboxcom/gludd",
            environment="azure-containerapp-live",
            environment_json=environment_path,
            branch_policies_json=policies_path,
            validate_only=False,
        )


def test_cli_rejection_is_bounded(capsys: pytest.CaptureFixture[str]) -> None:
    result = guard_cli.main(
        [
            "--repository",
            "invalid",
            "--environment",
            "azure-containerapp-live",
            "--validate-only",
            "1",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.err == ""
    assert "reason=repository-invalid" in captured.out
    assert "mutation=false" in captured.out


def test_validate_only_cli_is_network_free_and_emits_safe_receipt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = guard_cli.main(
        [
            "--repository",
            "sandboxcom/gludd",
            "--environment",
            "azure-containerapp-live",
            "--validate-only",
            "1",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert "AZURE_CONTAINERAPP_ENVIRONMENT_GUARD" in captured.out
    assert "reviewers=1" in captured.out
    assert "mutation=false" in captured.out
    assert "cloud-operators" not in captured.out


def test_workflow_runs_guard_before_minting_oidc_assertion() -> None:
    workflow = (
        ROOT / ".github/workflows/azure-containerapp-live.yml"
    ).read_text(encoding="utf-8")

    guard = workflow.index("make azure-containerapp-environment-guard")
    oidc = workflow.index("core.getIDToken('api://AzureADTokenExchange')")
    assert guard < oidc
    assert "actions: read" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "AZURE_CONTAINERAPP_GITHUB_REPOSITORY: ${{ github.repository }}" in workflow
    assert "AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT: azure-containerapp-live" in workflow
