"""
Unit tests for capability_router module_utils.

Tests the dispatch, list_capabilities, and register_capability functions
against mocked daemon HTTP responses.  No real daemon required.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(
    0,
    "collections/ansible_collections/general_ludd/agent/plugins/module_utils",
)
from capability_router import (  # type: ignore[import]
    CapabilityDispatchError,
    clear_registry,
    dispatch,
    get_registry,
    list_capabilities,
    register_capability,
)

DAEMON_URL = "http://localhost:8000"
FAKE_PSK = "test-psk-123"


class TestDispatch:
    """dispatch(capability, payload) -- routing through daemon."""

    def test_dispatches_capability_to_daemon(self):
        """dispatch POSTs to /api/dispatch with kind=collection."""
        from capability_router import _url

        mock_response = {
            "results": [{"ok": True, "capability": "agentic"}],
            "count": 1,
            "ok_count": 1,
            "error_count": 0,
        }

        with patch("capability_router._send", return_value={"_status": 200, **mock_response}) as mock_send:
            result = dispatch(
                "agentic",
                {"task": "build"},
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
            )

        mock_send.assert_called_once()
        call_args, call_kwargs = mock_send.call_args
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["psk"] == FAKE_PSK
        body = call_kwargs["body"]
        assert body["kind"] == "collection"
        assert body["name"] == "agentic"
        assert body["args"]["payload"] == {"task": "build"}
        assert result["ok_count"] == 1

    def test_dispatch_default_payload_empty_dict(self):
        """dispatch with None payload defaults to empty dict."""
        mock_response = {
            "results": [],
            "count": 0,
            "ok_count": 0,
            "error_count": 0,
        }
        with patch("capability_router._send", return_value={"_status": 200, **mock_response}) as mock_send:
            dispatch("tag", None, daemon_url=DAEMON_URL, psk=FAKE_PSK)

        body = mock_send.call_args.kwargs["body"]
        assert body["args"]["payload"] == {}

    def test_dispatch_empty_capability_raises(self):
        """dispatch with empty string raises CapabilityDispatchError."""
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            dispatch("", {}, daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_dispatch_none_capability_raises(self):
        """dispatch with None capability raises CapabilityDispatchError."""
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            dispatch(None, {}, daemon_url=DAEMON_URL, psk=FAKE_PSK)  # type: ignore[arg-type]

    def test_dispatch_daemon_unreachable_raises(self):
        """dispatch with URLError raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_error": "connection refused", "_status": 0},
        ):
            with pytest.raises(CapabilityDispatchError, match="daemon unreachable"):
                dispatch("tag", {}, daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_dispatch_unauthorized_raises(self):
        """dispatch with 401 raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_error": "", "_status": 401},
        ):
            with pytest.raises(CapabilityDispatchError, match="unauthorized"):
                dispatch("tag", {}, daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_dispatch_http_error_raises(self):
        """dispatch with 422 raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_status": 422, "detail": "bad request"},
        ):
            with pytest.raises(CapabilityDispatchError, match="failed.*bad request"):
                dispatch("tag", {}, daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_dispatch_forwards_model_profile(self):
        """dispatch includes model_profile in body when provided."""
        mock_response = {
            "results": [],
            "count": 0,
            "ok_count": 0,
            "error_count": 0,
        }
        with patch("capability_router._send", return_value={"_status": 200, **mock_response}) as mock_send:
            dispatch(
                "tag",
                {},
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
                model_profile="gpt-4",
            )

        body = mock_send.call_args.kwargs["body"]
        assert body["model_profile"] == "gpt-4"

    def test_dispatch_forwards_role(self):
        """dispatch includes role in body when provided."""
        mock_response = {
            "results": [],
            "count": 0,
            "ok_count": 0,
            "error_count": 0,
        }
        with patch("capability_router._send", return_value={"_status": 200, **mock_response}) as mock_send:
            dispatch(
                "tag",
                {},
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
                role="coder",
            )

        body = mock_send.call_args.kwargs["body"]
        assert body["role"] == "coder"


class TestListCapabilities:
    """list_capabilities() -- discover capabilities from daemon and registry."""

    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_lists_from_available_endpoint(self):
        """GET /api/dispatch/available and extracts registered_kinds."""
        mock_resp = {
            "_status": 200,
            "registered_kinds": [
                {"name": "agentic", "kind": "collection"},
                {"name": "mcp", "kind": "mcp"},
            ],
        }
        with patch("capability_router._send", return_value=mock_resp):
            caps = list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert "agentic" in caps
        assert "mcp" in caps

    def test_lists_from_handlers_field(self):
        """Uses handlers list when registered_kinds is absent."""
        mock_resp = {
            "_status": 200,
            "handlers": ["agentic", "planner", "tool_call"],
        }
        with patch("capability_router._send", return_value=mock_resp):
            caps = list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert "agentic" in caps
        assert "planner" in caps
        assert "tool_call" in caps

    def test_includes_local_registry_entries(self):
        """Local registry entries are merged with daemon results."""
        with patch("capability_router._send", return_value={"_status": 200}):
            register_capability(
                "local_only",
                roles=["coder"],
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
            )

        mock_resp = {"_status": 200, "registered_kinds": [{"name": "daemon_cap"}]}
        with patch("capability_router._send", return_value=mock_resp):
            caps = list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert "local_only" in caps
        assert "daemon_cap" in caps

    def test_deduplicates_across_sources(self):
        """Same capability in registry and daemon appears once."""
        with patch("capability_router._send", return_value={"_status": 200}):
            register_capability(
                "shared_cap",
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
            )

        mock_resp = {
            "_status": 200,
            "registered_kinds": [{"name": "shared_cap"}],
        }
        with patch("capability_router._send", return_value=mock_resp):
            caps = list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert caps.count("shared_cap") == 1

    def test_daemon_unreachable_raises(self):
        """URLError raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_error": "timed out", "_status": 0},
        ):
            with pytest.raises(CapabilityDispatchError, match="daemon unreachable"):
                list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_unauthorized_raises(self):
        """401 raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_status": 401},
        ):
            with pytest.raises(CapabilityDispatchError, match="unauthorized"):
                list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_http_error_raises(self):
        """Non-200 HTTP status raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_status": 500, "detail": "internal error"},
        ):
            with pytest.raises(CapabilityDispatchError, match="failed.*internal error"):
                list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_returns_sorted_list(self):
        """Result is sorted alphabetically."""
        mock_resp = {
            "_status": 200,
            "registered_kinds": [
                {"name": "zulu"},
                {"name": "alpha"},
            ],
        }
        with patch("capability_router._send", return_value=mock_resp):
            caps = list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert caps == sorted(caps)
        assert caps == ["alpha", "zulu"]

    def test_handles_dict_handlers_with_name_key(self):
        """Handlers as dicts with name key are parsed."""
        mock_resp = {
            "_status": 200,
            "handlers": [{"name": "cap_a"}, {"name": "cap_b"}],
        }
        with patch("capability_router._send", return_value=mock_resp):
            caps = list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert "cap_a" in caps
        assert "cap_b" in caps

    def test_handles_empty_daemon_response(self):
        """Empty daemon response returns empty list."""
        mock_resp = {"_status": 200}
        with patch("capability_router._send", return_value=mock_resp):
            caps = list_capabilities(daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert caps == []


class TestRegisterCapability:
    """register_capability(name, roles, model_needs) -- registration."""

    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_registers_locally_and_dispatches(self):
        """register_capability stores in process registry and POSTs to daemon."""
        mock_resp = {"_status": 200}
        with patch("capability_router._send", return_value=mock_resp) as mock_send:
            result = register_capability(
                "my_cap",
                roles=["coder", "operator"],
                model_needs={"min_tokens": 1024},
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
            )

        assert result["name"] == "my_cap"
        assert result["roles"] == ["coder", "operator"]
        assert result["model_needs"] == {"min_tokens": 1024}
        assert result["registered"] is True

        registry = get_registry()
        assert "my_cap" in registry
        assert registry["my_cap"]["roles"] == ["coder", "operator"]

        body = mock_send.call_args.kwargs["body"]
        assert body["kind"] == "collection"
        assert body["name"] == "register_capability"
        assert body["args"]["capability_name"] == "my_cap"

    def test_default_args(self):
        """register_capability defaults roles and model_needs to empty."""
        mock_resp = {"_status": 200}
        with patch("capability_router._send", return_value=mock_resp):
            result = register_capability(
                "bare_cap",
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
            )

        assert result["roles"] == []
        assert result["model_needs"] == {}

    def test_empty_name_raises(self):
        """Empty capability name raises CapabilityDispatchError."""
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            register_capability("", daemon_url=DAEMON_URL, psk=FAKE_PSK)

    def test_none_name_raises(self):
        """None capability name raises CapabilityDispatchError."""
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            register_capability(None, daemon_url=DAEMON_URL, psk=FAKE_PSK)  # type: ignore[arg-type]

    def test_daemon_unreachable_raises(self):
        """URLError during registration raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_error": "connection refused", "_status": 0},
        ):
            with pytest.raises(CapabilityDispatchError, match="daemon unreachable"):
                register_capability(
                    "cap",
                    daemon_url=DAEMON_URL,
                    psk=FAKE_PSK,
                )

    def test_unauthorized_raises(self):
        """401 during registration raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_status": 401},
        ):
            with pytest.raises(CapabilityDispatchError, match="unauthorized"):
                register_capability(
                    "cap",
                    daemon_url=DAEMON_URL,
                    psk=FAKE_PSK,
                )

    def test_http_error_raises(self):
        """Non-200/201 during registration raises CapabilityDispatchError."""
        with patch(
            "capability_router._send",
            return_value={"_status": 422, "detail": "duplicate capability"},
        ):
            with pytest.raises(CapabilityDispatchError, match="duplicate capability"):
                register_capability(
                    "cap",
                    daemon_url=DAEMON_URL,
                    psk=FAKE_PSK,
                )

    def test_multiple_registrations_accumulate(self):
        """Multiple register calls store all in the registry."""
        mock_resp = {"_status": 200}
        with patch("capability_router._send", return_value=mock_resp):
            register_capability("a", daemon_url=DAEMON_URL, psk=FAKE_PSK)
            register_capability("b", daemon_url=DAEMON_URL, psk=FAKE_PSK)

        registry = get_registry()
        assert "a" in registry
        assert "b" in registry

    def test_reregister_updates_existing(self):
        """Re-registering the same name updates the entry."""
        mock_resp = {"_status": 200}
        with patch("capability_router._send", return_value=mock_resp):
            register_capability(
                "cap",
                roles=["coder"],
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
            )
            register_capability(
                "cap",
                roles=["operator"],
                daemon_url=DAEMON_URL,
                psk=FAKE_PSK,
            )

        registry = get_registry()
        assert len(registry) == 1
        assert registry["cap"]["roles"] == ["operator"]


class TestRegistryManagement:
    """get_registry() and clear_registry() -- process-local state."""

    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_get_registry_returns_copy(self):
        """get_registry returns a distinct dict, not a reference."""
        mock_resp = {"_status": 200}
        with patch("capability_router._send", return_value=mock_resp):
            register_capability("a", daemon_url=DAEMON_URL, psk=FAKE_PSK)

        r1 = get_registry()
        r2 = get_registry()
        assert r1 is not r2
        assert r1 == r2

    def test_clear_registry_empties_state(self):
        """clear_registry removes all entries."""
        mock_resp = {"_status": 200}
        with patch("capability_router._send", return_value=mock_resp):
            register_capability("a", daemon_url=DAEMON_URL, psk=FAKE_PSK)

        assert len(get_registry()) == 1
        clear_registry()
        assert len(get_registry()) == 0


class TestCapabilityDispatchError:
    """CapabilityDispatchError -- exception class behaviour."""

    def test_is_exception(self):
        """CapabilityDispatchError is an Exception subclass."""
        assert issubclass(CapabilityDispatchError, Exception)

    def test_message_preserved(self):
        """The message passed is accessible via str()."""
        exc = CapabilityDispatchError("test message")
        assert str(exc) == "test message"

    def test_can_be_caught_as_exception(self):
        """CapabilityDispatchError can be caught via except Exception."""
        with pytest.raises(Exception):
            raise CapabilityDispatchError("caught")


class TestSendHelper:
    """_send() -- the internal HTTP transport."""

    def test_send_get_success(self):
        """_send GET returns parsed JSON with _status."""
        from capability_router import _send

        with patch("capability_router.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"ok": true}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = _send(
                "http://localhost:8000/api/test",
                method="GET",
                psk=FAKE_PSK,
            )

        assert result["_status"] == 200
        assert result["ok"] is True

    def test_send_post_sends_body(self):
        """_send POST includes the body as JSON."""
        from capability_router import _send

        with patch("capability_router.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 201
            mock_resp.read.return_value = b'{"created": true}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = _send(
                "http://localhost:8000/api/test",
                method="POST",
                body={"key": "value"},
                psk=FAKE_PSK,
            )

        assert result["_status"] == 201
        assert result["created"] is True

    def test_send_http_error_captures_body(self):
        """_send captures error body on HTTPError."""
        from capability_router import _send

        import urllib.error

        with patch(
            "capability_router.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://localhost:8000",
                422,
                "Unprocessable",
                {},
                MagicMock(read=MagicMock(return_value=b'{"detail": "invalid payload"}')),
            ),
        ):
            result = _send(
                "http://localhost:8000/api/test",
                method="POST",
                psk=FAKE_PSK,
            )

        assert result["_status"] == 422
        assert result["detail"] == "invalid payload"

    def test_send_urllib_error_captures_reason(self):
        """_send captures URLError reason."""
        from capability_router import _send

        import urllib.error

        with patch(
            "capability_router.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timed out"),
        ):
            result = _send(
                "http://localhost:8000/api/test",
                method="GET",
                psk=FAKE_PSK,
            )

        assert result["_status"] == 0
        assert result["_error"] == "timed out"

    def test_send_no_psk_no_auth_header(self):
        """_send with empty PSK omits Authorization header."""
        from capability_router import _send

        with patch("capability_router.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b"{}"
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            _send("http://localhost:8000/api/test", method="GET", psk="")

        request_arg = mock_urlopen.call_args[0][0]
        assert "Authorization" not in request_arg.headers
        assert "X-PSK" not in request_arg.headers

    def test_send_invalid_json_returns_raw(self):
        """_send with invalid JSON response stores in _raw."""
        from capability_router import _send

        with patch("capability_router.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b"not json"
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = _send(
                "http://localhost:8000/api/test",
                method="GET",
                psk=FAKE_PSK,
            )

        assert result["_status"] == 200
        assert result["_raw"] == "not json"


class TestUrlHelper:
    """_url() -- path joining."""

    def test_joins_base_and_path(self):
        from capability_router import _url

        assert _url("http://localhost:8000", "/api/dispatch") == ("http://localhost:8000/api/dispatch")

    def test_trailing_slash_on_base_normalized(self):
        from capability_router import _url

        assert _url("http://localhost:8000/", "/api/dispatch") == ("http://localhost:8000/api/dispatch")

    def test_no_leading_slash_on_path_normalized(self):
        from capability_router import _url

        assert _url("http://localhost:8000", "api/dispatch") == ("http://localhost:8000/api/dispatch")


class TestDefaults:
    """Default values are correct."""

    def test_default_daemon_url(self):
        from capability_router import DEFAULT_DAEMON_URL

        assert DEFAULT_DAEMON_URL == "http://localhost:8000"

    def test_default_timeout(self):
        from capability_router import DEFAULT_TIMEOUT

        assert DEFAULT_TIMEOUT == 30

    def test_default_endpoints(self):
        from capability_router import DISPATCH_ENDPOINT, DISPATCH_AVAILABLE_ENDPOINT

        assert DISPATCH_ENDPOINT == "/api/dispatch"
        assert DISPATCH_AVAILABLE_ENDPOINT == "/api/dispatch/available"
