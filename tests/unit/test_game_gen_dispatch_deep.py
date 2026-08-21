"""Deep game generation dispatch tests: snake game, AST validation,
code extraction, server lifecycle, error recovery."""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from general_ludd.cloud.game_e2e import (
    AzureGameE2E,
    DeploymentConfig,
    E2EResult,
    Frame,
    FrameComparator,
    GameGenerationCache,
    GameGenerator,
    GameInputEvent,
    GameRunner,
    GameSpec,
    _collect_pygame_controls,
    _has_rendered_menu_state,
    build_game_input_script,
)
from general_ludd.cloud.game_generation import normalize_generated_python

_LOOP_CONTENT = (
    "import pygame\npygame.init()\nwhile True:\n"
    "    for e in pygame.event.get():\n        pass\n    pygame.display.flip()\n"
)

SNAKE_VALID_CODE = """
import pygame
import random

class Snake:
    def __init__(self):
        self.grid_w = 20
        self.grid_h = 20
        self.restart()

    def start(self):
        self.restart()

    def restart(self):
        self.body = [(10, 10), (9, 10), (8, 10)]
        self.direction = "right"
        self._score = 0
        self._game_over = False
        self.food = self._place_food()

    def _place_food(self):
        while True:
            fx = random.randint(0, self.grid_w - 1)
            fy = random.randint(0, self.grid_h - 1)
            if (fx, fy) not in self.body:
                return (fx, fy)

    def tick(self, direction):
        if self._game_over:
            return
        self.direction = direction
        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
        head = (self.body[0][0] + dx, self.body[0][1] + dy)
        if not (0 <= head[0] < self.grid_w and 0 <= head[1] < self.grid_h):
            self._game_over = True
            return
        if head in self.body:
            self._game_over = True
            return
        self.body.insert(0, head)
        if head == self.food:
            self._score += 1
            self.food = self._place_food()
        else:
            self.body.pop()

    def score(self) -> int:
        return self._score

    def is_game_over(self) -> bool:
        return self._game_over

    def restart(self):
        self.body = [(10, 10), (9, 10), (8, 10)]
        self.direction = "right"
        self._score = 0
        self._game_over = False
        self.food = self._place_food()

pygame.init()
screen = pygame.display.set_mode((800, 600))
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""

SNAKE_MISSING_METHOD_CODE = """
class Snake:
    def __init__(self):
        self._score = 0
    def start(self):
        pass
    def tick(self, direction):
        pass
    def score(self) -> int:
        return self._score
"""


# ═══════════════════════════════════════════════════════════════════════════
#  1. Snake game AST validation
# ═══════════════════════════════════════════════════════════════════════════


class TestSnakeGameASTValidation:
    """Deep AST validation of generated Snake game code."""

    REQUIRED_METHODS = frozenset({"__init__", "start", "tick", "score", "is_game_over", "restart"})

    def _parse_code(self, code: str) -> ast.AST:
        return ast.parse(code)

    def _find_class(self, tree: ast.AST, class_name: str) -> ast.ClassDef | None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        return None

    def _find_methods(self, cls_node: ast.ClassDef) -> dict[str, ast.FunctionDef]:
        methods: dict[str, ast.FunctionDef] = {}
        for node in ast.walk(cls_node):
            if isinstance(node, ast.FunctionDef):
                methods[node.name] = node
        return methods

    def test_valid_snake_code_has_all_required_methods(self) -> None:
        tree = self._parse_code(SNAKE_VALID_CODE)
        cls_node = self._find_class(tree, "Snake")
        assert cls_node is not None, "Snake class must be present"
        methods = self._find_methods(cls_node)
        for name in self.REQUIRED_METHODS:
            assert name in methods, f"Missing required method: {name}"

    def test_valid_snake_code_score_returns_int_annotation(self) -> None:
        tree = self._parse_code(SNAKE_VALID_CODE)
        cls_node = self._find_class(tree, "Snake")
        assert cls_node is not None
        methods = self._find_methods(cls_node)
        score_fn = methods["score"]
        assert score_fn.returns is not None, "score() must have return annotation"
        score_returns = score_fn.returns
        if isinstance(score_returns, ast.Name):
            assert score_returns.id == "int", f"score() must return int, got {score_returns.id}"
        elif isinstance(score_returns, ast.Subscript):
            pass

    def test_valid_snake_code_is_game_over_returns_bool(self) -> None:
        tree = self._parse_code(SNAKE_VALID_CODE)
        cls_node = self._find_class(tree, "Snake")
        assert cls_node is not None
        methods = self._find_methods(cls_node)
        go_fn = methods["is_game_over"]
        assert go_fn.returns is not None, "is_game_over() must have return annotation"
        returns = go_fn.returns
        if isinstance(returns, ast.Name):
            assert returns.id == "bool", f"is_game_over() must return bool, got {returns.id}"

    def test_valid_snake_code_tick_accepts_direction_param(self) -> None:
        tree = self._parse_code(SNAKE_VALID_CODE)
        cls_node = self._find_class(tree, "Snake")
        assert cls_node is not None
        methods = self._find_methods(cls_node)
        tick_fn = methods["tick"]
        args = tick_fn.args
        assert len(args.args) >= 2, "tick must accept (self, direction)"
        assert args.args[1].arg == "direction", "Second parameter must be 'direction'"

    def test_snake_code_missing_methods_detected(self) -> None:
        tree = self._parse_code(SNAKE_MISSING_METHOD_CODE)
        cls_node = self._find_class(tree, "Snake")
        assert cls_node is not None
        methods = self._find_methods(cls_node)
        missing = self.REQUIRED_METHODS - set(methods)
        assert missing == {"is_game_over", "restart"}, f"Unexpected missing: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
#  2. GameGenerator.validate_game_code deep paths
# ═══════════════════════════════════════════════════════════════════════════


class TestGameGeneratorValidateDeep:
    """Tests for GameGenerator.validate_game_code beyond basic AST."""

    def test_validate_valid_pygame_code(self) -> None:
        code = "import pygame\npygame.init()\nwhile True:\n    pygame.display.flip()\n"
        assert GameGenerator.validate_game_code(code) is True

    def test_validate_syntax_error(self) -> None:
        assert GameGenerator.validate_game_code("{{{") is False

    def test_validate_no_pygame_import(self) -> None:
        code = "import os\npygame.init()\nwhile True:\n    pass\n"
        assert GameGenerator.validate_game_code(code) is False

    def test_validate_no_game_loop(self) -> None:
        code = "import pygame\npygame.init()\npygame.display.flip()\n"
        assert GameGenerator.validate_game_code(code) is False

    def test_validate_no_pygame_init(self) -> None:
        code = "import pygame\nwhile True:\n    pass\n"
        assert GameGenerator.validate_game_code(code) is False

    def test_validate_spec_with_menu_requirement_has_menu_state(self) -> None:
        code = """
import pygame
state = "menu"
while True:
    if state == "menu":
        for event in pygame.event.get():
            pass
        pygame.display.flip()
pygame.init()
"""
        spec = GameSpec(
            name="test",
            genre="fps",
            description="test",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            requires_menu=True,
            required_controls=(),
        )
        assert GameGenerator.validate_game_code(code, spec) is True

    def test_validate_spec_with_menu_requirement_missing_menu_state(self) -> None:
        code = """
import pygame
state = "game"
while True:
    for event in pygame.event.get():
        pass
    pygame.display.flip()
pygame.init()
"""
        spec = GameSpec(
            name="test",
            genre="fps",
            description="test",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            requires_menu=True,
            required_controls=(),
        )
        assert GameGenerator.validate_game_code(code, spec) is False

    def test_validate_spec_with_required_controls_all_present(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_w:
                pass
            if event.key == pygame.K_s:
                pass
            if event.key == pygame.K_ESCAPE:
                pass
        if event.type == pygame.MOUSEMOTION:
            pass
    pygame.display.flip()
"""
        spec = GameSpec(
            name="test",
            genre="fps",
            description="test",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            required_controls=("w", "s", "escape", "mouse"),
        )
        assert GameGenerator.validate_game_code(code, spec) is True

    def test_validate_spec_with_required_controls_missing(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_w:
                pass
    pygame.display.flip()
"""
        spec = GameSpec(
            name="test",
            genre="fps",
            description="test",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            required_controls=("w", "a", "d"),
        )
        assert GameGenerator.validate_game_code(code, spec) is False


# ═══════════════════════════════════════════════════════════════════════════
#  3. Code extraction — normalize_generated_python deeper paths
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeGeneratedPythonDeep:
    """Deeper paths through normalize_generated_python not covered by test_game_generation.py."""

    def test_handles_mapping_with_text_key(self) -> None:
        response = SimpleNamespace(
            content=[{"type": "text", "text": "```python\nprint(1)\n```"}],
        )
        result = normalize_generated_python(response)
        assert result == "print(1)"

    def test_handles_mapping_with_content_key(self) -> None:
        response = SimpleNamespace(
            content={"type": "output_text", "content": "```python\nx = 42\n```"},
        )
        result = normalize_generated_python(response)
        assert result == "x = 42"

    def test_skips_non_text_block_types(self) -> None:
        response = SimpleNamespace(
            content=[
                {"type": "image_url", "image_url": {"url": "http://img"}},
                {"type": "text", "text": "```python\nresult = 1\n```"},
            ],
        )
        result = normalize_generated_python(response)
        assert result == "result = 1"

    def test_extracts_from_second_fence_when_first_is_empty(self) -> None:
        response = SimpleNamespace(
            content="```python\n\n```\n```python\nvalid = True\n```",
        )
        result = normalize_generated_python(response)
        assert result == "valid = True"

    def test_raw_response_with_callable_text_adapter(self) -> None:
        code = "x = 1 + 2\n"
        raw = SimpleNamespace(text=lambda: code)
        response = SimpleNamespace(content="irrelevant", raw_response=raw)
        result = normalize_generated_python(response)
        assert result == code.strip()

    def test_raw_response_text_adapter_not_string_skipped(self) -> None:
        raw = SimpleNamespace(text=42)
        response = SimpleNamespace(content="```python\nused = True\n```", raw_response=raw)
        result = normalize_generated_python(response)
        assert result == "used = True"

    def test_empty_raw_response_falls_back_to_public_content(self) -> None:
        raw = SimpleNamespace(content="")
        response = SimpleNamespace(content="```python\nfallback\n```", raw_response=raw)
        result = normalize_generated_python(response)
        assert result == "fallback"

    def test_labeled_fence_case_insensitive(self) -> None:
        for label in ("Python", "python", "py", "PY"):
            response = SimpleNamespace(content=f"```{label}\ncode = 1\n```")
            result = normalize_generated_python(response)
            assert result == "code = 1", f"Failed for label: {label}"

    def test_unclosed_fence_extracts_all_code_after_opener(self) -> None:
        response = SimpleNamespace(content="Intro text\n```python\nx = 1\ny = 2")
        result = normalize_generated_python(response)
        assert "x = 1" in result
        assert "y = 2" in result

    def test_no_fence_returns_stripped_text(self) -> None:
        response = SimpleNamespace(content="  plain text without any markdown  ")
        result = normalize_generated_python(response)
        assert result == "plain text without any markdown"


# ═══════════════════════════════════════════════════════════════════════════
#  4. Server lifecycle — GameRunner and processes
# ═══════════════════════════════════════════════════════════════════════════


class TestGameRunnerLifecycle:
    """Tests for GameRunner server/process lifecycle management."""

    def test_cleanup_kills_tracked_processes(self) -> None:
        runner = GameRunner()
        proc = MagicMock(spec=subprocess.Popen)
        runner._processes.append(proc)
        runner.cleanup()
        proc.kill.assert_called_once()
        proc.wait.assert_called_once()
        assert len(runner._processes) == 0

    def test_cleanup_handles_oserror_in_kill(self) -> None:
        runner = GameRunner()
        proc = MagicMock(spec=subprocess.Popen)
        proc.kill.side_effect = OSError("process gone")
        runner._processes.append(proc)
        runner.cleanup()
        assert len(runner._processes) == 0

    def test_del_calls_cleanup(self) -> None:
        runner = GameRunner()
        proc = MagicMock(spec=subprocess.Popen)
        runner._processes.append(proc)
        runner.__del__()
        proc.kill.assert_called_once()

    def test_run_headless_timeout_kills_process(self) -> None:
        runner = GameRunner()
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="python", timeout=120), None]
        with (
            patch.object(subprocess, "Popen", return_value=proc),
            patch.object(os, "environ", {"SDL_VIDEODRIVER": "dummy"}),
            patch("general_ludd.cloud.game_e2e._require_pygame", return_value=MagicMock()),
        ):
            result = runner.run_headless("/fake/game.py", num_frames=30)
        assert result == []
        assert proc.kill.called
        assert proc.wait.call_count == 2
        assert runner._processes == []
        proc.stdout.close.assert_called_once()
        proc.stderr.close.assert_called_once()

        runner.cleanup()
        assert proc.wait.call_count == 2

    def test_run_headless_releases_completed_process_resources(self) -> None:
        runner = GameRunner()
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        with (
            patch.object(subprocess, "Popen", return_value=proc),
            patch.object(os, "environ", {"SDL_VIDEODRIVER": "dummy"}),
            patch("general_ludd.cloud.game_e2e._require_pygame", return_value=MagicMock()),
        ):
            assert runner.run_headless("/fake/game.py", num_frames=1) == []

        assert runner._processes == []
        proc.stdout.close.assert_called_once()
        proc.stderr.close.assert_called_once()

        runner.cleanup()
        proc.wait.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
#  5. Error recovery — gateway, policy, edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestGeneratorErrorRecovery:
    """Error recovery and rejection paths in GameGenerator and friends."""

    def test_generator_none_gateway_raises_valueerror(self) -> None:
        gen = GameGenerator(None)
        spec = GameSpec(
            name="test",
            genre="arcade",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
        )
        with pytest.raises(ValueError, match="not configured"):
            gen.generate_game(spec)

    def test_cache_miss_count_increments_on_miss(self) -> None:
        cache = GameGenerationCache()
        assert cache.miss_count == 0
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content=_LOOP_CONTENT,
        )
        gen = GameGenerator(gateway)
        spec = GameSpec(
            name="test",
            genre="arcade",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="import pygame",
        )
        cache.generate(gen, spec)
        assert cache.miss_count == 1

    def test_cache_hit_returns_cached_and_does_not_increment_miss(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content=_LOOP_CONTENT,
        )
        gen = GameGenerator(gateway)
        spec = GameSpec(
            name="test",
            genre="arcade",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="import pygame",
        )
        first = cache.generate(gen, spec)
        assert cache.miss_count == 1
        second = cache.generate(gen, spec)
        assert second == first
        assert cache.miss_count == 1

    def test_cache_different_model_ids_produce_different_keys(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="a = 1\n"),
            SimpleNamespace(content="b = 2\n"),
        ]
        gen = GameGenerator(gateway)
        spec = GameSpec(
            name="test",
            genre="arcade",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="p",
        )
        r1 = cache.generate(gen, spec, model_id="mdl-A")
        r2 = cache.generate(gen, spec, model_id="mdl-B")
        assert r1 != r2
        assert cache.miss_count == 2

    def test_azure_e2e_captures_generation_error(self) -> None:
        e2e = AzureGameE2E()
        spec = GameSpec(
            name="test",
            genre="arcade",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
        )
        result = e2e.run_full_test(spec)
        assert not result.code_generated
        assert len(result.errors) >= 1


# ═══════════════════════════════════════════════════════════════════════════
#  6. GameSpec and input script construction
# ═══════════════════════════════════════════════════════════════════════════


class TestGameInputScriptDeep:
    """Deep tests for build_game_input_script and GameInputEvent."""

    def test_input_event_creation(self) -> None:
        ev = GameInputEvent(frame=5, control="w")
        assert ev.frame == 5
        assert ev.control == "w"

    def test_build_input_script_return_first(self) -> None:
        spec = GameSpec(
            name="test",
            genre="fps",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            required_controls=("w", "a", "return", "escape"),
        )
        script = build_game_input_script(spec)
        assert script[0].control == "return"
        assert script[0].frame == 0

    def test_build_input_script_escape_last(self) -> None:
        spec = GameSpec(
            name="test",
            genre="fps",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            required_controls=("w", "escape", "return"),
        )
        script = build_game_input_script(spec)
        assert script[-1].control == "escape"

    def test_build_input_script_no_return_no_escape(self) -> None:
        spec = GameSpec(
            name="test",
            genre="fps",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            required_controls=("w", "a", "d"),
        )
        script = build_game_input_script(spec)
        assert len(script) == 3
        assert [s.control for s in script] == ["w", "a", "d"]

    def test_build_input_script_empty_controls(self) -> None:
        spec = GameSpec(
            name="test",
            genre="fps",
            description="d",
            expected_frames=30,
            similarity_threshold=0.0,
            prompt_template="",
            required_controls=(),
        )
        script = build_game_input_script(spec)
        assert len(script) == 0


# ═══════════════════════════════════════════════════════════════════════════
#  7. Pygame control collection and menu state detection
# ═══════════════════════════════════════════════════════════════════════════


class TestPygameControlCollection:
    """Unit tests for _collect_pygame_controls and _has_rendered_menu_state."""

    def test_collect_finds_key_constants(self) -> None:
        code = "import pygame\nif event.key == pygame.K_w:\n    pass\nif event.key == pygame.K_SPACE:\n    pass\n"
        tree = ast.parse(code)
        controls = _collect_pygame_controls(tree)
        assert "w" in controls
        assert "space" in controls

    def test_collect_mouse_from_mousemotion(self) -> None:
        code = "import pygame\nif event.type == pygame.MOUSEMOTION:\n    pass\n"
        tree = ast.parse(code)
        controls = _collect_pygame_controls(tree)
        assert "mouse" in controls

    def test_has_menu_state_true_with_menu_branch(self) -> None:
        code = """
state = "menu"
if state == "menu":
    pass
elif state == "playing":
    pass
"""
        tree = ast.parse(code)
        assert _has_rendered_menu_state(tree) is True

    def test_has_menu_state_false_with_no_menu(self) -> None:
        code = """
state = "playing"
if state == "playing":
    pass
"""
        tree = ast.parse(code)
        assert _has_rendered_menu_state(tree) is False


# ═══════════════════════════════════════════════════════════════════════════
#  8. Frame comparator edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestFrameComparatorDeep:
    """Deeper tests for FrameComparator beyond basic happy path."""

    def test_ssim_matching_frames_is_one(self) -> None:
        fc = FrameComparator()
        a = np.zeros((10, 10, 3), dtype=np.uint8)
        b = np.zeros((10, 10, 3), dtype=np.uint8)
        result = fc.compute_ssim(a, b)
        assert result == pytest.approx(1.0)

    def test_ssim_mismatched_shapes_returns_zero(self) -> None:
        fc = FrameComparator()
        a = np.zeros((10, 10, 3), dtype=np.uint8)
        b = np.zeros((12, 10, 3), dtype=np.uint8)
        assert fc.compute_ssim(a, b) == 0.0

    def test_psnr_matching_frames_is_inf(self) -> None:
        fc = FrameComparator()
        a = np.zeros((10, 10, 3), dtype=np.uint8)
        b = np.zeros((10, 10, 3), dtype=np.uint8)
        result = fc.compute_psnr(a, b)
        assert result == float("inf")

    def test_compare_frames_no_data(self) -> None:
        fc = FrameComparator()
        result = fc.compare_frames([], [], threshold=0.4)
        assert result["mean_ssim"] == 0.0
        assert result["pass"] is False
        assert result["frame_count"] == 0

    def test_compare_frames_different_lengths_uses_common_prefix(self) -> None:
        fc = FrameComparator()
        a: list[Frame] = [np.zeros((10, 10, 3), dtype=np.uint8)] * 5
        b: list[Frame] = [np.zeros((10, 10, 3), dtype=np.uint8)] * 3
        result = fc.compare_frames(a, b, threshold=0.4)
        assert result["frame_count"] == 3
        assert result["pass"] is True

    def test_compare_frames_below_threshold(self) -> None:
        fc = FrameComparator()
        a = np.zeros((10, 10, 3), dtype=np.uint8)
        b = np.full((10, 10, 3), 255, dtype=np.uint8)
        result = fc.compare_frames([a], [b], threshold=0.9)
        assert result["pass"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  9. E2E result structure and report
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EResultAndReport:
    """Tests for E2EResult dataclass and AzureGameE2E reporting."""

    def test_e2e_result_defaults_all_false(self) -> None:
        result = E2EResult(spec_name="test")
        assert result.code_generated is False
        assert result.code_valid is False
        assert result.game_ran is False
        assert result.frames_captured == 0
        assert result.errors == []

    def test_e2e_result_with_errors(self) -> None:
        result = E2EResult(spec_name="test", errors=["error1", "error2"])
        assert len(result.errors) == 2

    def test_azure_report_results_serializes(self) -> None:
        e2e = AzureGameE2E()
        result = E2EResult(
            spec_name="snake",
            code_generated=True,
            code_valid=True,
            game_ran=True,
            frames_captured=15,
            mean_ssim=0.85,
            comparison_pass=True,
            input_controls_injected=("w", "a"),
        )
        report = e2e.report_results(result)
        assert report["spec_name"] == "snake"
        assert report["code_generated"] is True
        assert report["frames_captured"] == 15
        assert report["comparison_pass"] is True
        assert report["input_controls_injected"] == ("w", "a")

    def test_gateway_integration_roundtrip(self) -> None:
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content=_LOOP_CONTENT,
        )
        gen = GameGenerator(gateway)
        spec = GameSpec(
            name="doom_e1m1_hallway",
            genre="fps",
            description="test",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="generate doom hallway game",
        )
        code = gen.generate_game(spec)
        assert len(code) > 0
        assert GameGenerator.validate_game_code(code) is True


# ═══════════════════════════════════════════════════════════════════════════
#  10. DeploymentConfig defaults
# ═══════════════════════════════════════════════════════════════════════════


class TestDeploymentConfigDefaults:
    """DeploymentConfig default values and mutability."""

    def test_default_resource_group(self) -> None:
        config = DeploymentConfig()
        assert config.resource_group == "gludd-game-e2e"

    def test_default_vm_size(self) -> None:
        config = DeploymentConfig()
        assert config.vm_size == "Standard_NC24ads_A100_v4"

    def test_default_location(self) -> None:
        config = DeploymentConfig()
        assert config.location == "eastus"

    def test_default_use_preprovisioned(self) -> None:
        config = DeploymentConfig()
        assert config.use_preprovisioned is True

    def test_default_provision_timeout(self) -> None:
        config = DeploymentConfig()
        assert config.provision_timeout_seconds == 600

    def test_reference_cache_dir_is_under_home(self) -> None:
        config = DeploymentConfig()
        assert ".cache" in config.reference_cache_dir
        assert "gludd" in config.reference_cache_dir

    def test_allow_reference_network_default_false(self) -> None:
        config = DeploymentConfig()
        assert config.allow_reference_network is False


# ═══════════════════════════════════════════════════════════════════════════
#  11. Test count self-pin
# ═══════════════════════════════════════════════════════════════════════════


def test_deep_game_gen_test_count() -> None:
    import re

    source = Path(__file__).read_text()
    count = len(re.findall(r"^\s*def test_", source, re.MULTILINE))
    assert count >= 45, f"Expected >=45 test functions, found {count}"
