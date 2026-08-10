"""Deep edge-case tests for coordination router and FileClaimRegistry.

Covers boundary validation, TTL/staleness, claim_or_conflict atomicity,
thread safety, scale, and router degradation paths not exercised by
existing tests.
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from general_ludd.coordination.file_claims import DEFAULT_TTL_SECONDS, FileClaimRegistry
from general_ludd.routers import coordination
from general_ludd.routers.coordination import ClaimRequest, ReleaseRequest

# ============================================================================
# Request model validation (Pydantic edge cases)
# ============================================================================


class TestClaimRequestValidation:
    def test_worker_id_exactly_one_char(self) -> None:
        req = ClaimRequest(worker_id="x", files=["a.py"])
        assert req.worker_id == "x"

    def test_worker_id_exactly_256_chars(self) -> None:
        long_id = "w" * 256
        req = ClaimRequest(worker_id=long_id, files=["a.py"])
        assert len(req.worker_id) == 256

    def test_worker_id_257_chars_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClaimRequest(worker_id="w" * 257, files=["a.py"])

    def test_worker_id_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClaimRequest(worker_id="", files=["a.py"])

    def test_worker_id_unicode(self) -> None:
        req = ClaimRequest(worker_id="wörker-漢字🔥", files=["a.py"])
        assert req.worker_id == "wörker-漢字🔥"

    def test_worker_id_with_spaces(self) -> None:
        req = ClaimRequest(worker_id="agent 42", files=["a.py"])
        assert req.worker_id == "agent 42"

    def test_files_defaults_to_empty_list(self) -> None:
        req = ClaimRequest(worker_id="w1")
        assert req.files == []

    def test_files_with_special_characters(self) -> None:
        req = ClaimRequest(worker_id="w1", files=["src/foo bar.py", "path/with-hyphens.py", "dir/file (copy).py"])
        assert len(req.files) == 3
        assert "src/foo bar.py" in req.files

    def test_files_with_empty_string_entry(self) -> None:
        req = ClaimRequest(worker_id="w1", files=["a.py", "", "b.py"])
        assert "" in req.files

    def test_files_with_duplicate_paths(self) -> None:
        req = ClaimRequest(worker_id="w1", files=["a.py", "a.py", "b.py"])
        assert len(req.files) == 3


class TestReleaseRequestValidation:
    def test_worker_id_exactly_256_chars(self) -> None:
        req = ReleaseRequest(worker_id="r" * 256)
        assert len(req.worker_id) == 256

    def test_worker_id_257_chars_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReleaseRequest(worker_id="r" * 257)

    def test_worker_id_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReleaseRequest(worker_id="")


# ============================================================================
# Router fixtures
# ============================================================================


@pytest.fixture()
def app() -> FastAPI:
    _app = FastAPI()
    coordination.register(_app, {})
    return _app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ============================================================================
# Router: claim endpoint deep edge cases
# ============================================================================


class TestClaimEdgeCases:
    def test_claim_updates_existing_worker_file_set(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py", "b.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["c.py"]})
        data = client.get("/api/coordination/claims").json()
        assert "c.py" in data["claims"]
        assert "a.py" not in data["claims"]
        assert "b.py" not in data["claims"]

    def test_claim_duplicate_files_in_request_deduped_by_registry(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py", "a.py", "a.py"]})
        data = client.get("/api/coordination/claims").json()
        assert data["claims"]["a.py"] == ["w1"]

    def test_claim_multiple_workers_non_overlapping(self, client: TestClient) -> None:
        for i in range(10):
            client.post("/api/coordination/claim", json={"worker_id": f"w{i}", "files": [f"file_{i}.py"]})
        data = client.get("/api/coordination/claims").json()
        assert len(data["claims"]) == 10
        assert data["merge_plan"] == {}

    def test_claim_three_workers_on_same_file(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["shared.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["shared.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w3", "files": ["shared.py"]})
        data = client.get("/api/coordination/claims").json()
        assert set(data["claims"]["shared.py"]) == {"w1", "w2", "w3"}
        assert data["merge_plan"]["shared.py"] == "union"

    def test_claim_worker_id_with_unicode_and_special_chars(self, client: TestClient) -> None:
        wid = "wörker-αβγ-№✓"
        resp = client.post("/api/coordination/claim", json={"worker_id": wid, "files": ["x.py"]})
        assert resp.status_code == 201
        data = client.get("/api/coordination/claims").json()
        assert wid in data["claims"]["x.py"]

    def test_claim_response_contains_files(self, client: TestClient) -> None:
        resp = client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py", "b.py", "c.py"]})
        data = resp.json()
        assert "a.py" in data["files"]
        assert "b.py" in data["files"]
        assert "c.py" in data["files"]


# ============================================================================
# Router: release endpoint deep edge cases
# ============================================================================


class TestReleaseEdgeCases:
    def test_release_twice_is_idempotent(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py"]})
        r1 = client.post("/api/coordination/release", json={"worker_id": "w1"})
        r2 = client.post("/api/coordination/release", json={"worker_id": "w1"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["released"] is True
        assert r2.json()["released"] is True

    def test_release_returns_worker_id_in_response(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "agent-7", "files": ["f.py"]})
        resp = client.post("/api/coordination/release", json={"worker_id": "agent-7"})
        assert resp.json()["worker_id"] == "agent-7"

    def test_release_one_worker_does_not_affect_others(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["shared.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["shared.py"]})
        client.post("/api/coordination/release", json={"worker_id": "w1"})
        data = client.get("/api/coordination/claims").json()
        assert "shared.py" in data["claims"]
        assert data["claims"]["shared.py"] == ["w2"]

    def test_release_clears_merge_plan_when_conflict_resolved(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["x.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["x.py"]})
        assert client.get("/api/coordination/claims").json()["merge_plan"] == {"x.py": "union"}
        client.post("/api/coordination/release", json={"worker_id": "w2"})
        assert client.get("/api/coordination/claims").json()["merge_plan"] == {}


# ============================================================================
# Router: overlaps endpoint deep edge cases
# ============================================================================


class TestOverlapsEdgeCases:
    def test_overlaps_three_workers_shared_file(self, client: TestClient) -> None:
        for wid in ("w1", "w2", "w3"):
            client.post("/api/coordination/claim", json={"worker_id": wid, "files": ["shared.py"]})
        data = client.get("/api/coordination/overlaps?worker_id=w1").json()
        assert set(data["overlaps"]["shared.py"]) == {"w2", "w3"}

    def test_overlaps_multiple_files_partial_overlap(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py", "b.py", "c.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["b.py", "d.py"]})
        data = client.get("/api/coordination/overlaps?worker_id=w1").json()
        assert "b.py" in data["overlaps"]
        assert "a.py" not in data["overlaps"]
        assert "c.py" not in data["overlaps"]

    def test_overlaps_worker_claims_then_checks_self(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["f.py"]})
        data = client.get("/api/coordination/overlaps?worker_id=w1").json()
        assert data["overlaps"] == {}
        assert data["should_wait"] == []

    def test_overlaps_after_release_clears(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["f.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["f.py"]})
        client.post("/api/coordination/release", json={"worker_id": "w2"})
        data = client.get("/api/coordination/overlaps?worker_id=w1").json()
        assert data["overlaps"] == {}
        assert data["should_wait"] == []

    def test_overlaps_should_wait_deduplicates(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py", "b.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["a.py", "b.py"]})
        data = client.get("/api/coordination/overlaps?worker_id=w1").json()
        assert data["should_wait"] == ["w2"]

    def test_overlaps_unicode_worker_id(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "wörker-ñü", "files": ["x.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "worker-ß", "files": ["x.py"]})
        data = client.get("/api/coordination/overlaps?worker_id=wörker-ñü").json()
        assert "worker-ß" in data["overlaps"]["x.py"]


# ============================================================================
# Router: claims endpoint deep edge cases
# ============================================================================


class TestClaimsEdgeCases:
    def test_claims_by_worker_has_expected_keys(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["x.py"]})
        data = client.get("/api/coordination/claims").json()
        bw = data["claims_by_worker"]
        assert "w1" in bw
        assert bw["w1"]["files"] == ["x.py"]
        assert "claimed_at" in bw["w1"]
        assert "age_seconds" in bw["w1"]
        assert bw["w1"]["age_seconds"] >= 0

    def test_claims_by_worker_files_are_sorted(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["c.py", "a.py", "b.py"]})
        data = client.get("/api/coordination/claims").json()
        assert data["claims_by_worker"]["w1"]["files"] == ["a.py", "b.py", "c.py"]

    def test_claims_by_worker_absent_after_release(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["f.py"]})
        client.post("/api/coordination/release", json={"worker_id": "w1"})
        data = client.get("/api/coordination/claims").json()
        assert "w1" not in data["claims_by_worker"]

    def test_claims_multiple_workers_sorted(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "worker_c", "files": ["f.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "worker_a", "files": ["f.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "worker_b", "files": ["f.py"]})
        data = client.get("/api/coordination/claims").json()
        assert data["claims"]["f.py"] == ["worker_a", "worker_b", "worker_c"]


# ============================================================================
# Router: facet degradation (uninitialized app.state)
# ============================================================================


class TestFacetDegradation:
    def test_facet_graceful_when_registry_missing(self) -> None:
        app = FastAPI()
        result = coordination._coordination_facet(app)
        assert result == {"claims": {}, "merge_plan": {}}


# ============================================================================
# FileClaimRegistry: controlled clock for TTL tests
# ============================================================================


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestRegistryTTL:
    def test_fresh_claim_not_stale(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=10.0, clock=clock)
        reg.claim("w1", ["a.py"])
        assert "a.py" in reg.all_claims()

    def test_claim_stale_after_ttl_exceeded(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=10.0, clock=clock)
        reg.claim("w1", ["a.py"])
        clock.advance(11.0)
        assert reg.all_claims() == {}

    def test_claim_stale_exactly_at_ttl_boundary(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=10.0, clock=clock)
        reg.claim("w1", ["a.py"])
        clock.advance(10.0)
        assert "a.py" in reg.all_claims()  # strict >, exactly at TTL is not stale

    def test_claim_not_stale_just_before_ttl(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=10.0, clock=clock)
        reg.claim("w1", ["a.py"])
        clock.advance(9.99)
        claims = reg.all_claims()
        assert "a.py" in claims

    def test_heartbeat_refreshes_ttl(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=10.0, clock=clock)
        reg.claim("w1", ["a.py"])
        clock.advance(9.0)
        reg.claim("w1", ["a.py"])
        clock.advance(9.0)
        assert "a.py" in reg.all_claims()
        clock.advance(2.0)
        assert reg.all_claims() == {}

    def test_heartbeat_with_different_files_refreshes_ttl(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=10.0, clock=clock)
        reg.claim("w1", ["a.py"])
        clock.advance(9.0)
        reg.claim("w1", ["b.py", "c.py"])
        clock.advance(9.0)
        claims = reg.all_claims()
        assert "b.py" in claims
        assert "c.py" in claims
        assert "a.py" not in claims

    def test_reap_stale_returns_sorted_worker_ids(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=5.0, clock=clock)
        reg.claim("w_c", ["a.py"])
        reg.claim("w_a", ["b.py"])
        reg.claim("w_b", ["c.py"])
        clock.advance(10.0)
        reaped = reg.reap_stale()
        assert reaped == ["w_a", "w_b", "w_c"]

    def test_overlaps_reaps_stale_before_checking(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=5.0, clock=clock)
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        clock.advance(10.0)
        assert reg.overlaps("w3") == {}

    def test_claims_with_age_has_reasonable_values(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=60.0, clock=clock)
        reg.claim("w1", ["x.py"])
        clock.advance(2.5)
        cwa = reg.claims_with_age()
        assert cwa["w1"]["age_seconds"] == pytest.approx(2.5)

    def test_claims_with_age_excludes_stale(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=5.0, clock=clock)
        reg.claim("w1", ["a.py"])
        clock.advance(10.0)
        assert reg.claims_with_age() == {}

    def test_overlaps_does_not_include_self(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "b.py"])
        reg.claim("w2", ["b.py"])
        overlaps = reg.overlaps("w1")
        assert "b.py" in overlaps
        assert "a.py" not in overlaps

    def test_merge_plan_empty_after_release_clears_conflict(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["x.py"])
        reg.claim("w2", ["x.py"])
        assert reg.merge_plan() == {"x.py": "union"}
        reg.release("w2")
        assert reg.merge_plan() == {}

    def test_merge_plan_three_workers_all_union(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["f.py"])
        reg.claim("w2", ["f.py"])
        reg.claim("w3", ["f.py"])
        assert reg.merge_plan() == {"f.py": "union"}

    def test_default_ttl_is_900_seconds(self) -> None:
        reg = FileClaimRegistry()
        assert reg._ttl_seconds == DEFAULT_TTL_SECONDS == 900.0

    def test_custom_ttl_respected(self) -> None:
        reg = FileClaimRegistry(ttl_seconds=30.0)
        assert reg._ttl_seconds == 30.0

    def test_claim_or_conflict_succeeds_with_no_overlap(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        result = reg.claim_or_conflict("w2", ["b.py"])
        assert result is True

    def test_claim_or_conflict_fails_with_overlap(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        result = reg.claim_or_conflict("w2", ["a.py"])
        assert result is False

    def test_claim_or_conflict_fails_partial_overlap(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        result = reg.claim_or_conflict("w2", ["a.py", "b.py"])
        assert result is False
        assert reg.overlaps("w2") == {}

    def test_claim_or_conflict_heartbeat_succeeds(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        result = reg.claim_or_conflict("w1", ["a.py"])
        assert result is True

    def test_claim_or_conflict_heartbeat_with_different_files(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        result = reg.claim_or_conflict("w1", ["b.py"])
        assert result is True
        assert "b.py" in reg.all_claims()
        assert "a.py" not in reg.all_claims()

    def test_claim_or_conflict_all_or_nothing_no_partial_claim(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "b.py"])
        result = reg.claim_or_conflict("w2", ["a.py", "c.py"])
        assert result is False
        assert reg.overlaps("w2") == {}
        data = reg.all_claims()
        assert "c.py" not in data

    def test_claim_or_conflict_reaps_stale_before_check(self) -> None:
        clock = FakeClock()
        reg = FileClaimRegistry(ttl_seconds=5.0, clock=clock)
        reg.claim("w1", ["a.py"])
        clock.advance(10.0)
        result = reg.claim_or_conflict("w2", ["a.py"])
        assert result is True

    def test_claim_or_conflict_sorts_files_for_determinism(self) -> None:
        reg = FileClaimRegistry()
        result1 = reg.claim_or_conflict("w1", ["c.py", "a.py", "b.py"])
        result2 = reg.claim_or_conflict("w2", ["b.py", "c.py", "a.py"])
        assert result1 is True
        assert result2 is False

    def test_release_unknown_worker_no_op(self) -> None:
        reg = FileClaimRegistry()
        reg.release("nonexistent")

    def test_should_wait_deduplicates_across_multiple_files(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "b.py", "c.py"])
        reg.claim("w2", ["a.py", "c.py"])
        wait = reg.should_wait("w1")
        assert wait == ["w2"]

    def test_all_claims_return_sorted_workers(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("worker_c", ["f.py"])
        reg.claim("worker_a", ["f.py"])
        reg.claim("worker_b", ["f.py"])
        claims = reg.all_claims()
        assert claims["f.py"] == ["worker_a", "worker_b", "worker_c"]


# ============================================================================
# FileClaimRegistry: thread safety
# ============================================================================


class TestRegistryThreadSafety:
    def test_concurrent_claims_no_corruption(self) -> None:
        reg = FileClaimRegistry()
        errors: list[Exception] = []

        def claim_files(prefix: str, n: int) -> None:
            try:
                for i in range(n):
                    reg.claim(f"{prefix}_{i}", [f"file_{i}.py"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=claim_files, args=(f"t{t}", 100)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        claims = reg.all_claims()
        assert len(claims) == 100

    def test_concurrent_claim_or_conflict_no_corruption(self) -> None:
        reg = FileClaimRegistry()
        errors: list[Exception] = []

        def claim_range(prefix: str, start: int, end: int) -> None:
            try:
                reg.claim_or_conflict(prefix, [f"shared_{i}.py" for i in range(start, end)])
            except Exception as exc:
                errors.append(exc)

        N_WORKERS = 8
        N_FILES = 80
        per = N_FILES // N_WORKERS
        threads = [
            threading.Thread(target=claim_range, args=(f"w{t}", t * per, (t + 1) * per)) for t in range(N_WORKERS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        data = reg.all_claims()
        assert len(data) == N_FILES
        for i in range(N_FILES):
            key = f"shared_{i}.py"
            assert key in data
            assert len(data[key]) == 1

    def test_concurrent_release_no_corruption(self) -> None:
        reg = FileClaimRegistry()
        for i in range(100):
            reg.claim(f"w{i}", [f"f{i}.py"])

        errors: list[Exception] = []

        def release_all() -> None:
            try:
                for i in range(100):
                    reg.release(f"w{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=release_all) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert reg.all_claims() == {}


# ============================================================================
# Scale tests
# ============================================================================


class TestRegistryScale:
    def test_claim_thousand_files(self) -> None:
        reg = FileClaimRegistry()
        files = [f"src/module_{i}.py" for i in range(1000)]
        reg.claim("w1", files)
        claims = reg.all_claims()
        assert len(claims) == 1000

    def test_claim_thousand_workers(self) -> None:
        reg = FileClaimRegistry()
        for i in range(1000):
            reg.claim(f"worker_{i}", [f"unique_{i}.py"])
        claims = reg.all_claims()
        assert len(claims) == 1000

    def test_overlaps_thousand_file_worker(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", [f"f{i}.py" for i in range(500)])
        reg.claim("w2", [f"f{i}.py" for i in range(250, 750)])
        ov = reg.overlaps("w1")
        assert len(ov) == 250

    def test_all_claims_after_mixed_lifecycle(self) -> None:
        reg = FileClaimRegistry()
        for i in range(50):
            reg.claim(f"w{i}", ["shared.py", f"unique_{i}.py"])
        for i in range(25):
            reg.release(f"w{i}")
        claims = reg.all_claims()
        assert len(claims["shared.py"]) == 25
        assert len(claims) == 26


# ============================================================================
# _get_registry helper
# ============================================================================


class TestGetRegistry:
    def test_get_registry_raises_attribute_error_when_missing(self) -> None:
        app = FastAPI()
        with pytest.raises(AttributeError):
            coordination._get_registry(app)


# ============================================================================
# Data integrity: claim/release/reclaim cycles
# ============================================================================


class TestDataIntegrity:
    def test_claim_release_reclaim_same_worker(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.release("w1")
        reg.claim("w1", ["a.py"])
        assert reg.all_claims() == {"a.py": ["w1"]}

    def test_claim_empty_files_then_claim_real_files(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", [])
        assert reg.all_claims() == {}
        reg.claim("w1", ["a.py", "b.py"])
        assert len(reg.all_claims()) == 2

    def test_file_worker_inverted_index_consistency(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "b.py"])
        reg.claim("w2", ["b.py", "c.py"])
        reg.release("w1")
        claims = reg.all_claims()
        assert claims == {"b.py": ["w2"], "c.py": ["w2"]}
