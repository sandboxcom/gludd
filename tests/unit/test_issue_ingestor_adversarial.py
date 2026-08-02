"""Adversarial unit tests for GitHubIssueIngestor.

Target: src/general_ludd/git_automation/issue_ingestor.py

Strategy: subclass the ingestor to override the network call
``_fetch_labeled_issues`` with canned fixtures, so we exercise the pure
poll/dedup/label-mapping logic without touching GitHub.

Where the task framing implies a feature that is NOT implemented
(skipping pull requests that the GitHub /issues endpoint returns), the
gap is documented and marked ``xfail``.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor


class _StubIngestor(GitHubIssueIngestor):
    """Ingestor whose fetch returns a programmable list of issues."""

    def __init__(self, issues: list[dict[str, Any]], **kw: Any) -> None:
        kw.setdefault("owner", "octo")
        kw.setdefault("repo", "demo")
        super().__init__(**kw)
        self._canned = issues
        self.fetch_calls = 0

    async def _fetch_labeled_issues(self) -> list[dict[str, Any]]:
        self.fetch_calls += 1
        return list(self._canned)


# --------------------------------------------------------------------------
# is_configured
# --------------------------------------------------------------------------

def test_not_configured_without_owner_and_repo() -> None:
    assert GitHubIssueIngestor().is_configured() is False


def test_not_configured_with_only_owner() -> None:
    assert GitHubIssueIngestor(owner="octo").is_configured() is False


def test_not_configured_with_only_repo() -> None:
    assert GitHubIssueIngestor(repo="demo").is_configured() is False


def test_configured_with_owner_and_repo() -> None:
    assert GitHubIssueIngestor(owner="octo", repo="demo").is_configured() is True


async def test_poll_returns_empty_when_unconfigured() -> None:
    # Even with overridden fetch, unconfigured short-circuits BEFORE fetching.
    ing = _StubIngestor([{"id": 1, "title": "x"}], owner="", repo="")
    todos = await ing.poll_issues()
    assert todos == []
    assert ing.fetch_calls == 0


# --------------------------------------------------------------------------
# Basic mapping
# --------------------------------------------------------------------------

async def test_basic_issue_maps_to_todo() -> None:
    ing = _StubIngestor([
        {"id": 10, "number": 7, "title": "Fix login", "body": "details", "labels": []},
    ])
    todos = await ing.poll_issues()
    assert len(todos) == 1
    todo = todos[0]
    assert todo["title"] == "Fix login"
    assert todo["description"] == "details"
    assert todo["queue"] == "core"
    assert todo["priority"] == "medium"
    assert todo["work_type"] == "code"
    assert todo["source"] == "github:octo/demo#7"


async def test_missing_fields_use_defaults() -> None:
    # Issue with only an id: title/body/number all default to empty.
    ing = _StubIngestor([{"id": 99}])
    todos = await ing.poll_issues()
    assert todos[0]["title"] == ""
    assert todos[0]["description"] == ""
    assert todos[0]["work_type"] == "code"
    assert todos[0]["source"] == "github:octo/demo#"


async def test_none_body_becomes_empty_string() -> None:
    # GitHub returns ``"body": null`` for bodyless issues -> Python None.
    # The code does ``body or ""`` so None must coerce to "".
    ing = _StubIngestor([{"id": 1, "number": 2, "title": "t", "body": None, "labels": []}])
    todos = await ing.poll_issues()
    assert todos[0]["description"] == ""


# --------------------------------------------------------------------------
# Label mapping
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label_name,expected",
    [
        ("bug", "bug_fix"),
        ("fix", "bug_fix"),
        ("bug_fix", "bug_fix"),
        ("docs", "docs"),
        ("documentation", "docs"),
        ("test", "test"),
        ("testing", "test"),
        ("enhancement", "code"),  # unknown -> default
        ("BUG", "code"),  # case-sensitive: uppercase does NOT match
    ],
)
async def test_label_maps_to_work_type(label_name: str, expected: str) -> None:
    ing = _StubIngestor([
        {"id": 1, "number": 1, "title": "t", "labels": [{"name": label_name}]},
    ])
    todos = await ing.poll_issues()
    assert todos[0]["work_type"] == expected


async def test_string_labels_are_accepted_not_just_dicts() -> None:
    # labels may be bare strings; the code does str(lbl) for non-dicts.
    ing = _StubIngestor([{"id": 1, "number": 1, "title": "t", "labels": ["bug"]}])
    todos = await ing.poll_issues()
    assert todos[0]["work_type"] == "bug_fix"


async def test_last_matching_label_wins() -> None:
    # The loop assigns on each match without break, so the LAST matching
    # label in the list determines work_type.
    ing = _StubIngestor([
        {"id": 1, "number": 1, "title": "t",
         "labels": [{"name": "bug"}, {"name": "docs"}]},
    ])
    todos = await ing.poll_issues()
    assert todos[0]["work_type"] == "docs"


async def test_unrelated_label_keeps_default_code() -> None:
    ing = _StubIngestor([
        {"id": 1, "number": 1, "title": "t",
         "labels": [{"name": "wontfix"}, {"name": "priority-high"}]},
    ])
    todos = await ing.poll_issues()
    assert todos[0]["work_type"] == "code"


async def test_label_dict_without_name_key_defaults_empty() -> None:
    ing = _StubIngestor([{"id": 1, "number": 1, "title": "t", "labels": [{}]}])
    todos = await ing.poll_issues()
    assert todos[0]["work_type"] == "code"


# --------------------------------------------------------------------------
# Dedup
# --------------------------------------------------------------------------

async def test_same_issue_not_emitted_twice_across_polls() -> None:
    ing = _StubIngestor([{"id": 5, "number": 5, "title": "t", "labels": []}])
    first = await ing.poll_issues()
    second = await ing.poll_issues()
    assert len(first) == 1
    assert second == []


async def test_duplicate_ids_within_one_poll_are_deduped() -> None:
    ing = _StubIngestor([
        {"id": 5, "number": 5, "title": "a", "labels": []},
        {"id": 5, "number": 6, "title": "b", "labels": []},
    ])
    todos = await ing.poll_issues()
    assert len(todos) == 1
    assert todos[0]["title"] == "a"  # first one seen wins


async def test_issues_without_id_are_distinguished_by_number() -> None:
    # FIXED (#80): id-less issues now fall back to a number-based dedup key,
    # so two distinct id-less issues are no longer collapsed onto sentinel 0.
    ing = _StubIngestor([
        {"number": 1, "title": "first", "labels": []},
        {"number": 2, "title": "second", "labels": []},
    ])
    todos = await ing.poll_issues()
    assert len(todos) == 2
    assert {t["title"] for t in todos} == {"first", "second"}


async def test_explicit_zero_id_distinct_from_missing_id() -> None:
    # FIXED (#80): an explicit id=0 and a missing id no longer share a dedup
    # key, so both issues survive.
    ing = _StubIngestor([
        {"id": 0, "number": 1, "title": "real-zero", "labels": []},
        {"number": 2, "title": "missing-id", "labels": []},
    ])
    todos = await ing.poll_issues()
    assert len(todos) == 2


# --------------------------------------------------------------------------
# Pull-request skipping — NOT IMPLEMENTED (gap report)
# --------------------------------------------------------------------------

async def test_pull_requests_are_skipped() -> None:
    # FIXED (#80): the GitHub /issues endpoint returns PRs too (objects with a
    # ``pull_request`` key); the ingestor now filters them out instead of
    # emitting a work todo for each.
    ing = _StubIngestor([
        {"id": 1, "number": 1, "title": "a real PR",
         "labels": [], "pull_request": {"url": "https://..."}},
    ])
    todos = await ing.poll_issues()
    assert todos == []  # PR filtered out


async def test_pull_requests_should_be_skipped() -> None:
    ing = _StubIngestor([
        {"id": 1, "number": 1, "title": "a real PR",
         "labels": [], "pull_request": {"url": "https://..."}},
    ])
    todos = await ing.poll_issues()
    assert todos == []


# --------------------------------------------------------------------------
# Malformed / robustness
# --------------------------------------------------------------------------

async def test_empty_fetch_yields_no_todos() -> None:
    ing = _StubIngestor([])
    assert await ing.poll_issues() == []


@pytest.mark.parametrize("payload", [None, {"error": "oops"}, "not-a-list"])
async def test_poll_rejects_non_list_fetch_payload(payload: Any) -> None:
    ing = GitHubIssueIngestor(owner="octo", repo="demo")

    with mock.patch.object(ing, "_fetch_labeled_issues", return_value=payload):
        assert await ing.poll_issues() == []

    assert ing._seen_ids == set()


async def test_poll_rejects_payload_with_non_mapping_item_atomically() -> None:
    ing = GitHubIssueIngestor(owner="octo", repo="demo")
    payload: list[Any] = [
        {"id": 1, "number": 1, "title": "valid", "labels": []},
        "not-an-issue",
    ]

    with mock.patch.object(ing, "_fetch_labeled_issues", return_value=payload):
        assert await ing.poll_issues() == []

    assert ing._seen_ids == set()


@pytest.mark.parametrize("labels", [None, {}, [42], [{"name": 42}]])
async def test_poll_rejects_malformed_labels_without_poisoning_dedup(
    labels: Any,
) -> None:
    ing = GitHubIssueIngestor(owner="octo", repo="demo")
    payload = [{"id": 1, "number": 1, "title": "unsafe", "labels": labels}]

    with mock.patch.object(ing, "_fetch_labeled_issues", return_value=payload):
        assert await ing.poll_issues() == []

    assert ing._seen_ids == set()


async def test_label_string_with_spaces_does_not_match() -> None:
    ing = _StubIngestor([
        {"id": 1, "number": 1, "title": "t", "labels": [{"name": " bug "}]},
    ])
    todos = await ing.poll_issues()
    # exact equality required -> whitespace-padded label is NOT mapped.
    assert todos[0]["work_type"] == "code"
