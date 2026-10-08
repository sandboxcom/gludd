"""Admission tests for shadow-only issue-source configuration."""

from __future__ import annotations

import builtins
import socket
import subprocess
import sys
import threading
from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from pydantic import ValidationError

from general_ludd.config.issue_sources import (
    GithubIssueSourceConfig as ConfigGithubIssueSourceConfig,
)
from general_ludd.config.user_config import IssuesConfig, UserConfig
from general_ludd.issue_sources.config import (
    MAX_ISSUE_SOURCES,
    MAX_PAGES_PER_POLL,
    MAX_POLL_INTERVAL_SECONDS,
    MAX_RECORDS_PER_POLL,
    MIN_POLL_INTERVAL_SECONDS,
    GithubIssueSourceConfig,
    IssueSourceConfig,
    JiraIssueSourceConfig,
    admit_issue_sources,
)


def _github(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "source": "github",
        "name": "github-main",
        "repository": "sandboxcom/gludd",
        "token_env": "GITHUB_TOKEN",
    }
    data.update(overrides)
    return data


def _jira(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "source": "jira",
        "name": "jira-acme",
        "base_url": "https://acme.atlassian.net",
        "project_key": "GLUDD",
        "email_env": "JIRA_EMAIL",
        "token_env": "JIRA_API_TOKEN",
    }
    data.update(overrides)
    return data


def _user_config(sources: list[dict[str, object]]) -> UserConfig:
    return UserConfig.model_validate({"issue_sources": sources})


def test_public_issue_source_models_reexport_config_owned_types() -> None:
    """Keep the public business import compatible with the core-owned schema."""
    config = _user_config([_github()])

    assert GithubIssueSourceConfig is ConfigGithubIssueSourceConfig
    assert isinstance(config.issue_sources[0], ConfigGithubIssueSourceConfig)


def test_issue_sources_are_optional_and_legacy_issues_defaults_are_unchanged() -> None:
    config = UserConfig()

    assert config.issue_sources == []
    assert config.issues == IssuesConfig()
    assert config.issues.polling_enabled is False
    assert config.issues.poll_interval_ticks == 300


def test_legacy_issues_block_is_preserved_with_new_sources() -> None:
    config = UserConfig.model_validate(
        {
            "issues": {
            "polling_enabled": True,
            "poll_interval_ticks": 42,
            "github_owner": "legacy-owner",
            "github_repo": "legacy-repo",
            "github_label": "legacy-label",
            },
            "issue_sources": [_github()],
        }
    )

    assert config.issues.polling_enabled is True
    assert config.issues.poll_interval_ticks == 42
    assert config.issues.github_owner == "legacy-owner"
    assert config.issues.github_repo == "legacy-repo"
    assert config.issues.github_label == "legacy-label"


def test_discriminator_builds_only_the_declared_builtin_models() -> None:
    config = _user_config([_github(), _jira()])

    assert isinstance(config.issue_sources[0], GithubIssueSourceConfig)
    assert isinstance(config.issue_sources[1], JiraIssueSourceConfig)


@pytest.mark.parametrize("source", ["gitlab", "redmine", "plugin", "markdown", "csv"])
def test_unknown_or_deferred_provider_fails_closed(source: str) -> None:
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        _user_config([_github(source=source)])


@pytest.mark.parametrize("extra", [{"token": "literal-secret"}, {"password": "literal-secret"}])
def test_literal_secret_fields_are_forbidden(extra: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _user_config([_github(**extra)])


@pytest.mark.parametrize(
    "env_name",
    ["ghp_literal_secret", "contains-dash", "9STARTS_WITH_DIGIT", "", "HAS SPACE"],
)
def test_credentials_must_be_environment_variable_references(env_name: str) -> None:
    with pytest.raises(ValidationError):
        _user_config([_github(token_env=env_name)])


def test_source_names_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="duplicate issue-source name"):
        _user_config([_github(), _jira(name="github-main")])


def test_source_count_is_bounded() -> None:
    allowed = [
        _github(name=f"github-{index}") for index in range(MAX_ISSUE_SOURCES)
    ]
    assert len(_user_config(allowed).issue_sources) == MAX_ISSUE_SOURCES

    with pytest.raises(ValidationError, match="too_long"):
        _user_config([*allowed, _jira(name="overflow")])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page_limit", 0),
        ("page_limit", MAX_PAGES_PER_POLL + 1),
        ("record_limit", 0),
        ("record_limit", MAX_RECORDS_PER_POLL + 1),
        ("poll_interval_seconds", MIN_POLL_INTERVAL_SECONDS - 1),
        ("poll_interval_seconds", MAX_POLL_INTERVAL_SECONDS + 1),
    ],
)
def test_poll_work_is_strictly_bounded(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _user_config([_github(**{field: value})])


def test_bounds_accept_their_exact_edges() -> None:
    config = _user_config(
        [
            _github(
                page_limit=MAX_PAGES_PER_POLL,
                record_limit=MAX_RECORDS_PER_POLL,
                poll_interval_seconds=MIN_POLL_INTERVAL_SECONDS,
            ),
            _jira(
                page_limit=1,
                record_limit=1,
                poll_interval_seconds=MAX_POLL_INTERVAL_SECONDS,
            ),
        ]
    )

    assert config.issue_sources[0].page_limit == MAX_PAGES_PER_POLL
    assert config.issue_sources[1].poll_interval_seconds == MAX_POLL_INTERVAL_SECONDS


def test_active_mode_is_rejected_because_admission_is_shadow_only() -> None:
    with pytest.raises(ValidationError):
        _user_config([_github(mode="active")])


@pytest.mark.parametrize(
    "jira_origin",
    [
        "http://acme.atlassian.net",
        "https://user:password@acme.atlassian.net",
        "https://acme.atlassian.net/jira",
        "https://acme.atlassian.net?token=value",
        "https://acme.atlassian.net#fragment",
    ],
)
def test_jira_origin_rejects_unsafe_request_components(jira_origin: str) -> None:
    with pytest.raises(ValidationError):
        _user_config([_jira(base_url=jira_origin)])


def test_json_environment_value_uses_the_same_discriminated_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GLUDD_ISSUE_SOURCES",
        '[{"source":"github","name":"github-env","repository":'
        '"sandboxcom/gludd","token_env":"GITHUB_TOKEN"}]',
    )

    config = UserConfig()

    assert len(config.issue_sources) == 1
    assert isinstance(config.issue_sources[0], GithubIssueSourceConfig)
    assert config.issue_sources[0].name == "github-env"


def test_admission_returns_a_sanitized_immutable_shadow_plan() -> None:
    config = _user_config([_github(), _jira()])

    plan = admit_issue_sources(config.issue_sources)

    assert isinstance(plan, tuple)
    assert [item.source for item in plan] == ["github", "jira"]
    assert all(item.mode == "shadow" for item in plan)
    assert plan[0].parameters == (("repository", "sandboxcom/gludd"),)
    assert plan[0].credential_env == ("GITHUB_TOKEN",)
    assert plan[1].parameters == (
        ("base_url", "https://acme.atlassian.net/"),
        ("project_key", "GLUDD"),
    )
    assert plan[1].credential_env == ("JIRA_EMAIL", "JIRA_API_TOKEN")
    assert "literal-secret" not in repr(plan)

    with pytest.raises(FrozenInstanceError):
        plan[0].__setattr__("name", "mutated")


def test_admission_revalidates_cardinality_and_unique_names() -> None:
    source = GithubIssueSourceConfig.model_validate(_github())

    with pytest.raises(ValueError, match="duplicate issue-source name"):
        admit_issue_sources([source, source])
    with pytest.raises(ValueError, match="at most"):
        admit_issue_sources([source] * (MAX_ISSUE_SOURCES + 1))

    unvalidated = cast(list[IssueSourceConfig], [object()])
    with pytest.raises(TypeError, match="validated built-in"):
        admit_issue_sources(unvalidated)


def test_admission_has_no_io_provider_process_or_thread_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_modules = {
        "general_ludd.issue_sources.github_issues",
        "general_ludd.issue_sources.jira",
    }
    loaded_before = provider_modules.intersection(sys.modules)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("configuration admission attempted a side effect")

    monkeypatch.setattr(builtins, "open", unexpected)
    monkeypatch.setattr(socket, "socket", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr(threading.Thread, "start", unexpected)

    config = UserConfig.model_validate({"issue_sources": [_github(), _jira()]})
    plan = admit_issue_sources(config.issue_sources)

    assert len(plan) == 2
    assert provider_modules.intersection(sys.modules) == loaded_before
