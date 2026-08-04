"""Unit tests for HuggingFace OIDC auth and ModelDownloader token resolution."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest

from general_ludd.small_models.download import ModelDownloader
from general_ludd.small_models.hf_auth import HfOidcAuth, OidcToken
from general_ludd.small_models.oidc import acquire_oidc_token


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


class TestOidcToken:
    def test_defaults(self):
        t = OidcToken(token="abc", expires_at=time.time() + 3600, provider="aws")
        assert t.token == "abc"
        assert t.provider == "aws"
        assert t.acquired_at > 0
        assert not t.is_expired

    def test_expired_token(self):
        t = OidcToken(token="abc", expires_at=time.time() - 1, provider="aws")
        assert t.is_expired

    def test_expiring_soon_within_buffer(self):
        t = OidcToken(token="abc", expires_at=time.time() + 30, provider="aws")
        assert t.is_expired

    def test_remaining_seconds(self):
        t = OidcToken(token="abc", expires_at=time.time() + 120, provider="aws")
        assert 60 <= t.remaining_seconds <= 120

    def test_remaining_seconds_expired(self):
        t = OidcToken(token="abc", expires_at=time.time() - 60, provider="aws")
        assert t.remaining_seconds == 0.0


class TestHfOidcAuthAcquire:
    def test_get_token_acquires_once(self):
        token_str = _make_jwt({"sub": "test", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="env")
        with patch.dict(os.environ, {"OIDC_TOKEN": token_str}):
            result = auth.get_token()
        assert result == token_str
        assert auth.has_valid_token()

    def test_get_token_uses_cache(self):
        token_str = _make_jwt({"sub": "test", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="env")
        with patch.dict(os.environ, {"OIDC_TOKEN": token_str}):
            auth.get_token()
        with patch.dict(os.environ, {}, clear=True):
            result = auth.get_token()
        assert result == token_str

    def test_get_token_refreshes_after_expiry(self):
        first = _make_jwt({"sub": "first", "exp": int(time.time()) - 1})
        second = _make_jwt({"sub": "second", "exp": int(time.time()) + 3600})

        auth = HfOidcAuth(provider="custom")
        with patch.object(auth, "_acquire") as mock_acquire:
            t1 = OidcToken(token=first, expires_at=time.time() - 1, provider="custom")
            t2 = OidcToken(token=second, expires_at=time.time() + 3600, provider="custom")
            mock_acquire.side_effect = [t1, t2]

            result1 = auth.get_token()
            assert result1 == first
            mock_acquire.assert_called_once()

            result2 = auth.get_token()
            assert result2 == second
            assert mock_acquire.call_count == 2

    def test_get_token_returns_stale_on_refresh_failure(self):
        token_str = "stale-token-value"
        auth = HfOidcAuth(provider="custom")
        with patch.object(auth, "_acquire") as mock_acquire:
            soon = OidcToken(token=token_str, expires_at=time.time() + 30, provider="custom")
            mock_acquire.return_value = None
            auth._cached = soon

            result = auth.get_token()
            assert result == token_str
            assert mock_acquire.call_count == 1

    def test_get_token_returns_none_when_no_auth_configured(self):
        auth = HfOidcAuth()
        assert auth.get_token() is None

    def test_get_token_returns_none_when_stale_and_refresh_fails(self):
        auth = HfOidcAuth(provider="custom")
        with patch.object(auth, "_acquire", return_value=None):
            auth._cached = OidcToken(token="old", expires_at=time.time() - 3600, provider="custom")
            assert auth.get_token() is None

    def test_refresh_clears_cache(self):
        first = _make_jwt({"sub": "first", "exp": int(time.time()) + 3600})
        second = _make_jwt({"sub": "second", "exp": int(time.time()) + 7200})
        auth = HfOidcAuth(provider="env")
        with patch.dict(os.environ, {"OIDC_TOKEN": first}):
            auth.get_token()
        with patch.dict(os.environ, {"OIDC_TOKEN": second}):
            result = auth.refresh()
        assert result == second

    def test_invalidate_clears_cache(self):
        token_str = _make_jwt({"sub": "test", "exp": int(time.time()) + 3600})
        auth = HfOidcAuth(provider="env")
        with patch.dict(os.environ, {"OIDC_TOKEN": token_str}):
            auth.get_token()
        assert auth.has_valid_token()
        auth.invalidate()
        assert not auth.has_valid_token()
        assert auth.get_token() is None

    def test_custom_endpoint_fetch(self):
        token_str = _make_jwt({"sub": "endpoint", "exp": int(time.time()) + 3600})
        url = "https://oidc.example.com/token"
        auth = HfOidcAuth(provider="", endpoint=url)
        with patch.object(auth, "_fetch_from_endpoint", return_value=token_str):
            result = auth.get_token()
        assert result == token_str


class TestJwtExpiryExtraction:
    def test_extracts_exp_from_valid_jwt(self):
        exp = int(time.time()) + 3600
        token_str = _make_jwt({"sub": "test", "exp": exp})
        auth = HfOidcAuth(provider="env")
        parsed = auth._extract_expiry(token_str)
        assert parsed == exp

    def test_extracts_exp_defaults_on_malformed(self):
        auth = HfOidcAuth(provider="env")
        parsed = auth._extract_expiry("not-a-jwt")
        assert parsed == pytest.approx(time.time() + auth.token_ttl, rel=0.1)

    def test_extracts_exp_defaults_on_missing_exp(self):
        token_str = _make_jwt({"sub": "test"})
        auth = HfOidcAuth(provider="env")
        parsed = auth._extract_expiry(token_str)
        assert parsed == pytest.approx(time.time() + auth.token_ttl, rel=0.1)


class TestAcquireOidcToken:
    def test_aws_web_identity_file(self, tmp_path):
        token_str = _make_jwt({"sub": "aws", "exp": int(time.time()) + 3600})
        token_file = tmp_path / "web_identity_token"
        token_file.write_text(token_str)
        with patch.dict(os.environ, {"AWS_WEB_IDENTITY_TOKEN_FILE": str(token_file)}, clear=True):
            result = acquire_oidc_token("aws")
        assert result == token_str

    def test_aws_no_credential_source(self):
        with patch.dict(os.environ, {}, clear=True):
            result = acquire_oidc_token("aws")
        assert result is None

    def test_env_provider(self):
        token_str = _make_jwt({"sub": "env", "exp": int(time.time()) + 3600})
        with patch.dict(os.environ, {"OIDC_TOKEN": token_str}):
            result = acquire_oidc_token("env")
        assert result == token_str

    def test_env_provider_hf_oidc_token(self):
        token_str = _make_jwt({"sub": "hfoidc", "exp": int(time.time()) + 3600})
        with patch.dict(os.environ, {"HF_OIDC_TOKEN": token_str}):
            result = acquire_oidc_token("env")
        assert result == token_str

    def test_env_provider_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            result = acquire_oidc_token("env")
        assert result is None

    def test_unknown_provider(self):
        result = acquire_oidc_token("unknown_provider")
        assert result is None

    def test_gcp_metadata_fetch(self):
        token_str = _make_jwt({"sub": "gcp", "exp": int(time.time()) + 3600})
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = token_str.encode()
            result = acquire_oidc_token("gcp", client_id="https://huggingface.co")
        assert result == token_str

    def test_azure_metadata_fetch(self):
        token_str = _make_jwt({"sub": "azure", "exp": int(time.time()) + 3600})
        json_resp = json.dumps({"access_token": token_str})
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = json_resp.encode()
            with patch.dict(
                os.environ,
                {
                    "IDENTITY_ENDPOINT": "http://169.254.169.254/metadata/identity/oauth2/token",
                    "IDENTITY_HEADER": "header-value",
                },
            ):
                result = acquire_oidc_token("azure")
        assert result == token_str

    def test_custom_provider(self):
        token_str = _make_jwt({"sub": "custom", "exp": int(time.time()) + 3600})
        json_resp = json.dumps({"token": token_str})
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = json_resp.encode()
            with patch.dict(os.environ, {"HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/token"}):
                result = acquire_oidc_token("custom")
        assert result == token_str


class TestModelDownloaderTokenResolution:
    def test_oidc_overrides_env_token(self):
        oidc_token_str = "oidc-token-123"
        auth = HfOidcAuth(provider="env")
        with patch.dict(os.environ, {"OIDC_TOKEN": oidc_token_str}), tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="env-token", oidc_auth=auth)
            resolved = d._resolve_token()
        assert resolved == oidc_token_str

    def test_falls_back_to_hf_token_when_no_oidc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="explicit-token")
            resolved = d._resolve_token()
        assert resolved == "explicit-token"

    def test_falls_back_to_env_when_no_token_and_no_oidc(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"HF_TOKEN": "env-hf-token"}):
            d = ModelDownloader(cache_dir=tmpdir)
            resolved = d._resolve_token()
        assert resolved == "env-hf-token"

    def test_returns_none_when_no_auth_at_all(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=True):
            d = ModelDownloader(cache_dir=tmpdir)
            assert d.hf_token is None
            resolved = d._resolve_token()
            assert resolved is None

    def test_oidc_revalidates_on_each_call(self):
        first = _make_jwt({"sub": "first", "exp": int(time.time()) + 3600})
        second = _make_jwt({"sub": "second", "exp": int(time.time()) + 7200})
        auth = HfOidcAuth(provider="env")
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"OIDC_TOKEN": first}):
                d = ModelDownloader(cache_dir=tmpdir, oidc_auth=auth)
                r1 = d._resolve_token()
            assert r1 == first
            with patch.dict(os.environ, {"OIDC_TOKEN": second}):
                auth.invalidate()
                r2 = d._resolve_token()
            assert r2 == second

    def test_explicit_token_used_when_oidc_returns_none(self):
        auth = HfOidcAuth()
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="fallback-token", oidc_auth=auth)
            resolved = d._resolve_token()
        assert resolved == "fallback-token"

    def test_oidc_stale_token_fallback(self):
        token_str = _make_jwt({"sub": "exp", "exp": int(time.time()) - 1})
        auth = HfOidcAuth(provider="custom")
        with patch.object(auth, "_acquire") as mock_acquire:
            mock_acquire.return_value = None
            auth._cached = OidcToken(token=token_str, expires_at=time.time() - 1, provider="custom")
            with tempfile.TemporaryDirectory() as tmpdir:
                d = ModelDownloader(cache_dir=tmpdir, oidc_auth=auth)
                resolved = d._resolve_token()
        assert resolved is None  # expired + refresh failed = None

    def test_huggingface_hub_token_env_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"HUGGING_FACE_HUB_TOKEN": "hub-token"}):
            d = ModelDownloader(cache_dir=tmpdir)
            assert d.hf_token == "hub-token"
