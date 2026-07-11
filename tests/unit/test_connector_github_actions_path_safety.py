from __future__ import annotations

import pytest

from general_ludd.connectors.github_actions import GitHubActionsSource


class _MockTransport:
    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, object]:
        self.calls.append((url, headers))
        return self.status, {}


def _make_source(transport=None, *, config_extra=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("GITHUB_TOKEN", "tok-secret")
    config: dict[str, object] = {"repo": "owner/name", "token_env": "GITHUB_TOKEN"}
    if config_extra:
        config.update(config_extra)
    return GitHubActionsSource(config, http_get=transport or _MockTransport())


# ---------------------------------------------------------------------------
# repo validation — traversal and injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_repo",
    [
        "o/n/../../x",
        "owner/name/extra",
        "owner/",
        "/name",
        "owner//name",
        "owner/name/..",
    ],
)
def test_repo_with_traversal_rejected(monkeypatch, bad_repo):
    with pytest.raises(ValueError):
        _make_source(config_extra={"repo": bad_repo}, monkeypatch=monkeypatch)


@pytest.mark.parametrize(
    "good_repo",
    [
        "owner/name",
        "my-org_1/my.repo-2",
    ],
)
def test_valid_repo_accepted(monkeypatch, good_repo):
    src = _make_source(config_extra={"repo": good_repo}, monkeypatch=monkeypatch)
    assert src.repo == good_repo


def test_repo_is_url_encoded(monkeypatch):
    transport = _MockTransport()
    src = _make_source(transport=transport, config_extra={"repo": "owner/name"}, monkeypatch=monkeypatch)
    src.query({})
    url = transport.calls[0][0]
    assert "/repos/owner/name/actions/runs" in url



def test_run_id_encoded(monkeypatch):
    transport = _MockTransport()
    src = _make_source(transport=transport, monkeypatch=monkeypatch)
    src.fetch_failed_logs("1/../2")
    url = transport.calls[0][0]
    assert "/../" not in url
    assert "%2F" in url


def test_query_param_injection_blocked(monkeypatch):
    with pytest.raises(ValueError):
        _make_source(config_extra={"repo": "owner/name?x=y"}, monkeypatch=monkeypatch)
