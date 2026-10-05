"""Unit tests for the GitHub Issues source — transport fully MOCKED."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.issue_sources.base import Transition
from general_ludd.issue_sources.github_issues import GitHubIssuesSource


class _Transport:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str, int, Any]] = []
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []

    def add(self, method: str, frag: str, status: int, body: Any) -> None:
        self.routes.append((method.upper(), frag, status, body))

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: Any | None = None
    ) -> tuple[int, Any]:
        self.calls.append((method.upper(), url, headers, body))
        for m, frag, status, resp in self.routes:
            if m == method.upper() and frag in url:
                return status, resp
        return 404, None


def _issues_payload() -> list[dict[str, object]]:
    return [
        {
            "number": 12,
            "title": "Add feature",
            "body": "please",
            "state": "open",
            "assignee": {"login": "octocat"},
            "labels": [{"name": "enhancement"}, {"name": "p1"}],
            "html_url": "https://github.com/o/r/issues/12",
            "updated_at": "2024-06-07T08:09:10Z",
        },
        {
            "number": 13,
            "title": "A PR not an issue",
            "state": "open",
            "pull_request": {"url": "..."},
        },
        {
            "number": 14,
            "title": "Closed issue",
            "state": "closed",
            "labels": ["plain-string-label"],
        },
    ]


def _make(transport: _Transport, **cfg: Any) -> GitHubIssuesSource:
    config: dict[str, Any] = {"repo": "o/r", "token_env": "GITHUB_TOKEN"}
    config.update(cfg)
    return GitHubIssuesSource(config, transport=transport)


@pytest.fixture(autouse=True)
def _creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xyz")


def test_source_name_and_default_base_url() -> None:
    assert GitHubIssuesSource.SOURCE == "github_issues"
    assert _make(_Transport()).base_url == "https://api.github.com"


def test_fetch_normalizes_and_skips_pull_requests() -> None:
    t = _Transport()
    t.add("GET", "/repos/o/r/issues", 200, _issues_payload())
    records = _make(t).fetch({})
    # PR (number 13) is filtered out -> 2 records.
    ids = [r["external_id"] for r in records]
    assert ids == ["12", "14"]
    first = records[0]
    assert first["title"] == "Add feature"
    assert first["assignee"] == "octocat"
    assert first["labels"] == ["enhancement", "p1"]
    assert first["url"] == "https://github.com/o/r/issues/12"
    # plain-string label form is also supported
    assert records[1]["labels"] == ["plain-string-label"]


def test_fetch_bearer_token_from_env() -> None:
    t = _Transport()
    t.add("GET", "/issues", 200, [])
    _make(t).fetch({})
    assert t.calls[0][2]["Authorization"] == "Bearer ghp_xyz"


def test_fetch_non_2xx_returns_empty() -> None:
    t = _Transport()
    t.add("GET", "/issues", 404, {"message": "Not Found"})
    assert _make(t).fetch({}) == []


def test_write_back_done_patches_state_closed() -> None:
    t = _Transport()
    t.add("PATCH", "/issues/12", 200, {})
    src = _make(t)
    assert src.write_back("12", Transition.DONE) is True
    method, url, _h, body = t.calls[0]
    assert method == "PATCH"
    assert url.endswith("/repos/o/r/issues/12")
    assert body == {"state": "closed"}


def test_write_back_claim_without_label_is_noop_success() -> None:
    t = _Transport()
    src = _make(t)
    assert src.write_back("12", Transition.CLAIM) is True
    assert t.calls == []  # nothing pushed


def test_write_back_claim_with_label_posts_label() -> None:
    t = _Transport()
    t.add("POST", "/issues/12/labels", 200, [])
    src = _make(t, claim_label="in-progress")
    assert src.write_back("12", Transition.CLAIM) is True
    method, url, _h, body = t.calls[0]
    assert method == "POST"
    assert url.endswith("/issues/12/labels")
    assert body == {"labels": ["in-progress"]}


@pytest.mark.parametrize(
    "external_id",
    [
        "",
        "0",
        "01",
        "-1",
        "+12",
        " 12",
        "12/labels",
        "../../admin",
        "\uff11\uff12",
        "1" * 21,
    ],
)
@pytest.mark.parametrize("transition", [Transition.DONE, Transition.CLAIM])
def test_write_back_rejects_noncanonical_issue_numbers_before_transport(
    external_id: str,
    transition: Transition,
) -> None:
    t = _Transport()
    src = _make(t, claim_label="in-progress")

    with pytest.raises(ValueError, match="positive ASCII decimal"):
        src.write_back(external_id, transition)

    assert t.calls == []


def test_bad_repo_rejected() -> None:
    with pytest.raises(ValueError):
        GitHubIssuesSource({"repo": "no-slash"}, transport=_Transport())
