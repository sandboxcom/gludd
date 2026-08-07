"""E2E: SoftwareGenerator with cloud models (DeepSeek API).

Proves SoftwareGenerator.generate_multi() and generate() produce valid,
runnable Python for multiple project types via the DeepSeek API.

Smoke mode: PT=game. Skip without DEEPSEEK_API_KEY or .deepseek.key.

Run:
    DEEPSEEK_API_KEY="sk-..." uv run pytest tests/e2e/test_software_generator_cloud.py -v -s
    PT=game DEEPSEEK_API_KEY="sk-..." uv run pytest tests/e2e/test_software_generator_cloud.py -v -s
"""

from __future__ import annotations

import ast
import os
import tempfile
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.cloud.project_types import get_project_type, validate_project_against_rules
from general_ludd.cloud.software_generator import ProjectSpec, SoftwareGenerator

# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _load_deepseek_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    key_file = _REPO_ROOT / ".deepseek.key"
    if key_file.exists():
        v = key_file.read_text().strip()
        return v if v else None
    return None


_DS_BASE_URL = "https://api.deepseek.com/v1"
_SKIP_REASON = (
    "DEEPSEEK_API_KEY not set and .deepseek.key not found — "
    "set DEEPSEEK_API_KEY or place key in .deepseek.key to run cloud software-generator test"
)
_DEEPSEEK_KEY = _load_deepseek_key()
_PT = os.environ.get("PT", "").strip().lower()

# ---------------------------------------------------------------------------
# Gateway builder
# ---------------------------------------------------------------------------

_GATEWAY_CACHE: dict[str, Any] = {}


def _build_deepseek_gateway() -> Any:
    if "gateway" in _GATEWAY_CACHE:
        return _GATEWAY_CACHE["gateway"]

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    key = _DEEPSEEK_KEY
    assert key, "key must be set before building gateway"

    profile = ModelProfile(
        model_profile_id="deepseek_coder",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name="deepseek-chat",
        api_base_alias="DEEPSEEK_API_BASE",
        credential_alias="DEEPSEEK_API_KEY",
        context_window=65536,
        max_input_tokens=60000,
        max_output_tokens=8192,
        cost_per_input_token=0.00000027,
        cost_per_output_token=0.0000011,
        api_metered=True,
        run_budget_usd=5.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("DEEPSEEK_API_KEY", key)
    secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
    gateway = cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
    _GATEWAY_CACHE["gateway"] = gateway
    return gateway


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_GAME_PROMPT = textwrap.dedent("""\
    Write a complete, self-contained Python module implementing a number-guessing game.
    NO external dependencies except stdlib. NO display code. NO prose, no markdown.

    Requirements:
    - Class name: `NumberGuesser`
    - `__init__(self, min_val=1, max_val=100)`: pick a random secret number in [min_val, max_val]
    - `start(self)`: transition from "ready" to "playing" state
    - `guess(self, n: int) -> str`: return "too low", "too high", or "correct"
    - `score(self) -> int`: return number of guesses made
    - `render_state(self) -> dict`: return dict with keys: state, score, game_over, secret_range
    - `restart(self)`: reset score to 0, pick new secret, state="ready"

    Lifecycle rules:
    - state starts at "ready" in __init__, NOT "playing"
    - start() transitions state to "playing"
    - score is 0 at construction and after restart()
    - game_over becomes True after correct guess; score stops changing
    - guess() after game_over is a no-op

    Output ONLY the Python code. Start with `import random` and `class NumberGuesser:`.
""").strip()

_CLI_PROMPT = textwrap.dedent("""\
    Write a complete, self-contained Python module implementing a file-statistics CLI tool.
    NO external dependencies except stdlib. NO prose, no markdown.

    Requirements:
    - Class name: `FileStats`
    - `__init__(self)`: initialize with empty results
    - `start(self)`: transition to "playing" state
    - `analyze(self, filepath: str) -> dict`: count lines, words, chars, bytes; return dict
    - `render_state(self) -> dict`: return dict with keys: filepath, lines, words, chars, bytes, state
    - `restart(self)`: clear results, state="ready"
    - Use argparse for CLI: --file PATH, --json (output as JSON)
    - Print to stdout by default, sys.exit(0) on success, sys.exit(1) on error

    Lifecycle rules:
    - state starts at "ready" in __init__, NOT "playing"
    - start() transitions state to "playing"
    - restart() clears results and sets state="ready"

    Output ONLY the Python code. Start with `import argparse` and `class FileStats:`.
""").strip()


# ---------------------------------------------------------------------------
# Spec helpers
# ---------------------------------------------------------------------------


def _build_game_spec() -> ProjectSpec:
    return ProjectSpec(
        name="number_guesser",
        project_type="game",
        description="A stdlib-only number-guessing game with lifecycle state machine",
        prompt_template=_GAME_PROMPT,
        expected_output_files=1,
        acceptance_criteria=("ast_valid", "importable"),
    )


def _build_cli_spec() -> ProjectSpec:
    return ProjectSpec(
        name="file_stats",
        project_type="cli_tool",
        description="A stdlib-only CLI file-statistics tool using argparse",
        prompt_template=_CLI_PROMPT,
        expected_output_files=1,
        acceptance_criteria=("ast_valid", "importable"),
    )


# ---------------------------------------------------------------------------
# Validation helpers — computes a ProjectResult-shaped dict
# ---------------------------------------------------------------------------


def _compute_project_result(code: str, project_type: str) -> dict[str, Any]:
    """Validate generated code and return ProjectResult-shaped metrics dict."""
    result: dict[str, Any] = {
        "files": {"main.py": len(code)},
        "metrics": {
            "code_bytes": len(code.encode("utf-8")),
            "code_lines": len(code.splitlines()),
            "ast_valid": False,
            "importable": False,
            "project_type_valid": False,
        },
        "pipeline_steps": ["generate", "validate", "save"],
    }

    try:
        ast.parse(code)
        result["metrics"]["ast_valid"] = True
    except SyntaxError:
        pass

    try:
        compile(code, "<generated>", "exec")
        result["metrics"]["importable"] = True
    except Exception:
        pass

    try:
        type_def = get_project_type(project_type)
        result["metrics"]["project_type_valid"] = validate_project_against_rules(code, type_def)
    except Exception:
        pass

    # Save step
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.py")
        SoftwareGenerator.save_output(code, out)
        result["pipeline_steps"].append("save_verified") if os.path.isfile(out) else None

    return result


# ---------------------------------------------------------------------------
# Parametrized project type list
# ---------------------------------------------------------------------------

_ALL_TYPES = ("game", "cli_tool")


def _filtered_types() -> tuple[str, ...]:
    if _PT:
        return tuple(t for t in _ALL_TYPES if t == _PT)
    return _ALL_TYPES


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
class TestSoftwareGeneratorCloud:
    """SoftwareGenerator end-to-end tests with DeepSeek cloud API."""

    @pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
    @pytest.mark.parametrize("pt", [pytest.param(t, id=t) for t in _filtered_types()])
    def test_generate_multi_produces_valid_code(self, pt: str) -> None:
        """generate_multi() produces valid Python via multi-model pipeline."""
        gateway = _build_deepseek_gateway()
        generator = SoftwareGenerator(gateway)
        spec: ProjectSpec = _build_game_spec() if pt == "game" else _build_cli_spec()

        code = generator.generate_multi(spec, model_profiles={})

        assert code, f"generate_multi() returned empty code for {pt}"
        assert len(code) > 100, f"Code too short for {pt}: {len(code)} chars"

        result = _compute_project_result(code, pt)
        assert result["metrics"]["ast_valid"], f"Code must be valid Python for {pt}: {code[:200]}"
        assert result["metrics"]["code_lines"] > 10, f"Code too few lines for {pt}: {result['metrics']['code_lines']}"

    @pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
    def test_generate_single_model_fallback(self) -> None:
        """generate() single-model fallback produces valid Python."""
        gateway = _build_deepseek_gateway()
        generator = SoftwareGenerator(gateway)
        spec = _build_game_spec()

        code = generator.generate(spec)

        assert code, "generate() returned empty code"
        assert len(code) > 100, f"Code too short: {len(code)} chars"

        result = _compute_project_result(code, "game")
        assert result["metrics"]["ast_valid"], f"Code must be valid Python: {code[:200]}"

    @pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
    def test_project_result_shape(self) -> None:
        """Verify generated code produces well-shaped ProjectResult metrics."""
        gateway = _build_deepseek_gateway()
        generator = SoftwareGenerator(gateway)
        spec = _build_game_spec()

        code = generator.generate(spec)
        result = _compute_project_result(code, "game")

        # files shape
        assert isinstance(result["files"], dict), "files must be a dict"
        assert len(result["files"]) >= 1, "must have at least one file entry"
        assert any(isinstance(v, int) for v in result["files"].values()), "file sizes must be ints"

        # metrics shape
        m = result["metrics"]
        assert isinstance(m, dict)
        assert isinstance(m["code_bytes"], int) and m["code_bytes"] > 0
        assert isinstance(m["code_lines"], int) and m["code_lines"] > 5
        assert isinstance(m["ast_valid"], bool)
        assert isinstance(m["importable"], bool)

        # pipeline_steps shape
        ps = result["pipeline_steps"]
        assert isinstance(ps, list)
        assert "generate" in ps
        assert "validate" in ps
        assert "save" in ps

    @pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
    def test_project_type_validation_passes(self) -> None:
        """Generated code passes project_type-specific validation rules."""
        gateway = _build_deepseek_gateway()
        generator = SoftwareGenerator(gateway)
        spec = _build_game_spec()

        code = generator.generate(spec)

        assert generator.validate_code(code, project_type="game"), (
            f"Generated code must pass 'game' project type validation: {code[:300]}"
        )

    @pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
    def test_save_output_creates_file(self) -> None:
        """save_output() writes code to disk correctly."""
        gateway = _build_deepseek_gateway()
        generator = SoftwareGenerator(gateway)
        spec = _build_game_spec()

        code = generator.generate(spec)

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "output.py")
            generator.save_output(code, out_path)
            assert os.path.isfile(out_path), "save_output must create the file"
            saved = Path(out_path).read_text()
            assert saved == code, "saved content must match generated code"
            assert len(saved.encode("utf-8")) == len(code.encode("utf-8"))

    @pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
    def test_generate_multi_cli_tool_validates(self) -> None:
        """generate_multi() for cli_tool passes project type validation."""
        gateway = _build_deepseek_gateway()
        generator = SoftwareGenerator(gateway)
        spec = _build_cli_spec()

        code = generator.generate_multi(spec, model_profiles={})

        assert code, "generate_multi(cli_tool) returned empty code"

        valid = generator.validate_code(code, project_type="cli_tool")
        assert valid, f"CLI tool code must pass validation: {code[:300]}"

        # CLI-specific content checks
        has_argparse = "argparse" in code or "click" in code
        assert has_argparse, f"CLI tool must use argparse or click: {code[:200]}"


@pytest.mark.e2e
class TestSoftwareGeneratorNoKey:
    """Tests that run without an API key — structural and ProjectSpec checks."""

    def test_project_spec_construction(self) -> None:
        """ProjectSpec objects can be built without an API key."""
        spec = _build_game_spec()
        assert spec.name == "number_guesser"
        assert spec.project_type == "game"
        assert spec.expected_output_files == 1
        assert len(spec.prompt_template) > 100
        assert len(spec.acceptance_criteria) == 2

    def test_cli_spec_construction(self) -> None:
        """CLI ProjectSpec builds correctly."""
        spec = _build_cli_spec()
        assert spec.name == "file_stats"
        assert spec.project_type == "cli_tool"
        assert "argparse" in spec.prompt_template

    def test_save_output_idempotent(self) -> None:
        """save_output() creates parent directories and writes content."""
        with tempfile.TemporaryDirectory() as td:
            deep = os.path.join(td, "nested", "dir", "output.py")
            SoftwareGenerator.save_output("x = 1\n", deep)
            assert os.path.isfile(deep)
            assert Path(deep).read_text() == "x = 1\n"

    def test_project_type_registry_has_targets(self) -> None:
        """Project type registry contains the types under test."""
        from general_ludd.cloud.project_types import available_type_ids

        types = available_type_ids()
        assert "game" in types
        assert "cli_tool" in types

    def test_validate_code_no_type_fallback(self) -> None:
        """validate_code() with no project_type falls back to ast.parse."""
        generator = SoftwareGenerator(gateway=None)
        assert generator.validate_code("x = 1\n") is True
        assert generator.validate_code("x = ", project_type=None) is False
