from __future__ import annotations

from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.task_embeddings import (
    CANONICAL_TASK_DESCRIPTIONS,
    _is_empty_vector,
    _select_default_embedder,
    _task_type_value,
)
from general_ludd.skills.embeddings import HashEmbedder


class TestCanonicalTaskDescriptions:
    def test_all_task_types_have_descriptions(self) -> None:
        task_types = [
            TaskType.BUG_FIX,
            TaskType.FEATURE,
            TaskType.REFACTOR,
            TaskType.TEST_WRITE,
            TaskType.CODE_REVIEW,
            TaskType.DOCUMENTATION,
            TaskType.DEBUGGING,
            TaskType.OPTIMIZATION,
            TaskType.SECURITY_FIX,
            TaskType.INTEGRATION,
        ]
        for tt in task_types:
            assert tt in CANONICAL_TASK_DESCRIPTIONS, f"Missing description for {tt}"

    def test_all_descriptions_are_non_empty_strings(self) -> None:
        for task_type, desc in CANONICAL_TASK_DESCRIPTIONS.items():
            assert isinstance(desc, str)
            assert len(desc) > 50, f"Description for {task_type} too short: {len(desc)} chars"

    def test_descriptions_are_unique(self) -> None:
        values = list(CANONICAL_TASK_DESCRIPTIONS.values())
        assert len(values) == len(set(values))


class TestSelectDefaultEmbedder:
    def test_returns_an_embedder(self) -> None:
        embedder = _select_default_embedder()
        assert embedder is not None
        assert hasattr(embedder, "embed")

    def test_openai_key_alone_does_not_change_default(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("GLUDD_TASK_EMBEDDINGS_PROVIDER", raising=False)

        embedder = _select_default_embedder()

        assert isinstance(embedder, HashEmbedder)

    def test_openai_embedder_requires_explicit_provider(self, monkeypatch) -> None:
        import general_ludd.scoring.task_embeddings as task_embeddings

        class FakeOpenAIEmbedder:
            def embed(self, text: str) -> list[float]:
                return [1.0]

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("GLUDD_TASK_EMBEDDINGS_PROVIDER", "openai")
        monkeypatch.setattr(task_embeddings, "OpenAIEmbedder", FakeOpenAIEmbedder)

        embedder = _select_default_embedder()

        assert isinstance(embedder, FakeOpenAIEmbedder)


class TestIsEmptyVector:
    def test_dim_zero_is_empty(self) -> None:
        class FakeRow:
            dim = 0
            embedding = "[]"
        assert _is_empty_vector(FakeRow()) is True  # type: ignore[arg-type]

    def test_dim_nonzero_is_not_empty(self) -> None:
        class FakeRow:
            dim = 128
            embedding = "[0.1, 0.2]"
        assert _is_empty_vector(FakeRow()) is False  # type: ignore[arg-type]

    def test_embedding_empty_string_is_empty(self) -> None:
        class FakeRow:
            dim = 128
            embedding = ""
        assert _is_empty_vector(FakeRow()) is True  # type: ignore[arg-type]


class TestTaskTypeValue:
    def test_from_enum(self) -> None:
        assert _task_type_value(TaskType.BUG_FIX) == "bug_fix"
        assert _task_type_value(TaskType.FEATURE) == "feature"

    def test_from_string(self) -> None:
        assert _task_type_value("bug_fix") == "bug_fix"
        assert _task_type_value("custom_type") == "custom_type"
