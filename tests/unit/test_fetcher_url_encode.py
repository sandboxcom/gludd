"""Regression: GitHubSkillSource must percent-encode URL components.

A branch/owner/repo extractable from a crafted GitHub URL could carry '?' or '#'
and inject a query/fragment, redirecting the credentialed request. Components are
now wrapped in urllib.parse.quote(); alphanumeric values are unchanged.
"""
from __future__ import annotations

from general_ludd.skills.fetcher import GitHubSkillSource


def test_normal_alphanumeric_components_unchanged() -> None:
    src = GitHubSkillSource(owner="torvalds", repo="linux", branch="master", subdir="kernel")
    assert src._api_url("kernel/SKILL.md") == (
        "https://api.github.com/repos/torvalds/linux/contents/kernel/SKILL.md?ref=master"
    )
    assert src._raw_url("kernel/SKILL.md") == (
        "https://raw.githubusercontent.com/torvalds/linux/master/kernel/SKILL.md"
    )


def test_question_mark_in_branch_is_encoded() -> None:
    src = GitHubSkillSource(owner="user", repo="repo", branch="feature?inject=1")
    api = src._api_url("SKILL.md")
    assert "%3F" in api
    assert "?inject" not in api
    assert api.count("?") == 1  # only the legitimate ?ref=
    raw = src._raw_url("SKILL.md")
    assert "%3F" in raw
    assert "?" not in raw


def test_hash_in_branch_is_encoded() -> None:
    src = GitHubSkillSource(owner="user", repo="repo", branch="v1#frag")
    api = src._api_url("SKILL.md")
    assert "%23" in api
    assert "#frag" not in api
    assert "#" not in api


def test_special_chars_in_owner_and_repo_encoded() -> None:
    src = GitHubSkillSource(owner="user?q", repo="my#repo", branch="main")
    api = src._api_url("SKILL.md")
    assert "user%3Fq" in api
    assert "my%23repo" in api


def test_path_slashes_preserved() -> None:
    src = GitHubSkillSource(owner="user", repo="repo", branch="main")
    raw = src._raw_url("dir/subdir/SKILL.md")
    assert "dir/subdir/SKILL.md" in raw
    assert "%2F" not in raw
