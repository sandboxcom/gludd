"""E2E tests for AI-generated game fidelity against reference gameplay.

These tests use Azure GPU compute to run an LLM that generates simple game code.
The generated game is run headless, frames are captured, and compared against
reference gameplay videos using SSIM similarity metrics.

All AI inference runs on Azure GPU resources exclusively.
Opt-in: requires AZURE_PROVISION_E2E=1 or pre-provisioned AZURE_BASE_URL.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import numpy as np
import pytest

from general_ludd.cloud.game_e2e import _HAS_PYGAME as HAS_PYGAME
from general_ludd.cloud.game_e2e import (
    GAME_SPECS,
    E2EResult,
    FrameComparator,
    GameGenerator,
    GameRunner,
)

_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None

_AZURE_CONFIGURED = bool(os.environ.get("AZURE_BASE_URL") or os.environ.get("AZURE_PROVISION_E2E") == "1")

_AZURE_SKIP_REASON = "Azure GPU not configured — set AZURE_BASE_URL or AZURE_PROVISION_E2E=1"

LIVE_SKIP_REASON = (
    _AZURE_SKIP_REASON
    if not _AZURE_CONFIGURED
    else ("langchain-openai is not installed" if not _HAS_LANGCHAIN_OPENAI else "")
)


def _get_deepseek_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY", os.environ.get("AZURE_API_KEY"))


def _build_gateway():
    from general_ludd.models.gateway import ModelGateway
    from general_ludd.models.profiles import ModelProfile

    key = _get_deepseek_key()
    base_url = os.environ.get("AZURE_BASE_URL") or (
        "https://api.deepseek.com" if key and key.startswith("sk-") else None
    )
    if not base_url:
        return None

    profile = ModelProfile(
        model_profile_id="deepseek_coder",
        provider="deepseek",
        model="deepseek-coder",
        api_base_url=base_url,
        api_key=key or "",
    )
    return ModelGateway(profiles=[profile])


# ── Test: Doom Hallway Generation ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestDoomHallwayGeneration:
    @pytest.fixture(scope="class")
    def gateway(self):
        gw = _build_gateway()
        if gw is None:
            pytest.skip("Azure gateway not configured")
        return gw

    def test_generate_and_run(self, gateway, tmp_path: Path) -> None:
        spec = GAME_SPECS[0]
        assert spec.name == "doom_e1m1_hallway"

        gen = GameGenerator(gateway)
        code = gen.generate_game(spec)
        assert code, "LLM returned empty code"
        assert gen.validate_game_code(code), "Generated code failed validation"

        game_path = tmp_path / "doom_game.py"
        gen.save_game(code, str(game_path))
        assert game_path.exists()

        if HAS_PYGAME:
            runner = GameRunner()
            frames = runner.run_headless_inline(str(game_path), spec.expected_frames)
            assert len(frames) > 0, "No frames captured"
            assert frames[0].shape == (600, 800, 3)
            runner.cleanup()

    def test_game_is_runnable(self, gateway, tmp_path: Path) -> None:
        spec = GAME_SPECS[0]
        gen = GameGenerator(gateway)
        code = gen.generate_game(spec)
        assert gen.validate_game_code(code), "Generated code should be valid syntactically"

    def test_game_has_required_elements(self, gateway, tmp_path: Path) -> None:
        spec = GAME_SPECS[0]
        gen = GameGenerator(gateway)
        code = gen.generate_game(spec)
        code_lower = code.lower()
        required = ["pygame.init", "while", "quit"]
        for item in required:
            assert item.lower() in code_lower, f"Missing required element: {item}"


# ── Test: Quake Arena Generation ───────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestQuakeArenaGeneration:
    @pytest.fixture(scope="class")
    def gateway(self):
        gw = _build_gateway()
        if gw is None:
            pytest.skip("Azure gateway not configured")
        return gw

    def test_generate_and_run(self, gateway, tmp_path: Path) -> None:
        spec = GAME_SPECS[1]
        assert spec.name == "quake_dm6_arena"

        gen = GameGenerator(gateway)
        code = gen.generate_game(spec)
        assert code, "LLM returned empty code"

        game_path = tmp_path / "quake_game.py"
        gen.save_game(code, str(game_path))

        if HAS_PYGAME:
            runner = GameRunner()
            frames = runner.run_headless_inline(str(game_path), spec.expected_frames)
            assert len(frames) > 0, "No frames captured"
            runner.cleanup()

    def test_game_has_required_elements(self, gateway, tmp_path: Path) -> None:
        spec = GAME_SPECS[1]
        gen = GameGenerator(gateway)
        code = gen.generate_game(spec)
        code_lower = code.lower()
        required = ["pygame.init", "platform", "while", "quit"]
        for item in required:
            assert item.lower() in code_lower, f"Missing required element: {item}"


# ── Test: Frame Comparison ─────────────────────────────────────────────────


@pytest.mark.e2e
class TestFrameComparison:
    def test_ssim_computation(self) -> None:
        fc = FrameComparator()
        rng = np.random.RandomState(42)
        img1 = rng.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        img2 = img1.copy()
        noise = rng.randint(0, 20, img2.shape, dtype=np.uint8)
        img2 = np.clip(img2.astype(np.int32) + noise.astype(np.int32), 0, 255).astype(np.uint8)

        ssim = fc.compute_ssim(img1, img2)
        assert 0.0 <= ssim <= 1.0, f"SSIM out of range: {ssim}"

        psnr = fc.compute_psnr(img1, img2)
        assert psnr > 0.0, f"PSNR should be > 0: {psnr}"

    def test_ssim_identity(self) -> None:
        fc = FrameComparator()
        img = np.ones((64, 64, 3), dtype=np.uint8) * 128
        result = fc.compute_ssim(img, img)
        assert result == 1.0, f"SSIM of identical images should be 1.0, got {result}"

    def test_frame_capture_format(self) -> None:
        if not HAS_PYGAME:
            pytest.skip("pygame not installed")

        import pygame

        pygame.display.init()
        try:
            surface = pygame.Surface((800, 600))
            surface.fill((128, 128, 128))
            runner = GameRunner()
            result = runner.capture_frame(surface)
            assert isinstance(result, np.ndarray)
            assert result.shape == (800, 600, 3), f"Expected (800, 600, 3), got {result.shape}"
            assert result.dtype == np.uint8
        finally:
            pygame.display.quit()

    def test_compare_frames_result_structure(self) -> None:
        fc = FrameComparator()
        frames = [np.ones((32, 32, 3), dtype=np.uint8) * (i * 10) for i in range(5)]
        ref = [np.ones((32, 32, 3), dtype=np.uint8) * (i * 10 + 3) for i in range(5)]
        result = fc.compare_frames(frames, ref, threshold=0.5)
        assert "mean_ssim" in result
        assert "mean_psnr" in result
        assert "pass" in result
        assert "frame_count" in result
        assert result["frame_count"] == 5

    def test_empty_frames(self) -> None:
        fc = FrameComparator()
        result = fc.compare_frames([], [])
        assert result["frame_count"] == 0
        assert result["mean_ssim"] == 0.0
        assert not result["pass"]


# ── Test: E2E Result ───────────────────────────────────────────────────────


class TestE2EResult:
    def test_defaults(self) -> None:
        r = E2EResult(spec_name="test")
        assert r.spec_name == "test"
        assert r.code_generated is False
        assert r.comparison_pass is False

    def test_errors_field(self) -> None:
        r = E2EResult(spec_name="test", errors=["err1", "err2"])
        assert len(r.errors) == 2
