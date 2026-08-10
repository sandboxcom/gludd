"""Deep tests for general_ludd.cloud.game_e2e — pure-function contracts, AST
validation, frame metrics, dataclass shapes, and input script ordering.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from general_ludd.cloud.game_e2e import (
    _HAS_SKIMAGE,
    E2EResult,
    FrameComparator,
    GameGenerationCache,
    GameGenerator,
    GameInputEvent,
    GameRunner,
    GameSpec,
    build_game_input_script,
    structural_similarity,
)

# ── GameSpec dataclass ────────────────────────────────────────────────────


def test_game_spec_default_fields():
    spec = GameSpec(
        name="test_game",
        genre="rpg",
        description="A test RPG game",
        expected_frames=60,
        similarity_threshold=0.5,
        prompt_template="Write a game",
    )
    assert spec.reference_video_url is None
    assert spec.required_controls == ()
    assert spec.requires_menu is False


def test_game_spec_all_fields_populated():
    spec = GameSpec(
        name="full",
        genre="fps",
        description="Full FPS",
        expected_frames=30,
        similarity_threshold=0.45,
        prompt_template="Create an FPS",
        reference_video_url="https://example.com/vid.mp4",
        required_controls=("w", "a", "s", "d"),
        requires_menu=True,
    )
    assert spec.name == "full"
    assert spec.required_controls == ("w", "a", "s", "d")
    assert spec.requires_menu is True
    assert spec.reference_video_url == "https://example.com/vid.mp4"


# ── GameInputEvent ────────────────────────────────────────────────────────


def test_game_input_event_frozen():
    event = GameInputEvent(frame=5, control="w")
    assert event.frame == 5
    assert event.control == "w"
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.frame = 10


def test_game_input_event_equality():
    a = GameInputEvent(frame=0, control="return")
    b = GameInputEvent(frame=0, control="return")
    c = GameInputEvent(frame=1, control="return")
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


# ── build_game_input_script ───────────────────────────────────────────────


def test_build_game_input_script_return_first():
    spec = GameSpec(
        name="menu_game",
        genre="fps",
        description="A game with menu",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write a game",
        required_controls=("w", "a", "return", "escape", "mouse"),
        requires_menu=True,
    )
    result = build_game_input_script(spec)
    assert result[0].control == "return"
    assert result[-1].control == "escape"
    controls = [e.control for e in result]
    assert controls == ["return", "w", "a", "mouse", "escape"]


def test_build_game_input_script_no_return():
    spec = GameSpec(
        name="no_menu",
        genre="rpg",
        description="No menu game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("space", "w", "mouse"),
    )
    result = build_game_input_script(spec)
    controls = [e.control for e in result]
    assert controls == ["space", "w", "mouse"]


def test_build_game_input_script_only_return_and_escape():
    spec = GameSpec(
        name="menu_only",
        genre="fps",
        description="Menu only",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("return", "escape"),
        requires_menu=True,
    )
    result = build_game_input_script(spec)
    controls = [e.control for e in result]
    assert controls == ["return", "escape"]


def test_build_game_input_script_empty_controls():
    spec = GameSpec(
        name="empty",
        genre="rpg",
        description="No controls",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=(),
    )
    result = build_game_input_script(spec)
    assert result == ()


def test_build_game_input_script_escape_before_return():
    spec = GameSpec(
        name="esc_first",
        genre="fps",
        description="Escape first in controls",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("escape", "return", "w"),
        requires_menu=True,
    )
    result = build_game_input_script(spec)
    controls = [e.control for e in result]
    assert controls == ["return", "w", "escape"]


def test_build_game_input_script_frames_increment():
    spec = GameSpec(
        name="seq",
        genre="fps",
        description="Sequence",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("w", "a", "s", "d"),
    )
    result = build_game_input_script(spec)
    for i, event in enumerate(result):
        assert event.frame == i


# ── GameGenerator.validate_game_code ──────────────────────────────────────

VALID_GAME_CODE = """
import pygame

pygame.init()
screen = pygame.display.set_mode((800, 600))
clock = pygame.time.Clock()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_w:
                pass
        if event.type == pygame.QUIT:
            running = False
    screen.fill((0, 0, 0))
    pygame.display.flip()
    clock.tick(60)
pygame.quit()
"""


def test_validate_game_code_valid():
    assert GameGenerator.validate_game_code(VALID_GAME_CODE) is True


def test_validate_game_code_syntax_error():
    assert GameGenerator.validate_game_code("def foo(:") is False
    assert GameGenerator.validate_game_code("import pygame\nwhile True break") is False


def test_validate_game_code_no_pygame_import():
    code = """
def main():
    while True:
        pass
"""
    assert GameGenerator.validate_game_code(code) is False


def test_validate_game_code_no_while_loop():
    code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code) is False


def test_validate_game_code_no_pygame_init():
    code = """
import pygame
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
"""
    assert GameGenerator.validate_game_code(code) is False


def test_validate_game_code_import_from_pygame():
    code = """
from pygame.locals import *
pygame.init()
screen = pygame.display.set_mode((800, 600))
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code) is True


def test_validate_game_code_with_spec_menu_requirement():
    spec = GameSpec(
        name="menu_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        requires_menu=True,
        required_controls=(),
    )
    code_no_menu = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code_no_menu, spec) is False


def test_validate_game_code_with_spec_required_controls():
    spec = GameSpec(
        name="controls_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("w", "space", "mouse", "escape"),
    )
    code_missing_controls = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_w:
                pass
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code_missing_controls, spec) is False


def test_validate_game_code_with_spec_all_controls_present():
    spec = GameSpec(
        name="controls_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("w", "space"),
    )
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_w:
                pass
            elif event.key == pygame.K_SPACE:
                pass
        if event.type == pygame.MOUSEMOTION:
            pass
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_with_spec_mouse_control():
    spec = GameSpec(
        name="mouse_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("mouse",),
    )
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.MOUSEMOTION:
            pass
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_with_spec_menu_state_present():
    spec = GameSpec(
        name="menu_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        requires_menu=True,
        required_controls=(),
    )
    code = """
import pygame
pygame.init()
state = "menu"
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    if state == "menu":
        pass
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_with_spec_menu_state_assign_only():
    spec = GameSpec(
        name="menu_game_partial",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        requires_menu=True,
        required_controls=(),
    )
    code = """
import pygame
pygame.init()
state = "menu"
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is False


def test_validate_game_code_with_spec_menu_branch_only():
    spec = GameSpec(
        name="menu_game_partial",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        requires_menu=True,
        required_controls=(),
    )
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    if running:
        pass
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is False


def test_validate_game_code_return_key_mapping():
    spec = GameSpec(
        name="return_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("return",),
    )
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
            pass
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_enter_alias_for_return():
    spec = GameSpec(
        name="enter_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("return",),
    )
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN and event.key == pygame.K_KP_ENTER:
            pass
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_mouse_via_get_rel():
    spec = GameSpec(
        name="mouse_rel",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("mouse",),
    )
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    rel = pygame.mouse.get_rel()
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_mouse_via_get_pos():
    spec = GameSpec(
        name="mouse_pos",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        required_controls=("mouse",),
    )
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    pos = pygame.mouse.get_pos()
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


# ── GameGenerator._to_project_spec ────────────────────────────────────────


def test_game_generator_to_project_spec():
    spec = GameSpec(
        name="test_proj",
        genre="rpg",
        description="A test RPG",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Generate code",
    )
    result = GameGenerator._to_project_spec(spec)
    assert result.name == "test_proj"
    assert result.project_type == "game"
    assert result.description == "A test RPG"
    assert result.prompt_template == "Generate code"


def test_game_generator_constructor():
    gen = GameGenerator(gateway=None)
    assert gen._gateway is None
    gen2 = GameGenerator(gateway=None, task_policy=object())
    assert gen2._task_policy is not None


# ── E2EResult dataclass ───────────────────────────────────────────────────


def test_e2e_result_defaults():
    result = E2EResult(spec_name="test")
    assert result.spec_name == "test"
    assert result.code_generated is False
    assert result.code_valid is False
    assert result.game_ran is False
    assert result.frames_captured == 0
    assert result.mean_ssim == 0.0
    assert result.mean_psnr == 0.0
    assert result.comparison_pass is False
    assert result.errors == []
    assert result.generated_code_path == ""
    assert result.generated_code == ""
    assert result.reference_source_url == ""
    assert result.reference_video_id == ""
    assert result.reference_cache_status == ""
    assert result.reference_frames_sampled == 0
    assert result.reference_network_used is False
    assert result.comparison_threshold == 0.0
    assert result.motion_correlation == 0.0
    assert result.input_controls_injected == ()


def test_e2e_result_populated_fields():
    result = E2EResult(
        spec_name="doom",
        code_generated=True,
        code_valid=True,
        game_ran=True,
        frames_captured=30,
        mean_ssim=0.85,
        mean_psnr=35.2,
        comparison_pass=True,
        errors=[],
        generated_code_path="/tmp/game.py",
        generated_code="import pygame",
        reference_source_url="https://ex.com/v.mp4",
        reference_video_id="doom_e1m1",
        reference_cache_status="hit",
        reference_frames_sampled=30,
        reference_network_used=False,
        comparison_threshold=0.4,
        motion_correlation=0.92,
        input_controls_injected=("w", "a", "s", "d"),
    )
    assert result.spec_name == "doom"
    assert result.code_generated is True
    assert result.mean_ssim == 0.85
    assert result.input_controls_injected == ("w", "a", "s", "d")


# ── FrameComparator with numpy only (no skimage) ──────────────────────────


@pytest.fixture
def solid_frame():
    return np.full((64, 64, 3), 128, dtype=np.uint8)


@pytest.fixture
def black_frame():
    return np.zeros((64, 64, 3), dtype=np.uint8)


@pytest.fixture
def white_frame():
    return np.full((64, 64, 3), 255, dtype=np.uint8)


def test_compute_ssim_identical_frames(solid_frame):
    val = FrameComparator.compute_ssim(solid_frame, solid_frame)
    assert val == 1.0 or pytest.approx(val, 0.001) == 1.0


def test_compute_ssim_different_shapes():
    a = np.zeros((64, 64, 3), dtype=np.uint8)
    b = np.zeros((32, 32, 3), dtype=np.uint8)
    assert FrameComparator.compute_ssim(a, b) == 0.0


def test_compute_ssim_different_content(black_frame, white_frame):
    val = FrameComparator.compute_ssim(black_frame, white_frame)
    assert val >= 0.0
    assert val < 1.0


def test_compute_psnr_identical_frames(solid_frame):
    val = FrameComparator.compute_psnr(solid_frame, solid_frame)
    assert val == float("inf")


def test_compute_psnr_different_shapes():
    a = np.zeros((64, 64, 3), dtype=np.uint8)
    b = np.zeros((32, 32, 3), dtype=np.uint8)
    assert FrameComparator.compute_psnr(a, b) == 0.0


def test_compute_psnr_different_content(black_frame, white_frame):
    val = FrameComparator.compute_psnr(black_frame, white_frame)
    assert val >= 0.0
    assert val < 100.0


def test_compare_frames_empty_lists():
    result = FrameComparator.compare_frames([], [], threshold=0.5)
    assert result["mean_ssim"] == 0.0
    assert result["mean_psnr"] == 0.0
    assert result["pass"] is False
    assert result["frame_count"] == 0


def test_compare_frames_identical_sequence():
    frame = np.full((64, 64, 3), 100, dtype=np.uint8)
    result = FrameComparator.compare_frames(
        [frame] * 10,
        [frame] * 10,
        threshold=0.5,
    )
    assert result["mean_ssim"] >= 0.9
    assert result["pass"] is True
    assert result["frame_count"] == 10


def test_compare_frames_different_lengths():
    frame_a = np.full((64, 64, 3), 100, dtype=np.uint8)
    frame_b = np.full((64, 64, 3), 200, dtype=np.uint8)
    result = FrameComparator.compare_frames(
        [frame_a] * 5,
        [frame_b] * 10,
        threshold=0.5,
    )
    assert result["frame_count"] == 5
    assert result["mean_ssim"] is not None
    assert result["mean_psnr"] is not None


def test_compare_frames_below_threshold():
    black = np.zeros((64, 64, 3), dtype=np.uint8)
    white = np.full((64, 64, 3), 255, dtype=np.uint8)
    result = FrameComparator.compare_frames(
        [black] * 3,
        [white] * 3,
        threshold=0.8,
    )
    assert result["pass"] is False


def test_compare_frames_single_element():
    a = np.full((64, 64, 3), 50, dtype=np.uint8)
    b = np.full((64, 64, 3), 50, dtype=np.uint8)
    result = FrameComparator.compare_frames([a], [b], threshold=0.99)
    assert result["mean_ssim"] >= 0.99
    assert result["pass"] is True


def test_compute_ssim_float_handling():
    a = np.full((32, 32, 3), 255, dtype=np.uint8)
    b = np.full((32, 32, 3), 0, dtype=np.uint8)
    val = FrameComparator.compute_ssim(a, b)
    assert isinstance(val, float)
    assert 0.0 <= val <= 1.0


def test_compute_psnr_zero_value_range():
    a = np.zeros((32, 32, 3), dtype=np.uint8)
    b = np.zeros((32, 32, 3), dtype=np.uint8)
    val = FrameComparator.compute_psnr(a, b)
    assert val == float("inf")


# ── GameGenerationCache ───────────────────────────────────────────────────


def test_game_generation_cache_hit():
    cache = GameGenerationCache()
    generator = GameGenerator(gateway=None)
    spec = GameSpec(
        name="cached",
        genre="fps",
        description="desc",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="prompt",
    )
    cache._generated = {("cached", "prompt", "sonnet", ()): "code_v1"}
    first = cache.generate(generator=generator, spec=spec, model_id="sonnet")
    assert first == "code_v1"
    assert cache.miss_count == 0
    second = cache.generate(generator=generator, spec=spec, model_id="sonnet")
    assert second == "code_v1"
    assert cache.miss_count == 0


def test_game_generation_cache_miss_primes_count():
    cache = GameGenerationCache()
    cache._cache.miss_count = 0
    generator = GameGenerator(gateway=None)
    spec_a = GameSpec(
        name="a",
        genre="fps",
        description="d",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="p1",
    )
    cache._generated = {("a", "p1", "m", ()): "result_a"}
    result = cache.generate(generator=generator, spec=spec_a, model_id="m")
    assert result == "result_a"
    assert cache.miss_count == 0


def test_game_generation_cache_different_model_ids_separate_keys():
    cache = GameGenerationCache()
    cache._generated = {
        ("multi", "p", "sonnet", ()): "v1",
        ("multi", "p", "opus", ()): "v2",
    }
    cache._cache.miss_count = 0
    generator = GameGenerator(gateway=None)
    spec = GameSpec(
        name="multi",
        genre="fps",
        description="d",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="p",
    )
    a = cache.generate(generator=generator, spec=spec, model_id="sonnet")
    b = cache.generate(generator=generator, spec=spec, model_id="opus")
    assert a == "v1"
    assert b == "v2"
    assert cache.miss_count == 0


def test_game_generation_cache_miss_count_setter():
    cache = GameGenerationCache()
    cache.miss_count = 42
    assert cache.miss_count == 42


def test_game_generation_cache_miss_count_getter_from_delegate():
    cache = GameGenerationCache()
    cache._cache.miss_count = 7
    assert cache.miss_count == 7


# ── _has_rendered_menu_state via validate_game_code ───────────────────────


def test_validate_game_code_pause_menu_state():
    spec = GameSpec(
        name="pause_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        requires_menu=True,
        required_controls=(),
    )
    code = """
import pygame
pygame.init()
state = "paused"
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    if state == "paused":
        pass
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_main_menu_state():
    spec = GameSpec(
        name="main_menu_game",
        genre="fps",
        description="A game",
        expected_frames=30,
        similarity_threshold=0.5,
        prompt_template="Write",
        requires_menu=True,
        required_controls=(),
    )
    code = """
import pygame
pygame.init()
state = "main_menu"
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    if state == "main_menu":
        pass
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, spec) is True


def test_validate_game_code_with_spec_none_skips_menu_check():
    code = """
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
    assert GameGenerator.validate_game_code(code, None) is True


# ── GameRunner (smoke test — no pygame required) ──────────────────────────


def test_game_runner_constructor():
    runner = GameRunner()
    assert runner._processes == []
    assert runner.last_injected_controls == set()


# ── structural_similarity detection ───────────────────────────────────────


def test_structural_similarity_type():
    if _HAS_SKIMAGE:
        assert callable(structural_similarity)
    else:
        assert structural_similarity is None
