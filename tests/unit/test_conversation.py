from __future__ import annotations

from agentic_harness.review.conversation import Conversation, ConversationMessage


class TestConversationMessageCreate:
    def test_message_with_required_fields(self):
        msg = ConversationMessage(role="user", content="Hello world")
        assert msg.role == "user"
        assert msg.content == "Hello world"
        assert msg.token_count == 0
        assert isinstance(msg.timestamp, float)
        assert msg.metadata == {}

    def test_message_with_all_fields(self):
        msg = ConversationMessage(
            role="assistant",
            content="Response text",
            token_count=42,
            metadata={"model": "gpt-4"},
        )
        assert msg.role == "assistant"
        assert msg.content == "Response text"
        assert msg.token_count == 42
        assert msg.metadata == {"model": "gpt-4"}

    def test_message_roles(self):
        for role in ("system", "user", "assistant", "tool"):
            msg = ConversationMessage(role=role, content="test")
            assert msg.role == role


class TestConversationCreate:
    def test_conversation_with_ids(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        assert conv.todo_id == "TODO-001"
        assert conv.return_id == "RET-001"
        assert conv.messages == []
        assert conv.conversation_id.startswith("conv-")

    def test_conversation_generates_unique_id(self):
        c1 = Conversation(todo_id="TODO-001", return_id="RET-001")
        c2 = Conversation(todo_id="TODO-002", return_id="RET-002")
        assert c1.conversation_id != c2.conversation_id


class TestConversationAddMessage:
    def test_add_message_appends(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("user", "Hello")
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello"

    def test_add_multiple_messages(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("system", "You are a helper")
        conv.add_message("user", "Do something")
        conv.add_message("assistant", "Done")
        assert len(conv.messages) == 3
        assert conv.messages[0].role == "system"
        assert conv.messages[1].role == "user"
        assert conv.messages[2].role == "assistant"

    def test_add_message_estimates_token_count(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("user", "This is a message with some words in it")
        assert conv.messages[0].token_count > 0


class TestConversationGetContextWindow:
    def test_get_context_returns_all_within_budget(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("user", "short msg")
        conv.add_message("assistant", "ok")
        context = conv.get_context(max_tokens=100000)
        assert len(context) == 2

    def test_get_context_truncates_to_budget(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        for i in range(20):
            conv.add_message("user", f"Message number {i} with some extra words to consume tokens")
        conv.add_message("user", "final message")
        context = conv.get_context(max_tokens=50)
        assert len(context) < 20
        assert context[-1].content == "final message"

    def test_get_context_empty_conversation(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        context = conv.get_context(max_tokens=1000)
        assert context == []


class TestConversationToDictRoundtrip:
    def test_roundtrip_preserves_all_data(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("system", "System prompt")
        conv.add_message("user", "User query")
        conv.add_message("assistant", "Assistant response")

        data = conv.to_dict()
        restored = Conversation.from_dict(data)

        assert restored.conversation_id == conv.conversation_id
        assert restored.todo_id == conv.todo_id
        assert restored.return_id == conv.return_id
        assert len(restored.messages) == 3
        assert restored.messages[0].content == "System prompt"
        assert restored.messages[1].content == "User query"
        assert restored.messages[2].content == "Assistant response"

    def test_roundtrip_preserves_empty_conversation(self):
        conv = Conversation(todo_id="TODO-005", return_id="RET-005")
        data = conv.to_dict()
        restored = Conversation.from_dict(data)
        assert restored.messages == []
        assert restored.todo_id == "TODO-005"


class TestConversationSummaryStats:
    def test_message_count(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        assert conv.message_count() == 0
        conv.add_message("user", "hi")
        conv.add_message("assistant", "hello")
        assert conv.message_count() == 2

    def test_total_tokens(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("user", "short message")
        conv.add_message("assistant", "response text here")
        total = conv.total_tokens()
        assert total > 0
        assert total == sum(m.token_count for m in conv.messages)

    def test_empty_conversation_stats(self):
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        assert conv.message_count() == 0
        assert conv.total_tokens() == 0
