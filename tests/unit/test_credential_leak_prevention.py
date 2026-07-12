"""H-GATEWAY-EXC-CREDLEAK: credential redaction in exception messages."""


from general_ludd.security.sanitize import sanitize_error_message


class TestSanitizeErrorMessage:
    def test_no_credentials_passes_through(self):
        msg = "Connection timed out after 30 seconds"
        result = sanitize_error_message(msg)
        assert result == msg

    def test_redacts_api_key_in_message(self):
        msg = "Error: api_key=sk-abc123def456ghi789jkl"  # pragma: allowlist secret
        result = sanitize_error_message(msg)
        assert "sk-abc123def456ghi789jkl" not in result  # pragma: allowlist secret
        assert "api_key" in result.lower()

    def test_redacts_bearer_token_in_message(self):
        msg = "401 Unauthorized: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = sanitize_error_message(msg)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED_BEARER_TOKEN]" in result

    def test_redacts_basic_auth_in_message(self):
        msg = "auth: Basic dXNlcjpwYXNzd29yZA=="
        result = sanitize_error_message(msg)
        assert "dXNlcjpwYXNzd29yZA==" not in result
        assert "[REDACTED_BASIC_AUTH]" in result

    def test_redacts_url_with_embedded_credentials(self):
        msg = "Connection refused to https://admin:secret123@api.example.com/v1/chat"  # pragma: allowlist secret
        result = sanitize_error_message(msg)
        assert "admin:secret123@" not in result
        assert "admin" not in result or "[REDACTED_CREDS_IN_URL]@" in result

    def test_redacts_openai_style_key(self):
        msg = "Invalid API key: sk-proj-abcdef1234567890ghijklmnop"  # pragma: allowlist secret
        result = sanitize_error_message(msg)
        assert "sk-proj-abcdef1234567890ghijklmnop" not in result  # pragma: allowlist secret
        assert "[REDACTED_OPENAI_KEY]" in result

    def test_redacts_x_api_key_header(self):
        msg = "Missing required header x-api-key: abc123secret456"
        result = sanitize_error_message(msg)
        assert "abc123secret456" not in result
        assert "[REDACTED_X_API_KEY]" in result

    def test_redacts_password_in_message(self):
        msg = "Authentication failed with password: SuperSecret123!"
        result = sanitize_error_message(msg)
        assert "SuperSecret123!" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_empty_string_passes_through(self):
        assert sanitize_error_message("") == ""

    def test_no_redaction_on_valid_url(self):
        msg = "Failed to connect to https://api.example.com:443"
        result = sanitize_error_message(msg)
        assert "api.example.com" in result
        assert "REDACTED" not in result

    def test_multiple_credentials_redacted(self):
        msg = (
            "api_key=abc123 and Authorization: Bearer token456 "
            "and x-api-key: secret789"
        )
        result = sanitize_error_message(msg)
        assert "abc123" not in result
        assert "token456" not in result
        assert "secret789" not in result
