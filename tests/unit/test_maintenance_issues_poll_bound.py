"""Regression tests: /admin/issues/poll must bound the seen-ID store (memory DoS guard).

Tests call the `issues_poll` handler directly (no real HTTP server needed) by
importing `register()` on a FastAPI test-client app and using
`fastapi.testclient.TestClient`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.maintenance import _MAX_SEEN_KEYS, _SAFE_LABEL, _SAFE_SLUG, register

# The ingestor is imported locally inside `issues_poll`, so patch the source module.
_INGESTOR_TARGET = "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor"


def _build_app(daemon_state: dict) -> TestClient:
    app = FastAPI()
    register(app, daemon_state)
    return TestClient(app, raise_server_exceptions=False)


def _poll(client: TestClient, owner: str, repo: str, label: str = "gludd") -> int:
    resp = client.post(
        "/admin/issues/poll",
        json={"owner": owner, "repo": repo, "label": label},
    )
    return resp.status_code


# ---------------------------------------------------------------------------
# Validation: bad owner/repo/label is rejected with 422
# ---------------------------------------------------------------------------


class TestInputValidation:
    def _client(self) -> TestClient:
        return _build_app({})

    def test_empty_owner_rejected(self):
        client = self._client()
        assert _poll(client, "", "somerepo") == 422

    def test_oversized_owner_rejected(self):
        client = self._client()
        assert _poll(client, "a" * 101, "somerepo") == 422

    def test_shell_metachar_owner_rejected(self):
        client = self._client()
        assert _poll(client, "owner;rm -rf /", "somerepo") == 422

    def test_slash_in_owner_rejected(self):
        client = self._client()
        assert _poll(client, "org/suborg", "repo") == 422

    def test_empty_repo_rejected(self):
        client = self._client()
        assert _poll(client, "owner", "") == 422

    def test_oversized_repo_rejected(self):
        client = self._client()
        assert _poll(client, "owner", "r" * 101) == 422

    def test_oversized_label_rejected(self):
        client = self._client()
        assert _poll(client, "owner", "repo", "l" * 101) == 422

    def test_valid_inputs_pass_validation(self):
        """Valid owner/repo/label should not be rejected at the validation layer.

        We mock the ingestor so no real GitHub call is made.
        """
        with patch(_INGESTOR_TARGET, autospec=True) as MockIngestor:
            instance = MockIngestor.return_value
            instance.poll_issues = AsyncMock(return_value=[])
            client = _build_app({})
            status = _poll(client, "my-org", "my-repo.git", "bug/needs-fix")
        assert status == 200


# ---------------------------------------------------------------------------
# Key-count cap: store never grows beyond _MAX_SEEN_KEYS
# ---------------------------------------------------------------------------


class TestKeyCountCap:
    def _client_with_full_store(self) -> tuple[TestClient, dict]:
        """Pre-fill the store to exactly _MAX_SEEN_KEYS entries."""
        daemon_state: dict = {}
        store: dict = daemon_state.setdefault("issue_ingestor_seen_ids", {})
        for i in range(_MAX_SEEN_KEYS):
            store[f"owner{i}/repo{i}#label{i}"] = set()
        client = _build_app(daemon_state)
        return client, daemon_state

    def test_new_key_when_store_full_returns_429(self):
        client, _ = self._client_with_full_store()
        status = _poll(client, "newowner", "newrepo", "newlabel")
        assert status == 429

    def test_existing_key_when_store_full_succeeds(self):
        """Polling an already-tracked key must not be blocked."""
        client, _daemon_state = self._client_with_full_store()
        # Pick the very first pre-filled key.
        with patch(_INGESTOR_TARGET, autospec=True) as MockIngestor:
            instance = MockIngestor.return_value
            instance.poll_issues = AsyncMock(return_value=[])
            status = _poll(client, "owner0", "repo0", "label0")
        assert status == 200

    def test_store_size_never_exceeds_cap_under_many_distinct_keys(self):
        """Sending _MAX_SEEN_KEYS + 50 distinct keys must not grow the store past cap."""
        daemon_state: dict = {}
        client = _build_app(daemon_state)

        with patch(_INGESTOR_TARGET, autospec=True) as MockIngestor:
            instance = MockIngestor.return_value
            instance.poll_issues = AsyncMock(return_value=[])

            for i in range(_MAX_SEEN_KEYS + 50):
                _poll(client, f"owner{i}", f"repo{i}", "bug")

        store = daemon_state.get("issue_ingestor_seen_ids", {})
        assert len(store) <= _MAX_SEEN_KEYS


# ---------------------------------------------------------------------------
# Regex sanity checks (fast, no HTTP)
# ---------------------------------------------------------------------------


class TestRegexes:
    def test_safe_slug_allows_hyphen_dot_underscore(self):
        assert _SAFE_SLUG.match("my-org.corp_2")

    def test_safe_slug_rejects_at_sign(self):
        assert not _SAFE_SLUG.match("user@host")

    def test_safe_label_allows_colon_slash(self):
        assert _SAFE_LABEL.match("area/bug:needs-triage")

    def test_safe_label_rejects_space(self):
        assert not _SAFE_LABEL.match("my label")
