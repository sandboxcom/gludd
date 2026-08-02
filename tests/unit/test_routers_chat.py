"""Structural tests for routers/chat.py."""

from general_ludd.routers.chat import _SessionSearchRequest, _ValidateRequest


class TestChatRouter:
    def test_imports(self):
        pass

    def test_session_search_request(self):
        req = _SessionSearchRequest(query="test query", limit=10)
        assert req.query == "test query"
        assert req.limit == 10

    def test_session_search_request_defaults(self):
        req = _SessionSearchRequest(query="defaults")
        assert req.limit == 20

    def test_validate_request(self):
        req = _ValidateRequest(role="user", content="Hello")
        assert req.role == "user"
        assert req.content == "Hello"
        assert req.timestamp is None
        assert req.model is None

    def test_validate_request_full(self):
        req = _ValidateRequest(
            role="assistant",
            content="Hi there",
            timestamp="2024-01-01T00:00:00Z",
            model="gpt-4",
        )
        assert req.role == "assistant"
        assert req.timestamp == "2024-01-01T00:00:00Z"
        assert req.model == "gpt-4"
