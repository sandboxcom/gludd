"""Pure configuration schema for fail-closed issue-source declarations.

The schema lives in the core configuration layer so :mod:`user_config` does
not depend on the business-facing issue-source package.  Provider admission
and execution remain owned by :mod:`general_ludd.issue_sources`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AfterValidator,
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


def ensure_unique_issue_source_names(sources: Sequence[_IssueSourceConfigBase]) -> None:
    """Raise when two source declarations use the same normalized name."""
    seen: set[str] = set()
    for source in sources:
        normalized = source.name.casefold()
        if normalized in seen:
            raise ValueError(f"duplicate issue-source name: {source.name!r}")
        seen.add(normalized)


def _validated_issue_sources(
    sources: list[IssueSourceConfig],
) -> list[IssueSourceConfig]:
    """Return a source list only after enforcing normalized-name uniqueness."""
    ensure_unique_issue_source_names(sources)
    return sources


IssueSourceConfigs: TypeAlias = Annotated[
    list[IssueSourceConfig],
    Field(max_length=MAX_ISSUE_SOURCES),
    AfterValidator(_validated_issue_sources),
]
