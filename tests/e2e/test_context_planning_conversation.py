"""E2E: Context compaction, plan artifacts, and conversation persistence.

Tests real integration between Todo → PlanArtifact, messages → compaction
→ reduced context, and conversation add/get/roundtrip workflows.
"""

from __future__ import annotations

import re

from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.planning.artifact import PlanArtifact
from general_ludd.review.conversation import Conversation
from general_ludd.schemas.todo import Todo


def _make_messages(count: int, tokens_per_msg: int = 100, role: str = "user") -> list[ContextMessage]:
    messages: list[ContextMessage] = []
    for i in range(count):
        text = f"Message {i}: " + "x" * (tokens_per_msg * 4)
        messages.append(
            ContextMessage(
                role=role,
                content=text,
                token_estimate=tokens_per_msg,
                is_system=False,
            )
        )
    return messages


class TestContextCompactionE2E:
    def test_context_compactor_real_messages(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.5, preserve_recent_count=4)
        messages = _make_messages(20, tokens_per_msg=100)
        input_tokens = sum(m.token_estimate for m in messages)
        result = compactor.compact(messages)
        output_tokens = sum(m.token_estimate for m in result)
        assert output_tokens < input_tokens

    def test_context_compactor_preserves_system(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.3, preserve_recent_count=4)
        system_msg = ContextMessage(
            role="system",
            content="Critical system prompt that must survive",
            token_estimate=8,
            is_system=True,
        )
        messages = [system_msg, *_make_messages(20, tokens_per_msg=100)]
        result = compactor.compact(messages)
        assert result[0] == system_msg
        assert any(m.content == "Critical system prompt that must survive" for m in result)

    def test_context_compactor_keeps_recent(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.3, preserve_recent_count=4)
        messages = _make_messages(20, tokens_per_msg=100)
        result = compactor.compact(messages)
        non_system = [m for m in result if not m.is_system]
        assert len(non_system) >= 4
        for orig, kept in zip(messages[-4:], non_system[-4:], strict=True):
            assert kept.content == orig.content

    def test_context_compactor_ratio_calculation(self):
        compactor = ContextCompactor(max_tokens=1000)
        messages = _make_messages(5, tokens_per_msg=200)
        ratio = compactor.get_compaction_ratio(messages)
        total = sum(m.token_estimate for m in messages)
        assert ratio == total / compactor._max_tokens
        assert ratio == 1.0

    def test_context_compactor_custom_summary_fn(self):
        called_with: list[str] = []

        def tracking_summary(text: str) -> str:
            called_with.append(text)
            return "CUSTOM_SUMMARY"

        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.3, preserve_recent_count=2)
        messages = _make_messages(10, tokens_per_msg=100)
        result = compactor.compact(messages, summary_fn=tracking_summary)
        assert len(called_with) == 1
        summary_msgs = [m for m in result if m.is_system and "prior context" in m.content.lower()]
        assert len(summary_msgs) == 1
        assert "CUSTOM_SUMMARY" in summary_msgs[0].content


class TestPlanArtifactE2E:
    def test_plan_artifact_from_todo(self):
        todo = Todo(
            title="Implement caching",
            description="Add Redis caching layer",
            tags=["performance", "redis"],
            test_commands=["make test-unit", "make test-e2e"],
        )
        artifact = PlanArtifact.from_todo(todo)
        assert artifact.todo_id == todo.todo_id
        assert artifact.title == "Implement caching"
        assert artifact.description == "Add Redis caching layer"
        assert "performance" in artifact.notes
        assert "redis" in artifact.notes
        assert "make test-unit" in artifact.notes

    def test_plan_artifact_to_markdown_renders(self):
        artifact = PlanArtifact(
            todo_id="TODO-ABCD",
            title="Refactor auth module",
            description="Clean up authentication code",
            target_files=["src/auth.py", "tests/test_auth.py"],
            dependencies=["TODO-0010", "TODO-0011"],
            notes="No breaking changes",
            content="Step 1: Extract helpers\nStep 2: Add tests",
        )
        md = artifact.to_markdown()
        assert "## Plan: Refactor auth module" in md
        assert "**Todo ID:** TODO-ABCD" in md
        assert "Clean up authentication code" in md
        assert "`src/auth.py`" in md
        assert "`tests/test_auth.py`" in md
        assert "TODO-0010" in md
        assert "TODO-0011" in md
        assert "No breaking changes" in md
        assert "Step 1: Extract helpers" in md

    def test_plan_artifact_roundtrip(self):
        artifact = PlanArtifact(
            todo_id="TODO-RT01",
            title="Roundtrip test",
            description="Verify serialization",
            target_files=["src/a.py"],
            contracts=["def foo() -> int"],
            dependencies=["TODO-DEP"],
            notes="Test notes",
            content="Plan body text",
        )
        data = artifact.to_dict()
        assert isinstance(data, dict)
        assert data["todo_id"] == "TODO-RT01"

        restored = PlanArtifact.from_dict(data)
        assert restored.todo_id == artifact.todo_id
        assert restored.title == artifact.title
        assert restored.description == artifact.description
        assert restored.target_files == artifact.target_files
        assert restored.contracts == artifact.contracts
        assert restored.dependencies == artifact.dependencies
        assert restored.notes == artifact.notes
        assert restored.content == artifact.content
        assert restored.created_at == artifact.created_at

    def test_plan_artifact_with_contracts(self):
        artifact = PlanArtifact(
            todo_id="TODO-CX01",
            title="API contract enforcement",
            contracts=[
                "def get_user(id: str) -> User",
                "def create_user(data: UserCreate) -> User",
                "class UserRepository",
            ],
        )
        md = artifact.to_markdown()
        assert "### Contracts" in md
        assert "`def get_user(id: str) -> User`" in md
        assert "`def create_user(data: UserCreate) -> User`" in md
        assert "`class UserRepository`" in md


class TestConversationE2E:
    def test_conversation_add_and_get_context(self):
        conv = Conversation(todo_id="TODO-CV01", return_id="RET-001")
        for i in range(10):
            conv.add_message("user", f"Message number {i} with enough words to have meaningful token count")
        conv.add_message("user", "final short msg")
        total = conv.total_tokens()
        context = conv.get_context(max_tokens=total // 2)
        assert len(context) < conv.message_count()
        assert context[-1].content == "final short msg"

    def test_conversation_total_tokens_tracks(self):
        conv = Conversation(todo_id="TODO-TK01", return_id="RET-002")
        conv.add_message("system", "System prompt with a few words")
        conv.add_message("user", "User query goes right here")
        conv.add_message("assistant", "Assistant response with some detail")
        conv.add_message("tool", "Tool output result data")
        expected = sum(m.token_count for m in conv.messages)
        assert conv.total_tokens() == expected

    def test_conversation_roundtrip(self):
        conv = Conversation(todo_id="TODO-RP01", return_id="RET-003")
        conv.add_message("system", "System prompt")
        conv.add_message("user", "User asks something")
        conv.add_message("assistant", "Assistant answers")

        data = conv.to_dict()
        restored = Conversation.from_dict(data)

        assert restored.conversation_id == conv.conversation_id
        assert restored.todo_id == conv.todo_id
        assert restored.return_id == conv.return_id
        assert len(restored.messages) == len(conv.messages)
        for orig, rest in zip(conv.messages, restored.messages, strict=True):
            assert rest.role == orig.role
            assert rest.content == orig.content
            assert rest.token_count == orig.token_count

    def test_conversation_message_roles(self):
        conv = Conversation(todo_id="TODO-RL01", return_id="RET-004")
        conv.add_message("system", "System setup")
        conv.add_message("user", "User request")
        conv.add_message("assistant", "Assistant reply")
        conv.add_message("tool", "Tool execution output")
        roles = [m.role for m in conv.messages]
        assert roles == ["system", "user", "assistant", "tool"]

    def test_conversation_auto_id(self):
        conv = Conversation(todo_id="TODO-AI01", return_id="RET-005")
        assert conv.conversation_id.startswith("conv-")
        pattern = r"^conv-[0-9a-f]{8}$"
        assert re.match(pattern, conv.conversation_id), f"ID {conv.conversation_id} doesn't match pattern"
        conv2 = Conversation(todo_id="TODO-AI02", return_id="RET-006")
        assert conv.conversation_id != conv2.conversation_id
