"""Deep edge-case tests for Conversation / ConversationMessage — field
validation, serialization round-trips, context-window boundaries,
from_dict error paths, and token estimation precision."""

from __future__ import annotations

import pytest

from general_ludd.review.conversation import Conversation, ConversationMessage

# ── ConversationMessage validation ──────────────────────────────────────


class TestConversationMessageValidation:
    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must not be empty"):
            ConversationMessage(role="", content="hello")

    def test_whitespace_only_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must not be empty"):
            ConversationMessage(role="   ", content="hello")

    def test_empty_content_raises(self) -> None:
        with pytest.raises(ValueError, match="content must not be empty"):
            ConversationMessage(role="user", content="")

    def test_whitespace_only_content_raises(self) -> None:
        with pytest.raises(ValueError, match="content must not be empty"):
            ConversationMessage(role="user", content="   \t ")

    def test_role_with_trailing_whitespace_stripped(self) -> None:
        msg = ConversationMessage(role="  user  ", content="hello")
        assert msg.role == "user"

    def test_content_with_leading_whitespace_preserved(self) -> None:
        msg = ConversationMessage(role="user", content="  hello  ")
        assert msg.content == "  hello  "


# ── _estimate_tokens helper ────────────────────────────────────────────


class TestEstimateTokens:
    def test_single_word_returns_one(self) -> None:
        assert Conversation._estimate_tokens("hello") == 1

    def test_multi_word_returns_word_count(self) -> None:
        assert Conversation._estimate_tokens("the quick brown fox") == 4

    def test_empty_string_returns_one(self) -> None:
        assert Conversation._estimate_tokens("") == 1

    def test_whitespace_only_returns_one(self) -> None:
        assert Conversation._estimate_tokens("      ") == 1

    def test_consecutive_spaces_still_splits_correctly(self) -> None:
        assert Conversation._estimate_tokens("a  b   c") == 3

    def test_newlines_count_as_separators(self) -> None:
        assert Conversation._estimate_tokens("line1\nline2\nline3") == 3


# ── Conversation.from_dict deep paths ───────────────────────────────────


class TestFromDictDeep:
    def test_from_dict_no_messages_key(self) -> None:
        conv = Conversation.from_dict({"todo_id": "T1", "return_id": "R1"})
        assert conv.messages == []
        assert conv.todo_id == "T1"
        assert conv.return_id == "R1"

    def test_from_dict_messages_is_empty_list(self) -> None:
        conv = Conversation.from_dict({"todo_id": "T1", "return_id": "R1", "messages": []})
        assert conv.messages == []

    def test_from_dict_preserves_conversation_id_from_data(self) -> None:
        conv = Conversation.from_dict(
            {
                "conversation_id": "conv-abc12345",
                "todo_id": "T1",
                "return_id": "R1",
            }
        )
        assert conv.conversation_id == "conv-abc12345"

    def test_from_dict_project_id_preserved(self) -> None:
        conv = Conversation.from_dict(
            {
                "todo_id": "T1",
                "return_id": "R1",
                "project_id": "proj-x",
            }
        )
        assert conv.project_id == "proj-x"

    def test_from_dict_created_at_as_iso_string(self) -> None:
        conv = Conversation.from_dict(
            {
                "todo_id": "T1",
                "return_id": "R1",
                "created_at": "2025-01-15T12:00:00+00:00",
            }
        )
        assert conv.created_at.year == 2025


# ── to_dict round-trip with all fields ─────────────────────────────────


class TestToDictFull:
    def test_to_dict_includes_project_id(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1", project_id="proj-x")
        data = conv.to_dict()
        assert data["project_id"] == "proj-x"

    def test_to_dict_includes_created_at_as_string(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        data = conv.to_dict()
        assert isinstance(data["created_at"], str)

    def test_roundtrip_preserves_project_id(self) -> None:
        conv = Conversation(
            todo_id="T1",
            return_id="R1",
            project_id="proj-x",
        )
        conv.add_message("user", "hello")
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.project_id == "proj-x"


# ── get_context boundary conditions ─────────────────────────────────────


class TestGetContextBoundaries:
    def test_max_tokens_zero_returns_empty(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        conv.add_message("user", "hello world")
        context = conv.get_context(max_tokens=0)
        assert context == []

    def test_max_tokens_negative_returns_empty(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        conv.add_message("user", "hello world")
        context = conv.get_context(max_tokens=-1)
        assert context == []

    def test_exact_token_boundary_includes_last_only(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        conv.add_message("user", "one token")
        conv.add_message("assistant", "two token")
        context = conv.get_context(max_tokens=3)
        assert len(context) == 1
        assert context[0].content == "two token"

    def test_token_boundary_excludes_first_when_budget_exceeded(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        conv.add_message("user", "first message here")
        conv.add_message("assistant", "second message")
        conv.add_message("user", "final")
        context = conv.get_context(max_tokens=3)
        assert len(context) >= 1
        assert context[-1].content == "final"

    def test_very_large_max_tokens_returns_all(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        for i in range(5):
            conv.add_message("user", f"msg {i}")
        context = conv.get_context(max_tokens=10**9)
        assert len(context) == 5

    def test_single_message_fits(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        conv.add_message("user", "one two three")
        context = conv.get_context(max_tokens=10)
        assert len(context) == 1
        assert context[0].content == "one two three"

    def test_single_message_too_large(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        conv.add_message("user", "one two three four five")
        context = conv.get_context(max_tokens=2)
        assert context == []


# ── add_message token_count precision ───────────────────────────────────


class TestAddMessageTokenCountPrecision:
    def test_short_message_token_count(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        conv.add_message("user", "hi")
        assert conv.messages[0].token_count == 1
        assert conv.total_tokens() == 1

    def test_token_count_after_many_additions(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        for i in range(100):
            conv.add_message("user", f"message number {i} with content")
        assert conv.message_count() == 100
        assert conv.total_tokens() > 0


# ── Conversation default values ─────────────────────────────────────────


class TestConversationDefaults:
    def test_messages_defaults_to_empty_list(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        assert conv.messages == []

    def test_conversation_id_format(self) -> None:
        conv = Conversation(todo_id="T1", return_id="R1")
        assert conv.conversation_id.startswith("conv-")
        assert len(conv.conversation_id) == 13

    def test_todo_id_empty_by_default(self) -> None:
        conv = Conversation()
        assert conv.todo_id == ""

    def test_return_id_empty_by_default(self) -> None:
        conv = Conversation()
        assert conv.return_id == ""

    def test_project_id_none_by_default(self) -> None:
        conv = Conversation()
        assert conv.project_id is None


# ── ConversationMessage metadata handling ───────────────────────────────


class TestConversationMessageMetadata:
    def test_default_metadata_is_empty_dict(self) -> None:
        msg = ConversationMessage(role="user", content="hello")
        assert msg.metadata == {}

    def test_custom_metadata_preserved(self) -> None:
        msg = ConversationMessage(
            role="user",
            content="hello",
            metadata={"model": "gpt-4", "latency_ms": 120},
        )
        assert msg.metadata["model"] == "gpt-4"
        assert msg.metadata["latency_ms"] == 120

    def test_timestamp_is_monotonic_float(self) -> None:
        msg = ConversationMessage(role="user", content="hello")
        assert isinstance(msg.timestamp, float)
        assert msg.timestamp > 0
