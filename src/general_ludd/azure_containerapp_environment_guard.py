"""Dependency-free guard for the paid Azure proof GitHub Environment."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

EXPECTED_ENVIRONMENT = "azure-containerapp-live"
REQUIRED_BRANCH_POLICIES = ("development", "master", "v*")


class EnvironmentProtectionError(ValueError):
    """A bounded, content-free GitHub Environment rejection."""

    def __init__(self, code: str) -> None:
        """Initialize the exception with one stable rejection code."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EnvironmentProtectionReceipt:
    """Non-sensitive evidence that the protected environment was inspected."""

    environment: str
    reviewer_count: int
    branch_policies: tuple[str, ...]
    configuration_digest: str

    def render(self) -> str:
        """Render one bounded receipt without reviewer or API response content."""
        policies = ",".join(self.branch_policies)
        return (
            "AZURE_CONTAINERAPP_ENVIRONMENT_GUARD "
            f"environment={self.environment} reviewers={self.reviewer_count} "
            f"policies={policies} configuration_digest={self.configuration_digest} "
            "mutation=false"
        )


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EnvironmentProtectionError(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EnvironmentProtectionError(code)
    return value


def _reviewer_subjects(environment: Mapping[str, object]) -> tuple[str, ...]:
    rules = _sequence(environment.get("protection_rules"), "protection-rules-invalid")
    reviewer_rules: list[Mapping[str, object]] = []
    for raw_rule in rules:
        rule = _mapping(raw_rule, "protection-rules-invalid")
        if rule.get("type") == "required_reviewers":
            reviewer_rules.append(rule)
    if not reviewer_rules:
        raise EnvironmentProtectionError("required-reviewers-rule-missing")
    if len(reviewer_rules) != 1:
        raise EnvironmentProtectionError("required-reviewers-rule-ambiguous")

    rule = reviewer_rules[0]
    if rule.get("prevent_self_review") is not True:
        raise EnvironmentProtectionError("prevent-self-review-disabled")
    reviewers = _sequence(rule.get("reviewers"), "reviewers-invalid")
    if not reviewers:
        raise EnvironmentProtectionError("reviewers-missing")

    subjects: list[str] = []
    for raw_reviewer in reviewers:
        wrapper = _mapping(raw_reviewer, "reviewer-invalid")
        reviewer_type = wrapper.get("type")
        if reviewer_type not in {"User", "Team"}:
            raise EnvironmentProtectionError("reviewer-invalid")
        reviewer = _mapping(wrapper.get("reviewer"), "reviewer-invalid")
        reviewer_id = reviewer.get("id")
        if (
            not isinstance(reviewer_id, int)
            or isinstance(reviewer_id, bool)
            or reviewer_id < 1
        ):
            raise EnvironmentProtectionError("reviewer-invalid")
        subjects.append(f"{reviewer_type}:{reviewer_id}")
    if len(set(subjects)) != len(subjects):
        raise EnvironmentProtectionError("reviewer-duplicate")
    return tuple(sorted(subjects))


def _branch_policy_names(
    environment: Mapping[str, object],
    branch_policies: Mapping[str, object],
) -> tuple[str, ...]:
    mode = _mapping(
        environment.get("deployment_branch_policy"),
        "branch-policy-mode-invalid",
    )
    if (
        mode.get("protected_branches") is not False
        or mode.get("custom_branch_policies") is not True
    ):
        raise EnvironmentProtectionError("branch-policy-mode-invalid")

    raw_policies = _sequence(
        branch_policies.get("branch_policies"),
        "branch-policy-list-invalid",
    )
    total_count = branch_policies.get("total_count")
    if (
        not isinstance(total_count, int)
        or isinstance(total_count, bool)
        or total_count != len(raw_policies)
    ):
        raise EnvironmentProtectionError("branch-policy-count-mismatch")

    names: list[str] = []
    for raw_policy in raw_policies:
        policy = _mapping(raw_policy, "branch-policy-list-invalid")
        name = policy.get("name")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise EnvironmentProtectionError("branch-policy-list-invalid")
        names.append(name)
    if len(set(names)) != len(names):
        raise EnvironmentProtectionError("branch-policy-duplicate")
    if set(names) != set(REQUIRED_BRANCH_POLICIES):
        raise EnvironmentProtectionError("branch-policy-set-mismatch")
    return tuple(sorted(names))


def verify_environment_protection(
    environment_payload: Mapping[str, object],
    branch_policies_payload: Mapping[str, object],
) -> EnvironmentProtectionReceipt:
    """Verify the exact non-mutating admission boundary for paid live proof."""
    if environment_payload.get("name") != EXPECTED_ENVIRONMENT:
        raise EnvironmentProtectionError("environment-name-mismatch")
    if environment_payload.get("can_admins_bypass") is not False:
        raise EnvironmentProtectionError("admin-bypass-enabled")
    reviewer_subjects = _reviewer_subjects(environment_payload)
    policy_names = _branch_policy_names(environment_payload, branch_policies_payload)
    canonical = json.dumps(
        {
            "environment": EXPECTED_ENVIRONMENT,
            "can_admins_bypass": False,
            "prevent_self_review": True,
            "reviewers": reviewer_subjects,
            "branch_policies": policy_names,
            "mutation": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return EnvironmentProtectionReceipt(
        environment=EXPECTED_ENVIRONMENT,
        reviewer_count=len(reviewer_subjects),
        branch_policies=policy_names,
        configuration_digest=hashlib.sha256(canonical).hexdigest(),
    )
