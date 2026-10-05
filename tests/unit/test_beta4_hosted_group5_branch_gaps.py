"""Close hosted-only branch gaps in the fifth beta4 coverage tranche."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from general_ludd.distributed.gossip import (
    DigestEntry,
    GossipMessage,
    GossipProtocol,
    Member,
    MemberStatus,
    Rumor,
    ensure_convergence,
    run_gossip_round,
    spread_rumor,
)
from general_ludd.infra.model_search import ModelIndex, ModelSearchResult, SearXModelSearch
from general_ludd.issue_sources.redmine import RedmineIssueSource, _host_is_internal, _parse_ts
from general_ludd.pipeline.daemon_adapters import make_disk_ok, make_dispatch_fn, make_merge_fn
from general_ludd.pipeline.state import CompletedUnit

REDMINE_ENV_NAME = "REDMINE_KEY"


class _SearchResponse:
    def __init__(self, payload: object, *, fail: bool = False) -> None:
        self._payload = payload
        self._fail = fail

    def raise_for_status(self) -> None:
        if self._fail:
            raise RuntimeError("search failed")

    def json(self) -> object:
        return self._payload


class _RedmineResponse:
    status_code = 200

    def json(self) -> object:
        return {"issues": []}


def _redmine_transport(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, object] | None = None,
    json: Mapping[str, object] | None = None,
    timeout: float,
) -> _RedmineResponse:
    del method, url, headers, params, json, timeout
    return _RedmineResponse()


class TestModelSearchHostedBranches:
    def test_search_rejects_non_list_payload_and_transport_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        search = SearXModelSearch("https://search.example", timeout=1)
        monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _SearchResponse({"results": {}}))
        assert search._do_search("model") == []
        monkeypatch.setattr(
            httpx,
            "get",
            lambda *args, **kwargs: _SearchResponse({}, fail=True),
        )
        assert search._do_search("model") == []

    def test_search_models_filters_empty_foreign_and_duplicate_urls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        search = SearXModelSearch("https://search.example")
        monkeypatch.setattr(
            search,
            "_do_search",
            lambda query, engines=None: [
                {"url": "", "title": "empty"},
                {"url": "https://example.com/model", "title": "foreign"},
                {"url": "https://huggingface.co/acme/model", "title": "first", "content": "one"},
                {"url": "https://huggingface.co/acme/model", "title": "duplicate", "content": "two"},
            ],
        )
        results = search.search_models("acme", source="unknown")
        assert [(result.name, result.description) for result in results] == [("acme__model", "one")]

    def test_find_model_falls_back_to_github_and_extracts_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        search = SearXModelSearch("https://search.example")
        content = "https://github.com/acme/model/blob/main/model-q4_k_m.gguf 7B model Apache-2.0"
        monkeypatch.setattr(
            search,
            "_do_search",
            lambda query, engines=None: [
                {"url": "https://example.com/nope", "title": "nope"},
                {"url": "https://github.com/acme/model", "title": "acme/model", "content": content},
            ],
        )
        result = search.find_model("acme")
        assert result is not None
        assert result.name == "acme__model"
        assert result.params_count == 7.0
        assert result.quantizations_available == ["q4_k_m", "gguf"]
        assert result.license == "apache-2.0"
        assert result.download_urls == ["https://github.com/acme/model/blob/main/model-q4_k_m.gguf"]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("12 billion parameters", 12.0), ("3.5 Billion model", 3.5), ("unknown", 0.0)],
    )
    def test_parameter_formats(self, text: str, expected: float) -> None:
        assert SearXModelSearch._extract_param_count(text) == expected

    def test_model_name_title_and_license_fallbacks(self) -> None:
        assert SearXModelSearch._extract_model_name("https://example.com", "org/model.") == "org__model"
        assert SearXModelSearch._extract_model_name("https://example.com", "Plain title") == "Plain title"
        assert SearXModelSearch._extract_license("licensed under MPL-2.0") == "mpl-2.0"
        assert SearXModelSearch._extract_license("proprietary") == ""

    def test_index_ignores_invalid_shape_and_searches_description(self, tmp_path: Path) -> None:
        (tmp_path / "index.json").write_text("[]")
        assert ModelIndex(str(tmp_path)).size() == 0
        (tmp_path / "index.json").write_text("{}")
        index = ModelIndex(str(tmp_path))
        index.put(ModelSearchResult(name="opaque", description="Contains Needle"))
        assert index.search("needle")[0].name == "opaque"


class TestRedmineHostedBranches:
    @pytest.mark.parametrize("host", ["", "metadata", "service.internal", "224.0.0.1", "example.com"])
    def test_literal_host_classification(self, host: str) -> None:
        expected = host != "example.com"
        assert _host_is_internal(host) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (7, None),
            ("not-a-time", None),
            ("2024-05-01T12:30:00+02:00", "2024-05-01T10:30:00+00:00"),
        ],
    )
    def test_timestamp_totality(self, value: object, expected: str | None) -> None:
        assert _parse_ts(value) == expected

    def test_constructor_rejects_empty_and_non_http_urls(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            RedmineIssueSource({}, transport=_redmine_transport)
        with pytest.raises(ValueError, match="http"):
            RedmineIssueSource(
                {"base_url": "ftp://redmine.example.com", "api_key_env": REDMINE_ENV_NAME},
                transport=_redmine_transport,
            )

    def test_constructor_ignores_boolean_status_and_non_scalar_timeout(self) -> None:
        source = RedmineIssueSource(
            {
                "base_url": "https://redmine.example.com",
                "api_key_env": REDMINE_ENV_NAME,
                "timeout": object(),
                "status_map": {"Done": True, "Closed": "9"},
            },
            transport=_redmine_transport,
        )
        assert source._timeout == 15.0
        assert source._resolve_status_id("Closed") == 9
        with pytest.raises(ValueError, match="Done"):
            source._resolve_status_id("Done")

    def test_normalization_and_payload_narrowing_are_total(self) -> None:
        source = RedmineIssueSource(
            {"base_url": "https://redmine.example.com", "api_key_env": REDMINE_ENV_NAME},
            transport=_redmine_transport,
        )
        assert source._name_of("status") is None
        assert source._name_of({"id": 1}) is None
        assert source._extract_issues([]) == []
        assert source._extract_issues({"issues": "not-a-list"}) == []
        assert source._extract_issues({"issues": [{"id": 1}, "bad", 2]}) == [{"id": 1}]


class TestGossipHostedBranches:
    def test_digest_diff_covers_missing_ahead_behind_and_local_only(self) -> None:
        gossip = GossipProtocol("n1", "a")
        gossip.put("ahead", "old")
        gossip.put("ahead", "new")
        gossip.put("behind", "old")
        gossip.put("local", "only")
        message = GossipMessage(
            msg_type="digest",
            sender_id="n2",
            round=1,
            digest=[
                DigestEntry("missing", 1, "n2"),
                DigestEntry("ahead", 1, "n2"),
                DigestEntry("behind", 2, "n2"),
            ],
        )
        pushes, pulls = gossip._diff_digest(message)
        assert {rumor.key for rumor in pushes} == {"ahead", "local"}
        assert pulls == ["missing", "behind"]

    def test_fresh_remote_rumor_can_be_newer_than_origin_but_older_locally(self) -> None:
        gossip = GossipProtocol("n1", "a")
        gossip.put("key", "v1")
        gossip.put("key", "v2")
        assert gossip._apply_rumor_if_fresh(Rumor("key", "remote", 1, "n2", 1)) is False

    def test_member_merge_newer_heartbeat_does_not_revive_failed_member(self) -> None:
        gossip = GossipProtocol("n1", "a")
        gossip.add_peer("n2", "old")
        gossip._members["n2"].status = MemberStatus.FAILED
        gossip._merge_remote_members([Member("n2", "new", MemberStatus.ALIVE, 4, 10.0, {"rack": 2})])
        assert gossip._members["n2"].status is MemberStatus.FAILED
        assert gossip._members["n2"].metadata == {"rack": 2}

    def test_selection_and_comparison_empty_paths(self) -> None:
        left = GossipProtocol("n1", "a")
        right = GossipProtocol("n2", "b")
        assert left.select_rumor_peers(Rumor("k", "v", 1, "n1", 1)) == []
        assert left.member_status_matches(right, "missing") is True
        left.add_peer("shared", "x")
        assert left.member_status_matches(right, "shared") is False
        left.put("only-left", 1)
        assert left.data_matches(right) is False
        assert right.data_matches(left) is False
        assert right.rumor_hop_count("missing") == -1

    def test_spread_skips_unknown_peer_and_duplicate(self) -> None:
        origin = GossipProtocol("n1", "a", rumor_fanout=2, seed=1)
        peer = GossipProtocol("n2", "b")
        origin.set_peers(["n2", "ghost"])
        rumor = origin.put_rumor("key", "value")
        peer._apply_rumor_if_fresh(rumor)
        assert spread_rumor({"n1": origin, "n2": peer}, rumor, "n1") == 0

    def test_round_and_convergence_empty_paths(self) -> None:
        node = GossipProtocol("n1", "a")
        node.set_peers(["ghost"])
        assert run_gossip_round({"n1": node}, "n1") == 0
        assert ensure_convergence({"n1": node}, rounds=0) is True


class TestDaemonAdapterHostedBranches:
    @pytest.mark.asyncio
    async def test_dispatch_uses_injected_task_builder(self) -> None:
        class Dispatcher:
            def __init__(self) -> None:
                self.task: object | None = None

            async def dispatch_one(self, task: object) -> object:
                self.task = task
                return object()

        dispatcher = Dispatcher()
        sentinel = object()
        await make_dispatch_fn(dispatcher, task_builder=lambda unit_id: sentinel)("unit")
        assert dispatcher.task is sentinel

    @pytest.mark.asyncio
    async def test_merge_skips_vanished_file_and_reclaims(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        worktree = tmp_path / "worktree"
        repo.mkdir()
        worktree.mkdir()
        reclaimed: list[str] = []
        outcome = await make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["vanished.txt"],
            reclaim=reclaimed.append,
        )(CompletedUnit("u1", str(worktree)))
        assert outcome.merged is True
        assert reclaimed == [str(worktree)]

    def test_disk_check_is_fail_open_on_usage_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def fail_usage(path: str) -> Any:
            raise OSError(path)

        monkeypatch.setattr(shutil, "disk_usage", fail_usage)
        assert make_disk_ok(str(tmp_path), floor_mib=1)() is True

    @pytest.mark.asyncio
    async def test_default_changed_files_handles_git_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        worktree = tmp_path / "worktree"
        repo.mkdir()
        worktree.mkdir()

        def failed_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 1, "", "failed")

        monkeypatch.setattr(subprocess, "run", failed_run)
        outcome = await make_merge_fn(str(repo))(CompletedUnit("u1", str(worktree)))
        assert outcome.merged is True
        assert outcome.detail == "empty"
