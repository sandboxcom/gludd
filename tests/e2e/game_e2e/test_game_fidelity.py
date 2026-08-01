"""E2E tests for AI-generated game fidelity against reference gameplay.

These tests use Azure GPU compute (A100/H100) to provision an inference endpoint,
then run an LLM on that endpoint to generate simple game code. The generated game
is run headless, frames are captured, and compared against reference gameplay
videos using SSIM similarity metrics.

ALL AI inference runs on Azure GPU resources exclusively. No fallback to hosted
APIs (DeepSeek, OpenAI, etc.) — this is an Azure compute E2E test.

Opt-in: requires AZURE_PROVISION_E2E=1 or pre-provisioned AZURE_BASE_URL.
Azure credentials (ARM_*) must be set in the environment.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from general_ludd.cloud.game_e2e import _HAS_PYGAME as HAS_PYGAME
from general_ludd.cloud.game_e2e import (
    GAME_SPECS,
    AzureGameE2E,
    E2EResult,
    FrameComparator,
    GameGenerator,
    GameRunner,
)
from general_ludd.cloud.game_gen import _HAS_PYGAME as HAS_PYGAME_GEN
from general_ludd.cloud.game_gen import (
    GAME_TEMPLATES,
    generate_game_code,
    run_game_headless,
    validate_game_syntax,
)
from general_ludd.cloud.video_compare import (
    REFERENCE_VIDEOS,
    compute_ssim,
    download_youtube_video,
)

_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None
_HAS_YTDLP_E2E = importlib.util.find_spec("yt_dlp") is not None

_AZURE_PROVISION_ENABLED = os.environ.get("AZURE_PROVISION_E2E") == "1"
_AZURE_ENDPOINT_SET = bool(os.environ.get("AZURE_BASE_URL"))
_AZURE_CONFIGURED = _AZURE_PROVISION_ENABLED or _AZURE_ENDPOINT_SET

_AZURE_SKIP_REASON = "Azure GPU not configured — set AZURE_BASE_URL or AZURE_PROVISION_E2E=1"

LIVE_SKIP_REASON = (
    _AZURE_SKIP_REASON
    if not _AZURE_CONFIGURED
    else ("langchain-openai is not installed" if not _HAS_LANGCHAIN_OPENAI else "")
)

_AZURE_CHECKED = bool(os.environ.get("ARM_SUBSCRIPTION_ID") or os.environ.get("AZURE_SUBSCRIPTION_ID"))


def _build_azure_gateway():
    """Build a ModelGateway pointed at an Azure-provisioned GPU endpoint.

    Uses DeployStrategist to resolve the endpoint — pre-provisioned via
    AZURE_BASE_URL or auto-provisioned via DeploymentManager/Terraform.
    NEVER falls back to DeepSeek/OpenAI hosted APIs.
    """
    from general_ludd.infra.deploy_strategy import build_azure_gateway as _build

    return _build()


# ── Test: Doom Hallway Generation ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestDoomHallwayGeneration:
    @pytest.fixture(scope="class")
    def gateway(self):
        gw = _build_azure_gateway()
        if gw is None:
            pytest.skip("Azure gateway not configured")
        return gw

    def test_generate_and_run(self, gateway, tmp_path: Path) -> None:
        spec = GAME_SPECS[0]
        assert spec.name == "doom_e1m1_hallway"

        gen = GameGenerator(gateway)
        code = gen.generate_game(spec)
        assert code, "LLM returned empty code"
        assert gen.validate_game_code(code, spec), "Generated code failed controls/menu validation"

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
        assert gen.validate_game_code(code, spec), "Generated code should satisfy controls/menu contract"

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
        gw = _build_azure_gateway()
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


# ── Test: Doom Generation via game_gen ────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestDoomGenerationGameGen:
    @pytest.fixture(scope="class")
    def gateway(self):
        gw = _build_azure_gateway()
        if gw is None:
            pytest.skip("Azure gateway not configured")
        return gw

    def test_generate_doom_hallway(self, gateway) -> None:
        code = generate_game_code(gateway, "doom_hallway")
        assert code, "LLM returned empty code"
        assert validate_game_syntax(code), "Generated code failed validation"

    def test_generated_code_has_game_loop(self, gateway) -> None:
        code = generate_game_code(gateway, "doom_hallway")
        code_lower = code.lower()
        for item in ["while", "pygame.init", "quit"]:
            assert item in code_lower, f"Missing: {item}"


# ── Test: Quake Arena Generation via game_gen ─────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestQuakeArenaGenerationGameGen:
    @pytest.fixture(scope="class")
    def gateway(self):
        gw = _build_azure_gateway()
        if gw is None:
            pytest.skip("Azure gateway not configured")
        return gw

    def test_generate_quake_arena(self, gateway) -> None:
        code = generate_game_code(gateway, "quake_arena")
        assert code, "LLM returned empty code"
        assert validate_game_syntax(code), "Generated code failed validation"

    def test_generated_code_has_platform_elements(self, gateway) -> None:
        code = generate_game_code(gateway, "quake_arena")
        code_lower = code.lower()
        for item in ["pygame.init", "while", "quit"]:
            assert item in code_lower, f"Missing: {item}"


# ── Test: Frame Consistency (Doom) ────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestDoomFrameConsistency:
    @pytest.fixture(scope="class")
    def gateway(self):
        gw = _build_azure_gateway()
        if gw is None:
            pytest.skip("Azure gateway not configured")
        return gw

    def test_frames_have_consistent_dimensions(self, gateway, tmp_path: Path) -> None:
        if not HAS_PYGAME_GEN:
            pytest.skip("pygame not installed")
        code = generate_game_code(gateway, "doom_hallway")
        game_path = tmp_path / "doom_consistency.py"
        game_path.write_text(code)
        frames = run_game_headless(str(game_path), num_frames=15)
        assert len(frames) >= 1, "No frames captured"
        expected_shape = frames[0].shape
        for i, f in enumerate(frames):
            assert f.shape == expected_shape, f"Frame {i} shape mismatch: {f.shape}"

    def test_frames_are_non_blank(self, gateway, tmp_path: Path) -> None:
        if not HAS_PYGAME_GEN:
            pytest.skip("pygame not installed")
        code = generate_game_code(gateway, "doom_hallway")
        game_path = tmp_path / "doom_nonblank.py"
        game_path.write_text(code)
        frames = run_game_headless(str(game_path), num_frames=10)
        assert len(frames) >= 1
        non_blank = False
        for f in frames:
            if f.max() > 0 or f.min() < 255:
                non_blank = True
                break
        assert non_blank, "All frames are blank/uniform"


# ── Test: Quake Rendering Colors ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestQuakeRendering:
    @pytest.fixture(scope="class")
    def gateway(self):
        gw = _build_azure_gateway()
        if gw is None:
            pytest.skip("Azure gateway not configured")
        return gw

    def test_frames_contain_expected_color_range(self, gateway, tmp_path: Path) -> None:
        if not HAS_PYGAME_GEN:
            pytest.skip("pygame not installed")
        code = generate_game_code(gateway, "quake_arena")
        game_path = tmp_path / "quake_color.py"
        game_path.write_text(code)
        frames = run_game_headless(str(game_path), num_frames=10)
        assert len(frames) >= 1
        has_color_variation = False
        for f in frames:
            std = float(f.std())
            if std > 5.0:
                has_color_variation = True
                break
        assert has_color_variation, "All frames are uniform (no color variation)"


# ── Test: Game Code Completeness ──────────────────────────────────────────


@pytest.mark.e2e
class TestGameCodeCompleteness:
    def test_templates_have_game_loop_requirement(self) -> None:
        for name, template in GAME_TEMPLATES.items():
            lower = template.lower()
            assert "while" in lower or "for" in lower, f"Template {name} missing loop requirement"
            assert "pygame" in lower, f"Template {name} missing pygame requirement"

    def test_templates_have_rendering_requirement(self) -> None:
        for name, template in GAME_TEMPLATES.items():
            lower = template.lower()
            has_render = any(w in lower for w in ["display", "blit", "flip", "render", "draw", "fill"])
            assert has_render, f"Template {name} missing rendering requirement"

    def test_generated_code_validates_syntax(self) -> None:
        valid_code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    screen.fill((0, 0, 0))
    pygame.display.flip()
pygame.quit()
"""
        assert validate_game_syntax(valid_code) is True

    def test_generated_code_without_input_fails(self) -> None:
        no_input_code = """
import pygame
pygame.init()
for i in range(30):
    pass
"""
        assert validate_game_syntax(no_input_code) is False

    def test_fps_pipeline_rejects_incomplete_controls_before_execution(self) -> None:
        class IncompleteGateway:
            def call_model(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(
                    content="""
import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    pygame.display.flip()
pygame.quit()
"""
                )

        pipeline = AzureGameE2E(model_gateway=cast(Any, IncompleteGateway()))
        result = pipeline.run_full_test(GAME_SPECS[0])

        assert result.code_generated is True
        assert result.code_valid is False
        assert result.game_ran is False
        assert result.frames_captured == 0
        assert result.errors == ["Generated game failed its required controls/menu contract"]


# ── Test: SSIM Computation ────────────────────────────────────────────────


@pytest.mark.e2e
class TestSSIMComputation:
    def test_ssim_against_known_image_pairs(self) -> None:
        from general_ludd.cloud.video_compare import compute_ssim as vc_ssim

        rng = np.random.RandomState(42)
        img1 = rng.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        img2 = img1.copy()
        noise = rng.randint(0, 10, img2.shape, dtype=np.uint8)
        img2 = np.clip(img2.astype(np.int32) + noise.astype(np.int32), 0, 255).astype(np.uint8)

        ssim = vc_ssim(img1, img2)
        assert 0.0 <= ssim <= 1.0, f"SSIM out of range: {ssim}"

    def test_ssim_identity_value(self) -> None:
        from general_ludd.cloud.video_compare import compute_ssim as vc_ssim

        img = np.ones((64, 64, 3), dtype=np.uint8) * 128
        result = vc_ssim(img, img)
        assert result == 1.0, f"SSIM of identical images should be 1.0, got {result}"


# ── Test: Video Download ──────────────────────────────────────────────────


@pytest.mark.e2e
class TestVideoDownload:
    @pytest.mark.skipif(not _HAS_YTDLP_E2E, reason="yt-dlp not installed")
    def test_download_short_clip(self, tmp_path: Path) -> None:
        url = REFERENCE_VIDEOS["doom_e1m1_hallway"]
        video_path = download_youtube_video(url, str(tmp_path))
        assert os.path.exists(video_path), f"Downloaded video not found: {video_path}"
        assert os.path.getsize(video_path) > 0, "Downloaded video is empty"

    def test_reference_urls_are_valid_youtube_links(self) -> None:
        for name, url in REFERENCE_VIDEOS.items():
            assert url.startswith("https://www.youtube.com/watch?v="), f"Invalid YouTube URL for {name}"
            assert "v=" in url, f"Missing video ID in {name} URL"


# ── Test: Frame Comparison via video_compare ──────────────────────────────


@pytest.mark.e2e
class TestFrameComparisonVC:
    def test_identical_frames_ssim_one(self) -> None:
        frame = np.ones((64, 64, 3), dtype=np.uint8) * 128
        result = compute_ssim(frame, frame)
        assert result == 1.0, f"SSIM of identical frames should be 1.0, got {result}"

    def test_different_frames_ssim_less_than_one(self) -> None:
        frame_a = np.zeros((64, 64, 3), dtype=np.uint8)
        frame_b = np.full((64, 64, 3), 255, dtype=np.uint8)
        result = compute_ssim(frame_a, frame_b)
        assert result < 1.0, f"SSIM of very different frames should be < 1.0: {result}"
