"""Unit tests for src/general_ludd/small_models/hf_auth.py.

Dedicated test file for HfOidcAuth, OidcToken, and _extract_expiry.
Covers edge cases, error paths, and behavior not exercised by test_model_download_auth.py
or test_oidc_lifecycle_deep.py.
"""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.small_models.hf_auth import (
    _DEFAULT_TTL_SEC,
    _OIDC_BUFFER_SEC,
    HfOidcAuth,
    OidcToken,
)


def _jwt(payload: dict) -> str:
    header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.sig"


class TestOidcTokenEdgeCases:
    def test_float_exp_value(self):
        exp = time.time() + 100.5
        tok = OidcToken(token="x", expires_at=exp, provider="test")
        assert tok.expires_at == exp

    def test_int_exp_true_is_expired(self):
        tok = OidcToken(token="x", expires_at=int(time.time()) + 10, provider="test")
        assert tok.is_expired

    def test_remaining_at_exact_buffer_boundary(self):
        now = time.time()
        tok = OidcToken(token="x", expires_at=now + _OIDC_BUFFER_SEC, provider="test")
        assert tok.is_expired
        assert tok.remaining_seconds == pytest.approx(_OIDC_BUFFER_SEC, abs=1)

    def test_remaining_just_beyond_buffer(self):
        tok = OidcToken(token="x", expires_at=time.time() + _OIDC_BUFFER_SEC + 1, provider="test")
        assert not tok.is_expired
        assert tok.remaining_seconds == pytest.approx(_OIDC_BUFFER_SEC + 1, abs=1)

    def test_explicit_acquired_at_zero_triggers_default(self):
        tok = OidcToken(token="x", expires_at=time.time() + 3600, provider="test", acquired_at=0.0)
        assert tok.acquired_at > 0
        assert abs(tok.acquired_at - time.time()) < 2

    def test_acquired_at_unchanged_when_provided(self):
        val = time.time() - 500
        tok = OidcToken(token="x", expires_at=time.time() + 3600, provider="x", acquired_at=val)
        assert tok.acquired_at == val


class TestHfOidcAuthConstructionEdgeCases:
    def test_partial_env_only_provider(self):
        with patch.dict("os.environ", {"HF_OIDC_PROVIDER": "aws"}, clear=True):
            auth = HfOidcAuth()
            assert auth.provider == "aws"
            assert auth.endpoint == ""
            assert auth.client_id == ""

    def test_partial_env_only_endpoint(self):
        with patch.dict("os.environ", {"HF_OIDC_ENDPOINT": "https://ep/token"}, clear=True):
            auth = HfOidcAuth()
            assert auth.provider == ""
            assert auth.endpoint == "https://ep/token"
            assert auth.client_id == ""

    def test_explicit_provider_overrides_env_client_id(self):
        with patch.dict("os.environ", {"HF_OIDC_CLIENT_ID": "env_cid"}, clear=True):
            auth = HfOidcAuth(provider="gcp")
            assert auth.provider == "gcp"
            assert auth.client_id == "env_cid"

    def test_custom_ttl_stored(self):
        auth = HfOidcAuth(provider="env", token_ttl=123)
        assert auth.token_ttl == 123

    def test_default_ttl(self):
        auth = HfOidcAuth()
        assert auth.token_ttl == _DEFAULT_TTL_SEC


class TestGetToken:
    def test_cached_valid_token_no_reacquire(self):
        token_str = _jwt({"sub": "x", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="env")
        with patch.dict("os.environ", {"OIDC_TOKEN": token_str}, clear=True):
            auth.get_token()
        with patch.object(auth, "_acquire") as mock_acq:
            result = auth.get_token()
        assert result == token_str
        mock_acq.assert_not_called()

    def test_expired_cached_triggers_reacquire(self):
        auth = HfOidcAuth(provider="env")
        with patch.dict("os.environ", {"OIDC_TOKEN": "first"}, clear=True):
            auth.get_token()
        cached = auth._cached
        assert cached is not None
        cached.expires_at = time.time() - 10
        with patch.dict("os.environ", {"OIDC_TOKEN": "second"}, clear=True):
            result = auth.get_token()
        assert result == "second"

    def test_empty_env_token_acquire_returns_none(self):
        with patch.dict("os.environ", {"OIDC_TOKEN": ""}, clear=True):
            auth = HfOidcAuth(provider="env")
            assert auth.get_token() is None

    def test_no_provider_no_endpoint_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            auth = HfOidcAuth()
            assert auth.get_token() is None

    def test_no_cached_and_acquire_returns_none(self):
        auth = HfOidcAuth(provider="env")
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("general_ludd.small_models.hf_auth.acquire_oidc_token", return_value=None),
        ):
            assert auth.get_token() is None

    def test_acquire_returns_none_but_stale_cached_valid(self):
        token_str = "stale_but_valid"
        auth = HfOidcAuth(provider="custom")
        auth._cached = OidcToken(
            token=token_str,
            expires_at=time.time() + _OIDC_BUFFER_SEC - 5,
            provider="custom",
        )
        with patch.object(auth, "_acquire", return_value=None):
            result = auth.get_token()
        assert result == token_str

    def test_acquire_returns_none_and_cached_truly_expired(self):
        auth = HfOidcAuth(provider="custom")
        auth._cached = OidcToken(token="dead", expires_at=time.time() - 3600, provider="custom")
        with patch.object(auth, "_acquire", return_value=None):
            assert auth.get_token() is None


class TestRefreshInvalidate:
    def test_refresh_returns_none_when_no_auth(self):
        with patch.dict("os.environ", {}, clear=True):
            auth = HfOidcAuth()
            assert auth.refresh() is None

    def test_invalidate_on_empty_cache(self):
        auth = HfOidcAuth(provider="env")
        auth.invalidate()
        assert auth._cached is None
        assert not auth.has_valid_token()

    def test_refresh_sets_new_token(self):
        auth = HfOidcAuth(provider="env")
        with patch.dict("os.environ", {"OIDC_TOKEN": "first"}, clear=True):
            auth.get_token()
        auth.invalidate()
        with patch.dict("os.environ", {"OIDC_TOKEN": "refreshed_val"}, clear=True):
            result = auth.refresh()
        assert result == "refreshed_val"


class TestHasValidToken:
    def test_no_cached_returns_false(self):
        auth = HfOidcAuth(provider="env")
        assert not auth.has_valid_token()

    def test_cached_and_valid_returns_true(self):
        auth = HfOidcAuth(provider="env")
        with patch.dict("os.environ", {"OIDC_TOKEN": "tok"}, clear=True):
            auth.get_token()
        assert auth.has_valid_token()

    def test_cached_but_expired_returns_false(self):
        auth = HfOidcAuth(provider="env")
        with patch.dict("os.environ", {"OIDC_TOKEN": "tok"}, clear=True):
            auth.get_token()
        cached = auth._cached
        assert cached is not None
        cached.expires_at = time.time() - 1
        assert not auth.has_valid_token()


class TestExtractExpiry:
    def test_jwt_with_float_exp(self):
        auth = HfOidcAuth(provider="env")
        exp = time.time() + 500.25
        token_str = _jwt({"sub": "x", "exp": exp})
        result = auth._extract_expiry(token_str)
        assert result == pytest.approx(exp, abs=1)

    def test_jwt_with_int_exp(self):
        auth = HfOidcAuth(provider="env")
        exp = int(time.time()) + 1000
        token_str = _jwt({"sub": "x", "exp": exp})
        result = auth._extract_expiry(token_str)
        assert isinstance(result, float)
        assert result == pytest.approx(float(exp), abs=1)

    def test_non_jwt_token_non_string_exp(self):
        auth = HfOidcAuth(provider="env", token_ttl=300)
        assert auth._extract_expiry("just_a_string") == pytest.approx(time.time() + 300, abs=2)

    def test_jwt_with_corrupt_base64_body(self):
        auth = HfOidcAuth(provider="env", token_ttl=400)
        token_str = "header.!!!not_base64!!.sig"
        assert auth._extract_expiry(token_str) == pytest.approx(time.time() + 400, abs=2)

    def test_jwt_exp_as_string_falls_back(self):
        auth = HfOidcAuth(provider="env", token_ttl=200)
        token_str = _jwt({"sub": "x", "exp": "not_a_number"})
        assert auth._extract_expiry(token_str) == pytest.approx(time.time() + 200, abs=2)

    def test_jwt_exp_as_json_bool_is_treated_as_int_one(self):
        auth = HfOidcAuth(provider="env")
        token_str = _jwt({"sub": "x", "exp": True})
        assert auth._extract_expiry(token_str) == 1.0

    def test_jwt_with_zero_padding_needed(self):
        auth = HfOidcAuth(provider="env")
        payload = {"sub": "x", "exp": int(time.time()) + 600}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        assert len(payload_b64) % 4 != 0
        token_str = f"head.{payload_b64}.sig"
        result = auth._extract_expiry(token_str)
        assert result == pytest.approx(payload["exp"], abs=1)

    def test_falls_back_on_index_error(self):
        auth = HfOidcAuth(provider="env", token_ttl=100)
        assert auth._extract_expiry("no_dots_at_all") == pytest.approx(time.time() + 100, abs=2)


class TestFetchFromEndpoint:
    def test_returns_token_field(self):
        auth = HfOidcAuth(endpoint="https://ep", client_id="cid")
        resp_json = json.dumps({"token": "tok_val", "expires_in": 3600})
        with patch("urllib.request.Request") as mock_req, patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = resp_json.encode()
            result = auth._fetch_from_endpoint("https://ep")
            assert result == "tok_val"
            mock_req.assert_called_once_with("https://ep")

    def test_returns_access_token_field(self):
        auth = HfOidcAuth(endpoint="https://ep")
        resp_json = json.dumps({"access_token": "at_val"})
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = resp_json.encode()
            result = auth._fetch_from_endpoint("https://ep")
            assert result == "at_val"

    def test_returns_id_token_field(self):
        auth = HfOidcAuth(endpoint="https://ep")
        resp_json = json.dumps({"id_token": "id_val"})
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = resp_json.encode()
            result = auth._fetch_from_endpoint("https://ep")
            assert result == "id_val"

    def test_prefers_token_over_access_token(self):
        auth = HfOidcAuth(endpoint="https://ep")
        resp_json = json.dumps({"token": "preferred", "access_token": "ignored"})
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = resp_json.encode()
            result = auth._fetch_from_endpoint("https://ep")
            assert result == "preferred"

    def test_returns_none_when_no_token_fields(self):
        auth = HfOidcAuth(endpoint="https://ep")
        resp_json = json.dumps({"message": "no token here"})
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = resp_json.encode()
            result = auth._fetch_from_endpoint("https://ep")
            assert result is None

    def test_returns_none_on_http_error(self):
        auth = HfOidcAuth(endpoint="https://ep")
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = auth._fetch_from_endpoint("https://ep")
            assert result is None

    def test_returns_none_on_json_decode_error(self):
        auth = HfOidcAuth(endpoint="https://ep")
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"not json"
            result = auth._fetch_from_endpoint("https://ep")
            assert result is None

    def test_adds_client_id_header_when_present(self):
        auth = HfOidcAuth(endpoint="https://ep", client_id="my_client")
        resp_json = json.dumps({"token": "tok"})
        with patch("urllib.request.Request") as mock_req_class:
            mock_req = MagicMock()
            mock_req_class.return_value = mock_req
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = resp_json.encode()
                auth._fetch_from_endpoint("https://ep")
            calls = mock_req.add_header.call_args_list
            added_headers = {args[0][0]: args[0][1] for args in calls if len(args[0]) == 2}
            assert added_headers.get("X-Client-ID") == "my_client"
            assert added_headers.get("Accept") == "application/json"

    def test_no_client_id_header_when_empty(self):
        auth = HfOidcAuth(endpoint="https://ep")
        resp_json = json.dumps({"token": "tok"})
        with patch("urllib.request.Request") as mock_req_class:
            mock_req = MagicMock()
            mock_req_class.return_value = mock_req
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = resp_json.encode()
                auth._fetch_from_endpoint("https://ep")
            calls = mock_req.add_header.call_args_list
            header_names = [args[0][0] for args in calls if len(args[0]) == 2]
            assert "X-Client-ID" not in header_names


class TestEndpointAcquireFlow:
    def test_endpoint_acquire_uses_fetch_not_oidc(self):
        token_str = _jwt({"sub": "ep", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="", endpoint="https://custom/token")
        with patch.object(auth, "_fetch_from_endpoint", return_value=token_str):
            result = auth.get_token()
        assert result == token_str
        assert auth.has_valid_token()

    def test_endpoint_acquire_returns_none_on_empty_resp(self):
        auth = HfOidcAuth(provider="", endpoint="https://custom/token")
        with patch.object(auth, "_fetch_from_endpoint", return_value=None):
            result = auth.get_token()
        assert result is None

    def test_provider_takes_priority_over_endpoint(self):
        token_str = _jwt({"sub": "p", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="aws", endpoint="https://custom/token")
        with patch.dict("os.environ", {"AWS_WEB_IDENTITY_TOKEN_FILE": "/dev/null"}, clear=True):
            src = None

            def capture_fetch(endpoint):
                nonlocal src
                src = "endpoint"
                return token_str

            def capture_acquire(provider, client_id):
                nonlocal src
                src = "provider"
                return None

            with (
                patch.object(auth, "_fetch_from_endpoint", side_effect=capture_fetch),
                patch("general_ludd.small_models.hf_auth.acquire_oidc_token", side_effect=capture_acquire),
            ):
                auth.get_token()
        assert src == "provider"

    def test_endpoint_token_has_custom_provider_name(self):
        token_str = _jwt({"sub": "ep", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="", endpoint="https://ep")
        with patch.object(auth, "_fetch_from_endpoint", return_value=token_str):
            auth.get_token()
        assert auth._cached is not None
        assert auth._cached.provider == "custom_endpoint"

    def test_provider_flow_sets_provider_name(self):
        token_str = _jwt({"sub": "x", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="aws", token_ttl=3600)
        with patch("general_ludd.small_models.hf_auth.acquire_oidc_token", return_value=token_str):
            auth._acquire()
        assert auth._cached is not None
        assert auth._cached.provider == "aws"


class TestAcquireMethod:
    def test_acquire_returns_token_with_provider(self):
        token_str = _jwt({"sub": "x", "exp": int(time.time()) + 3600})
        with patch.dict("os.environ", {"OIDC_TOKEN": token_str}, clear=True):
            auth = HfOidcAuth(provider="env")
            result = auth._acquire()
        assert result is not None
        assert result.token == token_str
        assert result.provider == "env"

    def test_acquire_returns_none_when_no_provider_or_endpoint(self):
        auth = HfOidcAuth()
        assert auth._acquire() is None

    def test_acquire_warns_on_empty_token(self):
        with (
            patch.dict("os.environ", {"OIDC_TOKEN": ""}, clear=True),
            patch("logging.Logger.warning") as mock_warn,
        ):
            auth = HfOidcAuth(provider="env")
            result = auth._acquire()
        assert result is None
        mock_warn.assert_called_once()

    def test_acquire_stores_token_in_cache(self):
        token_str = _jwt({"sub": "x", "exp": int(time.time()) + 3600})
        with patch.dict("os.environ", {"OIDC_TOKEN": token_str}, clear=True):
            auth = HfOidcAuth(provider="env")
            result = auth._acquire()
        assert result is not None
        assert auth._cached is result
        assert result.token == token_str
