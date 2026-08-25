"""Deep tests for OIDC token lifecycle: caching, expiry, refresh, concurrent access, invalidation.

Tests cover: OidcToken dataclass, HfOidcAuth get_token/refresh/invalidate,
_extract_expiry JWT parsing, multi-thread concurrent access, stale-token fallback.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from unittest.mock import patch

import pytest

from general_ludd.small_models.hf_auth import (
    _DEFAULT_TTL_SEC,
    _OIDC_BUFFER_SEC,
    HfOidcAuth,
    OidcToken,
)
from general_ludd.small_models.oidc import acquire_oidc_token

# ── helpers ────────────────────────────────────────────────────────────────


def _jwt_token(exp_offset: float = 3600) -> str:
    """Build a minimal signed-looking JWT string with a given exp offset."""
    payload = {"sub": "test", "exp": time.time() + exp_offset}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{payload_b64}.signature"


def _non_jwt_token() -> str:
    return "ghp_mock_token_no_dots"


# ── OidcToken ──────────────────────────────────────────────────────────────


class TestOidcToken:
    def test_construction_sets_default_acquired_at(self):
        tok = OidcToken(token="t1", expires_at=time.time() + 3600, provider="aws")
        assert tok.acquired_at > 0
        assert abs(tok.acquired_at - time.time()) < 2

    def test_construction_respects_explicit_acquired_at(self):
        past = time.time() - 100
        tok = OidcToken(token="t2", expires_at=time.time() + 200, provider="gcp", acquired_at=past)
        assert tok.acquired_at == past

    def test_is_expired_false_when_far_from_expiry(self):
        tok = OidcToken(token="fresh", expires_at=time.time() + 7200, provider="env")
        assert not tok.is_expired

    def test_is_expired_true_when_past_buffer(self):
        tok = OidcToken(token="old", expires_at=time.time() + (_OIDC_BUFFER_SEC - 10), provider="aws")
        assert tok.is_expired

    def test_is_expired_true_when_already_past(self):
        tok = OidcToken(token="expired", expires_at=time.time() - 1, provider="gcp")
        assert tok.is_expired

    def test_remaining_seconds_positive_before_expiry(self):
        tok = OidcToken(token="live", expires_at=time.time() + 500, provider="azure")
        assert 490 <= tok.remaining_seconds <= 510

    def test_remaining_seconds_zero_when_expired(self):
        tok = OidcToken(token="dead", expires_at=time.time() - 10, provider="env")
        assert tok.remaining_seconds == 0.0


# ── HfOidcAuth construction ────────────────────────────────────────────────


class TestHfOidcAuthConstruction:
    def test_defaults_from_env_vars(self):
        with patch.dict(
            "os.environ",
            {
                "HF_OIDC_PROVIDER": "aws",
                "HF_OIDC_ENDPOINT": "https://example.com/token",
                "HF_OIDC_CLIENT_ID": "cid123",
            },
            clear=True,
        ):
            auth = HfOidcAuth()
            assert auth.provider == "aws"
            assert auth.endpoint == "https://example.com/token"
            assert auth.client_id == "cid123"
            assert auth.token_ttl == _DEFAULT_TTL_SEC
            assert auth._cached is None

    def test_explicit_args_override_env(self):
        with patch.dict(
            "os.environ",
            {
                "HF_OIDC_PROVIDER": "aws",
                "HF_OIDC_CLIENT_ID": "env_cid",
            },
            clear=True,
        ):
            auth = HfOidcAuth(provider="gcp", client_id="arg_cid", token_ttl=7200)
            assert auth.provider == "gcp"
            assert auth.client_id == "arg_cid"
            assert auth.token_ttl == 7200

    def test_all_empty_when_nothing_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            auth = HfOidcAuth()
            assert auth.provider == ""
            assert auth.endpoint == ""
            assert auth.client_id == ""


# ── Token caching ──────────────────────────────────────────────────────────


class TestTokenCaching:
    def test_get_token_caches_and_returns_same_token(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        token_str = _non_jwt_token()
        with patch.dict("os.environ", {"OIDC_TOKEN": token_str}, clear=True):
            t1 = auth.get_token()
            t2 = auth.get_token()
        assert t1 == token_str
        assert t2 == token_str
        cached = auth._cached
        assert cached is not None
        assert cached.token == token_str

    def test_get_token_refreshes_when_cached_token_expired(self):
        auth = HfOidcAuth(provider="env", token_ttl=1)
        with patch.dict("os.environ", {"OIDC_TOKEN": "first"}, clear=True):
            t1 = auth.get_token()
        assert t1 == "first"
        cached = auth._cached
        assert cached is not None
        cached.expires_at = time.time() - 1
        with patch.dict("os.environ", {"OIDC_TOKEN": "second"}, clear=True):
            t2 = auth.get_token()
        assert t2 == "second"

    def test_get_token_returns_none_when_no_provider_and_empty_env(self):
        with patch.dict("os.environ", {}, clear=True):
            auth = HfOidcAuth(provider="", endpoint="")
            assert auth.get_token() is None

    def test_get_token_returns_none_when_env_var_empty(self):
        with patch.dict("os.environ", {"OIDC_TOKEN": ""}, clear=True):
            auth = HfOidcAuth(provider="env")
            assert auth.get_token() is None


# ── Token expiry and refresh ───────────────────────────────────────────────


class TestTokenExpiryRefresh:
    def test_get_token_refreshes_when_within_buffer(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "expiring_soon"}, clear=True):
            t1 = auth.get_token()
        assert t1 == "expiring_soon"
        cached = auth._cached
        assert cached is not None
        cached.expires_at = time.time() + (_OIDC_BUFFER_SEC - 5)
        with patch.dict("os.environ", {"OIDC_TOKEN": "refreshed"}, clear=True):
            t2 = auth.get_token()
        assert t2 == "refreshed"

    def test_get_token_falls_back_to_stale_token_on_refresh_failure(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "cached_token"}, clear=True):
            t1 = auth.get_token()
        assert t1 == "cached_token"
        cached = auth._cached
        assert cached is not None
        cached.expires_at = time.time() + 120
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("general_ludd.small_models.hf_auth.acquire_oidc_token", return_value=None),
        ):
            t2 = auth.get_token()
        assert t2 == "cached_token"

    def test_get_token_returns_none_when_stale_token_truly_expired(self):
        auth = HfOidcAuth(provider="env", token_ttl=1)
        with patch.dict("os.environ", {"OIDC_TOKEN": "old_token"}, clear=True):
            auth.get_token()
        cached = auth._cached
        assert cached is not None
        cached.expires_at = time.time() - 10
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("general_ludd.small_models.oidc.acquire_oidc_token", return_value=None),
        ):
            assert auth.get_token() is None

    def test_refresh_forces_new_acquisition(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "old_token"}, clear=True):
            auth.get_token()
        with patch.dict("os.environ", {"OIDC_TOKEN": "new_token"}, clear=True):
            result = auth.refresh()
        assert result == "new_token"
        cached = auth._cached
        assert cached is not None
        assert cached.token == "new_token"

    def test_invalidate_clears_cache(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "token1"}, clear=True):
            auth.get_token()
            assert auth.has_valid_token()
        auth.invalidate()
        assert auth._cached is None
        assert not auth.has_valid_token()

    def test_has_valid_token(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "token_val"}, clear=True):
            assert not auth.has_valid_token()
            auth.get_token()
            assert auth.has_valid_token()
        cached = auth._cached
        assert cached is not None
        cached.expires_at = time.time() - 1
        assert not auth.has_valid_token()


# ── _extract_expiry ────────────────────────────────────────────────────────


class TestExtractExpiry:
    def test_extracts_exp_from_valid_jwt(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        jwt_str = _jwt_token(exp_offset=7200)
        exp = auth._extract_expiry(jwt_str)
        expected = json.loads(base64.urlsafe_b64decode(jwt_str.split(".")[1] + "===").decode())["exp"]
        assert exp == pytest.approx(expected, abs=1)

    def test_falls_back_to_ttl_for_non_jwt_token(self):
        auth = HfOidcAuth(provider="env", token_ttl=1800)
        now = time.time()
        exp = auth._extract_expiry("plaintext_token")
        assert exp == pytest.approx(now + 1800, abs=2)

    def test_falls_back_to_ttl_for_malformed_jwt(self):
        auth = HfOidcAuth(provider="env", token_ttl=900)
        exp = auth._extract_expiry("header.bad_payload!!.sig")
        assert exp == pytest.approx(time.time() + 900, abs=2)

    def test_falls_back_to_ttl_when_exp_missing(self):
        auth = HfOidcAuth(provider="env", token_ttl=600)
        payload = {"sub": "no_exp"}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        jwt_str = f"h.{payload_b64}.s"
        exp = auth._extract_expiry(jwt_str)
        assert exp == pytest.approx(time.time() + 600, abs=2)

    def test_falls_back_to_ttl_when_exp_not_number(self):
        auth = HfOidcAuth(provider="env", token_ttl=500)
        payload = {"sub": "test", "exp": "not_a_number"}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        jwt_str = f"h.{payload_b64}.s"
        exp = auth._extract_expiry(jwt_str)
        assert exp == pytest.approx(time.time() + 500, abs=2)


# ── Multi-thread concurrent access ─────────────────────────────────────────


class TestMultiThreadConcurrentAccess:
    def test_concurrent_get_token_acquires_only_once(self) -> None:
        acquire_count = [0]
        lock = threading.Lock()
        first_acquire = threading.Event()
        all_acquires = threading.Event()
        release_acquire = threading.Event()

        def counting_acquire(*_args: object, **_kwargs: object) -> str:
            with lock:
                acquire_count[0] += 1
                first_acquire.set()
                if acquire_count[0] == 8:
                    all_acquires.set()
            assert release_acquire.wait(timeout=2)
            return "shared_token"

        auth = HfOidcAuth(provider="aws", token_ttl=3600)
        results: list[str | None] = []

        def worker() -> None:
            results.append(auth.get_token())

        with patch("general_ludd.small_models.hf_auth.acquire_oidc_token", side_effect=counting_acquire):
            threads = [threading.Thread(target=worker, name=f"oidc-reader-{index}") for index in range(8)]
            for t in threads:
                t.start()
            assert first_acquire.wait(timeout=2)
            all_acquires.wait(timeout=0.1)
            release_acquire.set()
            for t in threads:
                t.join(timeout=2)

        assert len(results) == 8
        assert all(r == "shared_token" for r in results)
        assert acquire_count == [1]

    def test_concurrent_invalidate_propagates_to_all_threads(self) -> None:
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "t1"}, clear=True):
            auth.get_token()

        invalidation_seen: list[str] = []
        invalidated = threading.Event()

        def reader() -> None:
            assert invalidated.wait(timeout=2)
            if auth.has_valid_token():
                invalidation_seen.append("valid")
            else:
                invalidation_seen.append("invalid")

        def invalidator() -> None:
            auth.invalidate()
            invalidated.set()

        threads = [threading.Thread(target=reader, name=f"oidc-observer-{index}") for index in range(4)]
        threads.append(threading.Thread(target=invalidator, name="oidc-invalidator"))
        for t in threads:
            t.start()

        assert invalidated.wait(timeout=2)
        for t in threads:
            t.join(timeout=2)

        assert invalidation_seen == ["invalid"] * 4

    def test_invalidate_waits_for_inflight_acquisition_and_wins(self) -> None:
        auth = HfOidcAuth(provider="aws", token_ttl=3600)
        acquisition_started = threading.Event()
        release_acquisition = threading.Event()
        invalidation_returned = threading.Event()
        results: list[str | None] = []

        def blocking_acquire(*_args: object, **_kwargs: object) -> str:
            acquisition_started.set()
            assert release_acquisition.wait(timeout=2)
            return "inflight-token"

        def getter() -> None:
            results.append(auth.get_token())

        def invalidator() -> None:
            auth.invalidate()
            invalidation_returned.set()

        with patch("general_ludd.small_models.hf_auth.acquire_oidc_token", side_effect=blocking_acquire):
            get_thread = threading.Thread(target=getter, name="oidc-inflight-get")
            get_thread.start()
            assert acquisition_started.wait(timeout=2)

            invalidate_thread = threading.Thread(target=invalidator, name="oidc-inflight-invalidate")
            invalidate_thread.start()
            returned_before_release = invalidation_returned.wait(timeout=0.1)
            release_acquisition.set()
            get_thread.join(timeout=2)
            invalidate_thread.join(timeout=2)

        assert not returned_before_release
        assert invalidation_returned.is_set()
        assert results == ["inflight-token"]
        assert auth._cached is None

    def test_refresh_during_concurrent_reads_provides_new_token(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "old"}, clear=True):
            auth.get_token()

        results = []

        def reader():
            results.append(auth.get_token())

        def refresher():
            with patch.dict("os.environ", {"OIDC_TOKEN": "refreshed_token"}, clear=True):
                results.append(auth.refresh())

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads.append(threading.Thread(target=refresher))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert "refreshed_token" in results
        all_valid = set(results) - {None}
        assert len(all_valid) >= 1


# ── Token invalidation propagation ─────────────────────────────────────────


class TestInvalidationPropagation:
    def test_invalidate_then_get_token_reacquires(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "t1"}, clear=True):
            assert auth.get_token() == "t1"
        auth.invalidate()
        with patch.dict("os.environ", {"OIDC_TOKEN": "t2"}, clear=True):
            assert auth.get_token() == "t2"

    def test_refresh_clears_stale_cache(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        with patch.dict("os.environ", {"OIDC_TOKEN": "stale"}, clear=True):
            auth.get_token()
        cached = auth._cached
        assert cached is not None
        cached.expires_at = 0
        with patch.dict("os.environ", {"OIDC_TOKEN": "fresh"}, clear=True):
            token = auth.refresh()
        assert token == "fresh"

    def test_multiple_invalidate_cycles(self):
        auth = HfOidcAuth(provider="env", token_ttl=3600)
        for i in range(5):
            token_val = f"token_{i}"
            with patch.dict("os.environ", {"OIDC_TOKEN": token_val}, clear=True):
                result = auth.get_token()
            assert result == token_val
            auth.invalidate()
            assert auth._cached is None


# ── acquire_oidc_token provider dispatch ───────────────────────────────────


class TestAcquireOidcTokenDispatch:
    def test_env_provider_reads_oidc_token_env(self):
        with patch.dict("os.environ", {"OIDC_TOKEN": "env_token_value"}, clear=True):
            assert acquire_oidc_token("env") == "env_token_value"

    def test_env_provider_reads_hf_oidc_token_env(self):
        with patch.dict("os.environ", {"HF_OIDC_TOKEN": "hf_env_token"}, clear=True):
            assert acquire_oidc_token("env") == "hf_env_token"

    def test_env_provider_prefers_hf_oidc_token(self):
        with patch.dict(
            "os.environ",
            {
                "HF_OIDC_TOKEN": "hf_preferred",
                "OIDC_TOKEN": "fallback",
            },
            clear=True,
        ):
            assert acquire_oidc_token("env") == "hf_preferred"

    def test_unknown_provider_returns_none(self):
        assert acquire_oidc_token("nonexistent") is None

    def test_provider_case_insensitive(self):
        with patch.dict("os.environ", {"OIDC_TOKEN": "case_test"}, clear=True):
            assert acquire_oidc_token("ENV") == "case_test"

    def test_aws_provider_no_credentials_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            assert acquire_oidc_token("aws") is None


# ── end-to-end acquire + cache ─────────────────────────────────────────────


class TestE2EAcquireCache:
    def test_full_cycle_provider_acquire_to_cache(self):
        with patch.dict("os.environ", {"OIDC_TOKEN": _jwt_token(3600)}, clear=True):
            auth = HfOidcAuth(provider="env", token_ttl=3600)
            token = auth.get_token()
            assert token is not None
            assert auth.has_valid_token()
            cached = auth._cached
            assert cached is not None
            assert cached.provider == "env"

    def test_full_cycle_custom_endpoint_mocked(self):
        import urllib.request

        class FakeResponse:
            def read(self):
                return json.dumps({"access_token": _jwt_token(7200)}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        auth = HfOidcAuth(
            endpoint="https://mock.example.com/oidc",
            client_id="cid",
            token_ttl=3600,
        )
        with patch.object(urllib.request, "urlopen", return_value=FakeResponse()):
            token = auth.get_token()
        assert token is not None
        assert auth.has_valid_token()
        cached = auth._cached
        assert cached is not None
        assert cached.provider == "custom_endpoint"
