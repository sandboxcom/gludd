"""Tests for software_generator — ProjectSpec, SoftwareGenerator, GenerationCache."""

from __future__ import annotations

from unittest import mock

import pytest

from general_ludd.cloud.software_generator import (
    GenerationCache,
    ProjectSpec,
    SoftwareGenerator,
)


class TestProjectSpec:
    def test_default_construction(self):
        spec = ProjectSpec(
            name="test-proj",
            project_type="cli_tool",
            description="A test project",
            prompt_template="Write a CLI tool",
        )
        assert spec.name == "test-proj"
        assert spec.project_type == "cli_tool"
        assert spec.expected_output_files == 1
        assert spec.acceptance_criteria == ()
        assert spec.extra_context == ""

    def test_full_construction(self):
        spec = ProjectSpec(
            name="full-proj",
            project_type="api_server",
            description="A full project",
            prompt_template="Build an API",
            expected_output_files=3,
            acceptance_criteria=("syntax_valid", "tests_pass"),
            extra_context="use fastapi",
        )
        assert spec.expected_output_files == 3
        assert spec.acceptance_criteria == ("syntax_valid", "tests_pass")
        assert spec.extra_context == "use fastapi"

    def test_equality(self):
        a = ProjectSpec(name="x", project_type="game", description="d", prompt_template="p")
        b = ProjectSpec(name="x", project_type="game", description="d", prompt_template="p")
        assert a == b

    def test_inequality(self):
        a = ProjectSpec(name="x", project_type="game", description="d", prompt_template="p")
        b = ProjectSpec(name="y", project_type="game", description="d", prompt_template="p")
        assert a != b


class TestSoftwareGenerator:
    def test_requires_gateway_for_generate(self):
        gen = SoftwareGenerator(gateway=None)
        with pytest.raises(ValueError, match="not configured"):
            gen.generate(ProjectSpec(name="x", project_type="game", description="d", prompt_template="p"))

    def test_generate_calls_model(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "```python\nresult\n```"
        gen = SoftwareGenerator(gateway=mock_gw)
        spec = ProjectSpec(name="x", project_type="game", description="d", prompt_template="build a game")
        code = gen.generate(spec)
        assert code == "result"
        mock_gw.call_model.assert_called_once()

    def test_generate_passes_prompt_template(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "ok"
        gen = SoftwareGenerator(gateway=mock_gw)
        spec = ProjectSpec(name="t", project_type="game", description="d", prompt_template="GENERATE A GAME")
        gen.generate(spec, model_id="custom")
        call_kwargs = mock_gw.call_model.call_args
        assert call_kwargs[0][0] == "custom"
        assert "GENERATE A GAME" in call_kwargs[1]["messages"][0]["content"]

    def test_generate_multi_requires_gateway(self):
        gen = SoftwareGenerator(gateway=None)
        with pytest.raises(ValueError, match="not configured"):
            gen.generate_multi(
                ProjectSpec(name="x", project_type="game", description="d", prompt_template="p"),
                model_profiles={},
            )

    def test_validate_code_with_project_type(self):
        gen = SoftwareGenerator(gateway=None)
        result = gen.validate_code("print('valid')", project_type="cli_tool")
        assert isinstance(result, bool)

    def test_validate_code_syntax_error(self):
        gen = SoftwareGenerator(gateway=None)
        result = gen.validate_code("def broken(:", project_type=None)
        assert result is False

    def test_validate_code_valid_syntax(self):
        gen = SoftwareGenerator(gateway=None)
        result = gen.validate_code("x = 1", project_type=None)
        assert result is True

    def test_save_output_creates_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "output.py"
            SoftwareGenerator.save_output("print('hi')", str(out))
            assert out.exists()
            assert out.read_text(encoding="utf-8") == "print('hi')"

    def test_save_output_creates_parents(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "sub" / "nested" / "out.py"
            SoftwareGenerator.save_output("x = 1", str(out))
            assert out.exists()

    def test_task_policy_authorized(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "ok"
        mock_policy = mock.MagicMock()
        mock_policy.authorize.return_value = type("Decision", (), {"action": 1, "reason": ""})()

        gen = SoftwareGenerator(gateway=mock_gw, task_policy=mock_policy)
        spec = ProjectSpec(name="t", project_type="game", description="d", prompt_template="p")
        gen.generate(spec, model_identity=object())
        mock_policy.authorize.assert_called_once()

    def test_task_policy_denied_raises(self):
        mock_gw = mock.MagicMock()
        mock_policy = mock.MagicMock()
        mock_policy.authorize.return_value = type("Decision", (), {"action": 0, "reason": "blocked"})()

        gen = SoftwareGenerator(gateway=mock_gw, task_policy=mock_policy)
        spec = ProjectSpec(name="t", project_type="game", description="d", prompt_template="p")
        with pytest.raises(PermissionError, match="denied"):
            gen.generate(spec, model_identity=object())


class TestGenerationCache:
    def test_caches_generated_code(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "```python\ncached\n```"
        gen = SoftwareGenerator(gateway=mock_gw)
        cache = GenerationCache()
        spec = ProjectSpec(name="test", project_type="game", description="d", prompt_template="p")

        result1 = cache.generate(gen, spec, model_id="default")
        result2 = cache.generate(gen, spec, model_id="default")
        assert result1 == result2
        assert cache.miss_count == 1

    def test_different_model_ids_are_separate(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.side_effect = ["first", "second"]
        gen = SoftwareGenerator(gateway=mock_gw)
        cache = GenerationCache()
        spec = ProjectSpec(name="test", project_type="game", description="d", prompt_template="p")

        cache.generate(gen, spec, model_id="default")
        cache.generate(gen, spec, model_id="other")
        assert cache.miss_count == 2

    def test_miss_count_starts_zero(self):
        cache = GenerationCache()
        assert cache.miss_count == 0

    def test_different_settings_cause_miss(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.side_effect = ["a", "b"]
        gen = SoftwareGenerator(gateway=mock_gw)
        cache = GenerationCache()
        spec = ProjectSpec(name="test", project_type="game", description="d", prompt_template="p")

        cache.generate(gen, spec, model_settings={"temp": "0.7"})
        cache.generate(gen, spec, model_settings={"temp": "0.9"})
        assert cache.miss_count == 2

    def test_settings_order_independent(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "same"
        gen = SoftwareGenerator(gateway=mock_gw)
        cache = GenerationCache()
        spec = ProjectSpec(name="test", project_type="game", description="d", prompt_template="p")

        cache.generate(gen, spec, model_settings={"a": "1", "b": "2"})
        cache.generate(gen, spec, model_settings={"b": "2", "a": "1"})
        assert cache.miss_count == 1
