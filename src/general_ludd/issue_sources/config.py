"""Fail-closed, shadow-only admission for configured issue sources.

This module deliberately validates declarations without importing or creating
provider adapters.  It is the inert configuration boundary for the first
issue-source rollout; execution, polling, and write-back belong to later
explicitly gated slices.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

MAX_ISSUE_SOURCES = 16
MIN_POLL_INTERVAL_SECONDS = 60
MAX_POLL_INTERVAL_SECONDS = 86_400
MAX_PAGES_PER_POLL = 10
MAX_RECORDS_PER_POLL = 500

IssueSourceKind: TypeAlias = Literal["github", "jira"]
ShadowMode: TypeAlias = Literal["shadow"]
SourceName: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    ),
]
EnvironmentVariableName: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z_][A-Z0-9_]*$",
    ),
]


class _IssueSourceConfigBase(BaseModel):
    """Shared resource bounds for an admitted built-in source declaration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: SourceName
    mode: ShadowMode = "shadow"
    poll_interval_seconds: int = Field(
        default=300,
        ge=MIN_POLL_INTERVAL_SECONDS,
        le=MAX_POLL_INTERVAL_SECONDS,
        strict=True,
    )
    page_limit: int = Field(default=5, ge=1, le=MAX_PAGES_PER_POLL, strict=True)
    record_limit: int = Field(
        default=100,
        ge=1,
        le=MAX_RECORDS_PER_POLL,
        strict=True,
    )


class GithubIssueSourceConfig(_IssueSourceConfigBase):
    """Configuration admitted for the built-in GitHub issue source."""

    source: Literal["github"] = "github"
    repository: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=201,
            pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        ),
    ]
    token_env: EnvironmentVariableName


class JiraIssueSourceConfig(_IssueSourceConfigBase):
    """Configuration admitted for the built-in Jira issue source."""

    source: Literal["jira"] = "jira"
    base_url: AnyHttpUrl
    project_key: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Z][A-Z0-9_]*$",
        ),
    ]
    email_env: EnvironmentVariableName
    token_env: EnvironmentVariableName

    @field_validator("base_url")
    @classmethod
    def _require_origin_only_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Reject credentials, request components, and clear-text Jira URLs."""
        if value.scheme != "https":
            raise ValueError("Jira base_url must use https")
        if (
            value.username
            or value.password
            or value.path not in ("", "/")
            or value.query
            or value.fragment
        ):
            raise ValueError("Jira base_url must be an origin without credentials or query data")
        return value


IssueSourceConfig: TypeAlias = Annotated[
    GithubIssueSourceConfig | JiraIssueSourceConfig,
    Field(discriminator="source"),
]


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


def ensure_unique_issue_source_names(sources: Sequence[_IssueSourceConfigBase]) -> None:
    """Raise when two source declarations use the same normalized name."""
    seen: set[str] = set()
    for source in sources:
        normalized = source.name.casefold()
        if normalized in seen:
            raise ValueError(f"duplicate issue-source name: {source.name!r}")
        seen.add(normalized)


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
