"""Unit tests for OIDC token acquisition module."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, mock_open, patch

from general_ludd.small_models.oidc import acquire_oidc_token


class TestProviderDiscovery:
    def test_aws_routes_to_aws_fetcher(self):
        with patch("general_ludd.small_models.oidc._fetch_aws_oidc_token") as mock_fetch:
            mock_fetch.return_value = "aws-token"
            result = acquire_oidc_token("aws")
            assert result == "aws-token"
            mock_fetch.assert_called_once()

    def test_gcp_routes_to_gcp_fetcher_with_client_id(self):
        with patch("general_ludd.small_models.oidc._fetch_gcp_oidc_token") as mock_fetch:
            mock_fetch.return_value = "gcp-token"
            result = acquire_oidc_token("gcp", client_id="my-audience")
            assert result == "gcp-token"
            mock_fetch.assert_called_once_with("my-audience")

    def test_gcp_routes_to_gcp_fetcher_no_client_id(self):
        with patch("general_ludd.small_models.oidc._fetch_gcp_oidc_token") as mock_fetch:
            mock_fetch.return_value = "gcp-token"
            result = acquire_oidc_token("gcp")
            assert result == "gcp-token"
            mock_fetch.assert_called_once_with(None)

    def test_azure_routes_to_azure_fetcher(self):
        with patch("general_ludd.small_models.oidc._fetch_azure_oidc_token") as mock_fetch:
            mock_fetch.return_value = "azure-token"
            result = acquire_oidc_token("azure", client_id="api://myapp")
            assert result == "azure-token"
            mock_fetch.assert_called_once_with("api://myapp")

    def test_env_routes_to_env_fetcher(self):
        with patch("general_ludd.small_models.oidc._fetch_env_oidc_token") as mock_fetch:
            mock_fetch.return_value = "env-token"
            result = acquire_oidc_token("env")
            assert result == "env-token"
            mock_fetch.assert_called_once()

    def test_custom_routes_to_custom_fetcher(self):
        with patch("general_ludd.small_models.oidc._fetch_custom_oidc_token") as mock_fetch:
            mock_fetch.return_value = "custom-token"
            result = acquire_oidc_token("custom", client_id="test-client")
            assert result == "custom-token"
            mock_fetch.assert_called_once_with("test-client")

    def test_unknown_provider_returns_none(self):
        result = acquire_oidc_token("nonexistent")
        assert result is None

    def test_case_insensitive_provider(self):
        with patch("general_ludd.small_models.oidc._fetch_env_oidc_token") as mock_fetch:
            mock_fetch.return_value = "tok"
            result = acquire_oidc_token("ENV")
            assert result == "tok"
            mock_fetch.assert_called_once()

    def test_whitespace_in_provider_trimmed(self):
        with patch("general_ludd.small_models.oidc._fetch_env_oidc_token") as mock_fetch:
            mock_fetch.return_value = "tok"
            result = acquire_oidc_token("  env  ")
            assert result == "tok"
            mock_fetch.assert_called_once()


class TestEnvToken:
    def test_reads_hf_oidc_token_env_var(self):
        with patch.dict(os.environ, {"HF_OIDC_TOKEN": "hf-token-value"}, clear=True):
            from general_ludd.small_models.oidc import _fetch_env_oidc_token

            result = _fetch_env_oidc_token()
            assert result == "hf-token-value"

    def test_reads_oidc_token_env_var_fallback(self):
        with patch.dict(os.environ, {"OIDC_TOKEN": "fallback-token"}, clear=True):
            from general_ludd.small_models.oidc import _fetch_env_oidc_token

            result = _fetch_env_oidc_token()
            assert result == "fallback-token"

    def test_prefers_hf_oidc_token_over_oidc_token(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_TOKEN": "primary",
                "OIDC_TOKEN": "fallback",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_env_oidc_token

            result = _fetch_env_oidc_token()
            assert result == "primary"

    def test_returns_none_when_no_env_vars_set(self):
        with patch.dict(os.environ, {}, clear=True):
            from general_ludd.small_models.oidc import _fetch_env_oidc_token

            result = _fetch_env_oidc_token()
            assert result is None


class TestAwsWebIdentityFile:
    def test_reads_token_from_web_identity_file(self):
        with patch.dict(
            os.environ,
            {
                "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/fake-aws-token",
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            m = mock_open(read_data="web-identity-jwt")
            with patch("builtins.open", m):
                result = _fetch_aws_oidc_token()
                assert result == "web-identity-jwt"

    def test_returns_none_when_web_identity_file_empty(self):
        with patch.dict(
            os.environ,
            {
                "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/empty-aws-token",
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            m = mock_open(read_data="")
            with patch("builtins.open", m):
                result = _fetch_aws_oidc_token()
                assert result is None

    def test_returns_none_when_no_credential_source(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "",
                "AWS_WEB_IDENTITY_TOKEN_FILE": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            result = _fetch_aws_oidc_token()
            assert result is None

    def test_handles_file_read_error_gracefully(self):
        with patch.dict(
            os.environ,
            {
                "AWS_WEB_IDENTITY_TOKEN_FILE": "/nonexistent/token.file",
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            result = _fetch_aws_oidc_token()
            assert result is None


class TestAwsContainerCredentials:
    def test_fetches_container_token(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/abc",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            mock_cm = _urlopen_mock(json_body={"Token": "ecs-jwt-token"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_aws_oidc_token()
                assert result == "ecs-jwt-token"

    def test_falls_back_to_access_key_id(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/def",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            mock_cm = _urlopen_mock(json_body={"AccessKeyId": "AKIA12345"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_aws_oidc_token()
                assert result == "AKIA12345"

    def test_prefers_token_over_access_key_id(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/ghi",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            mock_cm = _urlopen_mock(json_body={"Token": "jwt-first", "AccessKeyId": "AKIA-secondary"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_aws_oidc_token()
                assert result == "jwt-first"

    def test_returns_none_when_container_creds_missing_both_fields(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/jkl",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            mock_cm = _urlopen_mock(json_body={"OtherField": "ignored"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_aws_oidc_token()
                assert result is None

    def test_handles_http_error_gracefully(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/err",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
                result = _fetch_aws_oidc_token()
                assert result is None


class TestGcpMetadata:
    def test_fetches_gcp_identity_token(self):
        from general_ludd.small_models.oidc import _fetch_gcp_oidc_token

        mock_cm = _urlopen_mock(raw_body="gcp-id-token-jwt")
        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = _fetch_gcp_oidc_token(client_id="https://custom.aud")
            assert result == "gcp-id-token-jwt"

    def test_default_audience_when_no_client_id(self):
        from general_ludd.small_models.oidc import _fetch_gcp_oidc_token

        mock_cm = _urlopen_mock(raw_body="gcp-default-token")
        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = _fetch_gcp_oidc_token()
            assert result == "gcp-default-token"

    def test_returns_none_when_response_empty(self):
        from general_ludd.small_models.oidc import _fetch_gcp_oidc_token

        mock_cm = _urlopen_mock(raw_body="")
        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = _fetch_gcp_oidc_token()
            assert result is None

    def test_handles_http_error_gracefully(self):
        from general_ludd.small_models.oidc import _fetch_gcp_oidc_token

        with patch("urllib.request.urlopen", side_effect=OSError("metadata unreachable")):
            result = _fetch_gcp_oidc_token()
            assert result is None


class TestAzureIMDS:
    def test_fetches_azure_access_token(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "http://169.254.169.254/metadata/identity/oauth2/token",
                "IDENTITY_HEADER": "secret123",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            mock_cm = _urlopen_mock(json_body={"access_token": "azure-jwt-access-token"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_azure_oidc_token()
                assert result == "azure-jwt-access-token"

    def test_falls_back_to_token_field(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "http://169.254.169.254/metadata/identity/oauth2/token",
                "IDENTITY_HEADER": "header456",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            mock_cm = _urlopen_mock(json_body={"token": "azure-token-fallback"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_azure_oidc_token()
                assert result == "azure-token-fallback"

    def test_prefers_access_token_over_token(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "http://169.254.169.254/metadata/identity/oauth2/token",
                "IDENTITY_HEADER": "hdr789",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            mock_cm = _urlopen_mock(json_body={"access_token": "primary-token", "token": "fallback-token"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_azure_oidc_token()
                assert result == "primary-token"

    def test_passes_client_id_as_resource(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "http://169.254.169.254/metadata/identity/oauth2/token",
                "IDENTITY_HEADER": "hdr",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            mock_cm = _urlopen_mock(json_body={"access_token": "tok"})
            with patch("urllib.request.urlopen", return_value=mock_cm):

                result = _fetch_azure_oidc_token(client_id="https://custom.api")
                assert result == "tok"

    def test_returns_none_when_identity_endpoint_not_set(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "",
                "IDENTITY_HEADER": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            result = _fetch_azure_oidc_token()
            assert result is None

    def test_returns_none_when_no_token_in_response(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "http://169.254.169.254/metadata",
                "IDENTITY_HEADER": "hdr",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            mock_cm = _urlopen_mock(json_body={"error": "unauthorized"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_azure_oidc_token()
                assert result is None

    def test_handles_http_error_gracefully(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "http://bad.endpoint/",
                "IDENTITY_HEADER": "hdr",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            with patch("urllib.request.urlopen", side_effect=OSError("imds unreachable")):
                result = _fetch_azure_oidc_token()
                assert result is None


class TestCustomOidcEndpoint:
    def test_fetches_token_from_custom_endpoint(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"token": "custom-jwt"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result == "custom-jwt"

    def test_falls_back_to_access_token_field(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/v2/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"access_token": "access-jwt"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result == "access-jwt"

    def test_second_fallback_to_id_token(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/v3/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"id_token": "id-jwt"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result == "id-jwt"

    def test_prefers_token_over_access_and_id(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/all",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(
                json_body={
                    "token": "first",
                    "access_token": "second",
                    "id_token": "third",
                }
            )
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result == "first"

    def test_passes_client_id_header(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"token": "tok"})
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value = mock_cm
                result = _fetch_custom_oidc_token(client_id="my-client-123")
                assert result == "tok"
                call_req = mock_urlopen.call_args[0][0]
                assert call_req.headers.get("X-client-id") == "my-client-123"

    def test_no_client_id_header_when_none(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"token": "tok"})
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value = mock_cm
                result = _fetch_custom_oidc_token()
                assert result == "tok"
                call_req = mock_urlopen.call_args[0][0]
                assert call_req.headers.get("X-Client-ID") is None

    def test_returns_none_when_endpoint_env_not_set(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            result = _fetch_custom_oidc_token()
            assert result is None

    def test_returns_none_when_no_token_in_response(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"error": "bad request"})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result is None

    def test_handles_http_error_gracefully(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://unreachable.example.com/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            with patch("urllib.request.urlopen", side_effect=OSError("endpoint down")):
                result = _fetch_custom_oidc_token()
                assert result is None


class TestTokenExchange:
    def test_aws_returns_token_string(self):
        with patch.dict(
            os.environ,
            {
                "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/exchange-token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import acquire_oidc_token

            m = mock_open(read_data="exchanged-jwt-token")
            with patch("builtins.open", m):
                result = acquire_oidc_token("aws")
                assert result == "exchanged-jwt-token"

    def test_env_returns_token_string(self):
        with patch.dict(os.environ, {"HF_OIDC_TOKEN": "direct-env-token"}, clear=True):
            from general_ludd.small_models.oidc import acquire_oidc_token

            result = acquire_oidc_token("env")
            assert result == "direct-env-token"


class TestExpiredEmptyTokens:
    def test_aws_container_empty_token_string_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/empty",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            mock_cm = _urlopen_mock(json_body={"Token": ""})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_aws_oidc_token()
                assert result is None

    def test_gcp_empty_response_returns_none(self):
        from general_ludd.small_models.oidc import _fetch_gcp_oidc_token

        mock_cm = _urlopen_mock(raw_body="")
        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = _fetch_gcp_oidc_token()
            assert result is None

    def test_custom_empty_token_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/token",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"token": ""})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result is None

    def test_env_empty_token_returns_empty_string(self):
        with patch.dict(os.environ, {"OIDC_TOKEN": ""}, clear=True):
            from general_ludd.small_models.oidc import _fetch_env_oidc_token

            result = _fetch_env_oidc_token()
            assert result == ""


class TestInvalidIssuer:
    def test_unknown_provider_returns_none_like_invalid_issuer(self):
        result = acquire_oidc_token("unknown-cloud")
        assert result is None

    def test_azure_missing_endpoint_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "",
                "IDENTITY_HEADER": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            result = _fetch_azure_oidc_token()
            assert result is None

    def test_custom_missing_endpoint_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            result = _fetch_custom_oidc_token()
            assert result is None


class TestErrorHandling:
    def test_aws_container_bad_json_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/bad",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            mock_cm = _urlopen_mock(raw_body="not-valid-json{{{")
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_aws_oidc_token()
                assert result is None

    def test_azure_bad_json_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "IDENTITY_ENDPOINT": "http://169.254.169.254/metadata",
                "IDENTITY_HEADER": "hdr",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_azure_oidc_token

            mock_cm = _urlopen_mock(raw_body="corrupt-json")
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_azure_oidc_token()
                assert result is None

    def test_custom_bad_json_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://bad.example.com/",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(raw_body="<html>not json</html>")
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result is None

    def test_aws_container_non_string_token_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/num",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_aws_oidc_token

            mock_cm = _urlopen_mock(json_body={"Token": 12345})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_aws_oidc_token()
                assert result is None

    def test_custom_non_string_token_returns_none(self):
        with patch.dict(
            os.environ,
            {
                "HF_OIDC_CUSTOM_ENDPOINT": "https://oidc.example.com/",
            },
            clear=True,
        ):
            from general_ludd.small_models.oidc import _fetch_custom_oidc_token

            mock_cm = _urlopen_mock(json_body={"token": 999})
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = _fetch_custom_oidc_token()
                assert result is None


def _urlopen_mock(json_body=None, raw_body=None):
    """Build a MagicMock that properly chains urlopen → __enter__ → read → decode → bytes/str."""
    if json_body is not None:
        raw = json.dumps(json_body).encode()
    elif raw_body is not None:
        raw = raw_body.encode() if isinstance(raw_body, str) else raw_body
    else:
        raw = b"{}"

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_resp
    return mock_cm
