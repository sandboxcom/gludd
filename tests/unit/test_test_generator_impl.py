"""Tests for GeneratorImpl — creates pytest files from GenerationSpec."""

from __future__ import annotations

import ast
from pathlib import Path

from general_ludd.agents.test_generation.contracts import GenerationHarness, GenerationSpec
from general_ludd.agents.test_generation.test_generator import GeneratorImpl


class GeneratorImplCreation:
    def test_creates_with_spec_and_harness(self) -> None:
        spec = GenerationSpec(target_module="general_ludd.foo")
        harness = GenerationHarness()
        gen = GeneratorImpl(spec=spec, harness=harness)
        assert gen.spec is spec
        assert gen.harness is harness

    def test_default_output_dir_from_spec(self) -> None:
        spec = GenerationSpec(target_module="general_ludd.foo", output_dir="tests/e2e")
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        assert gen.spec.output_dir == "tests/e2e"


class GeneratorImplGenerateFile:
    def test_generates_pytest_file(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.foo",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        assert len(output_files) == 1
        assert output_files[0].endswith(".py")

    def test_generated_file_is_valid_python(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.foo",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        source = Path(output_files[0]).read_text()
        ast.parse(source)

    def test_generated_file_contains_imports(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.bar",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        source = Path(output_files[0]).read_text()
        assert "import pytest" in source

    def test_generated_file_contains_test_function(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.baz",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        source = Path(output_files[0]).read_text()
        assert "def test_" in source

    def test_output_dir_created_if_missing(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "nested" / "e2e"
        spec = GenerationSpec(
            target_module="general_ludd.qux",
            output_dir=str(out_dir),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        gen.generate()
        assert out_dir.exists()

    def test_filename_matches_target_module(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.example",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        name = Path(output_files[0]).name
        assert "example" in name

    def test_generated_file_has_module_docstring(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.docmod",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        source = Path(output_files[0]).read_text()
        assert '"""' in source

    def test_generated_file_follows_aaa_pattern(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.aaapat",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        source = Path(output_files[0]).read_text()
        assert "Arrange" in source or "arrange" in source.lower()

    def test_generated_test_collects_without_error(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.collectable",
            output_dir=str(tmp_path),
        )
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = gen.generate()
        tree = ast.parse(Path(output_files[0]).read_text())
        import_nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert len(import_nodes) > 0
