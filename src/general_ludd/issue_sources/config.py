"""Fail-closed, shadow-only admission for configured issue sources.

This module deliberately validates declarations without importing or creating
provider adapters.  It is the inert configuration boundary for the first
issue-source rollout; execution, polling, and write-back belong to later
explicitly gated slices.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from general_ludd.config.issue_sources import (
    MAX_ISSUE_SOURCES,
    MAX_PAGES_PER_POLL,
    MAX_POLL_INTERVAL_SECONDS,
    MAX_RECORDS_PER_POLL,
    MIN_POLL_INTERVAL_SECONDS,
    GithubIssueSourceConfig,
    IssueSourceConfig,
    IssueSourceConfigs,
    IssueSourceKind,
    JiraIssueSourceConfig,
    ShadowMode,
    ensure_unique_issue_source_names,
)

__all__ = (
    "MAX_ISSUE_SOURCES",
    "MAX_PAGES_PER_POLL",
    "MAX_POLL_INTERVAL_SECONDS",
    "MAX_RECORDS_PER_POLL",
    "MIN_POLL_INTERVAL_SECONDS",
    "GithubIssueSourceConfig",
    "IssueSourceAdmissionPlan",
    "IssueSourceConfig",
    "IssueSourceConfigs",
    "IssueSourceKind",
    "JiraIssueSourceConfig",
    "ShadowMode",
    "admit_issue_sources",
    "ensure_unique_issue_source_names",
)


@dataclass(frozen=True, slots=True)
class IssueSourceAdmissionPlan:
    """Sanitized, immutable description of admitted shadow work.

    ``credential_env`` contains environment-variable *names*, never resolved
    values. ``parameters`` is a tuple so callers cannot mutate the admitted
    provider inputs after validation.
    """

    source: IssueSourceKind
    name: str
    mode: ShadowMode
    poll_interval_seconds: int
    page_limit: int
    record_limit: int
    parameters: tuple[tuple[str, str], ...]
    credential_env: tuple[str, ...]


def admit_issue_sources(
    sources: Sequence[IssueSourceConfig],
) -> tuple[IssueSourceAdmissionPlan, ...]:
    """Validate admission invariants and return inert shadow plans only.

    No provider module is imported and no credential value is resolved.  A
    caller cannot turn these plans into active work accidentally because the
    only admitted mode is the literal ``"shadow"``.
    """
    if len(sources) > MAX_ISSUE_SOURCES:
        raise ValueError(f"issue_sources admits at most {MAX_ISSUE_SOURCES} entries")
    admitted_types = (GithubIssueSourceConfig, JiraIssueSourceConfig)
    if not all(isinstance(source, admitted_types) for source in sources):
        raise TypeError("issue_sources must be validated built-in source configurations")

    ensure_unique_issue_source_names(sources)
    plans: list[IssueSourceAdmissionPlan] = []
    for source in sources:
        parameters: tuple[tuple[str, str], ...]
        credential_env: tuple[str, ...]
        if isinstance(source, GithubIssueSourceConfig):
            parameters = (("repository", source.repository),)
            credential_env = (source.token_env,)
        else:
            parameters = (
                ("base_url", str(source.base_url)),
                ("project_key", source.project_key),
            )
            credential_env = (source.email_env, source.token_env)

        plans.append(
            IssueSourceAdmissionPlan(
                source=source.source,
                name=source.name,
                mode=source.mode,
                poll_interval_seconds=source.poll_interval_seconds,
                page_limit=source.page_limit,
                record_limit=source.record_limit,
                parameters=parameters,
                credential_env=credential_env,
            )
        )
    return tuple(plans)
