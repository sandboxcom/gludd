"""Unit tests for game_e2e module — cloud/game_e2e.py."""

from __future__ import annotations

import numpy as np
import pytest

from general_ludd.cloud.game_e2e import (
    GAME_SPECS,
    FrameComparator,
    GameGenerator,
    GameRunner,
    GameSpec,
)


class TestGameSpec:
    def test_instantiation(self) -> None:
        spec = GameSpec(
            name="test_game",
            genre="fps",
            description="A test game",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        assert spec.name == "test_game"
        assert spec.genre == "fps"
        assert spec.expected_frames == 30
        assert spec.similarity_threshold == 0.35
        assert spec.reference_video_url is None

    def test_predefined_specs_exist(self) -> None:
        assert len(GAME_SPECS) >= 2
        names = {s.name for s in GAME_SPECS}
        assert "doom_e1m1_hallway" in names
        assert "quake_dm6_arena" in names

    def test_all_specs_have_prompt_template(self) -> None:
        for spec in GAME_SPECS:
            assert spec.prompt_template, f"{spec.name} missing prompt_template"


class TestFrameComparatorUnit:
    def test_ssim_identical_images(self) -> None:
        """SSIM of identical images should be ~1.0."""
        fc = FrameComparator()
        img = np.ones((64, 64, 3), dtype=np.float64) * 128
        result = fc.compute_ssim(img, img)
        assert 0.95 <= result <= 1.0, f"SSIM={result}"

    def test_ssim_different_images(self) -> None:
        """SSIM of very different images should be lower."""
        fc = FrameComparator()
        img1 = np.zeros((64, 64, 3), dtype=np.float64)
        img2 = np.ones((64, 64, 3), dtype=np.float64) * 255
        result = fc.compute_ssim(img1, img2)
        assert result < 0.5, f"SSIM={result}"

    def test_psnr_identical_images(self) -> None:
        """PSNR of identical images should be very high."""
        fc = FrameComparator()
        img = np.ones((64, 64, 3), dtype=np.float64) * 128
        result = fc.compute_psnr(img, img)
        assert result > 50, f"PSNR={result}"

    def test_compare_frames_structure(self) -> None:
        fc = FrameComparator()
        frames = [np.ones((64, 64, 3), dtype=np.float64) * (i * 10) for i in range(10)]
        ref = [np.ones((64, 64, 3), dtype=np.float64) * (i * 10 + 5) for i in range(10)]
        result = fc.compare_frames(frames, ref)
        assert "mean_ssim" in result
        assert "mean_psnr" in result
        assert isinstance(result["mean_ssim"], float)
        assert isinstance(result["mean_psnr"], float)

    def test_compare_frames_empty(self) -> None:
        fc = FrameComparator()
        result = fc.compare_frames([], [])
        assert result["mean_ssim"] == 0.0
        assert result["mean_psnr"] == 0.0


class TestGameGeneratorUnit:
    def test_validate_valid_code(self) -> None:
        gen = GameGenerator(None)  # type: ignore[arg-type]
        code = """
import pygame
import numpy as np
pygame.init()
screen = pygame.display.set_mode((800, 600))
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""
        assert gen.validate_game_code(code) is True

    def test_validate_invalid_code(self) -> None:
        gen = GameGenerator(None)  # type: ignore[arg-type]
        assert gen.validate_game_code("this is not python {{{") is False

    def test_validate_missing_loop(self) -> None:
        gen = GameGenerator(None)  # type: ignore[arg-type]
        code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
pygame.quit()
"""
        assert gen.validate_game_code(code) is False


class TestGameRunnerUnit:
    def test_runner_importable(self) -> None:
        assert GameRunner is not None

    def test_capture_frame_type(self) -> None:
        try:
            import pygame
        except ImportError:
            pytest.skip("pygame not installed")
        pygame.display.init()
        surface = pygame.Surface((64, 64))
        surface.fill((255, 0, 0))
        runner = GameRunner()
        result = runner.capture_frame(surface)
        assert isinstance(result, np.ndarray)
        assert result.shape == (64, 64, 3)
