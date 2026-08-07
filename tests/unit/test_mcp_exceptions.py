"""Tests for mcp/exceptions.py."""

from general_ludd.mcp.exceptions import MCPTransportError


def test_mcp_transport_error_is_exception():
    assert issubclass(MCPTransportError, Exception)


def test_mcp_transport_error_can_raise_and_catch():
    msg = "test error message"
    try:
        raise MCPTransportError(msg)
    except MCPTransportError as e:
        assert str(e) == msg


def test_mcp_transport_error_can_be_caught_as_exception():
    try:
        raise MCPTransportError("err")
    except Exception:
        pass
