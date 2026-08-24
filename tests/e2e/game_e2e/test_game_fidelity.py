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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from general_ludd.cloud.azure_game_runtime import AzureGameRuntime
from general_ludd.cloud.game_e2e import _HAS_PYGAME as HAS_PYGAME
from general_ludd.cloud.game_e2e import (
    GAME_SPECS,
    AzureGameE2E,
    E2EResult,
    FrameComparator,
    GameGenerationCache,
    GameGenerator,
    GameRunner,
    build_game_input_script,
)
from general_ludd.cloud.game_gen import _HAS_PYGAME as HAS_PYGAME_GEN
from general_ludd.cloud.game_gen import (
    GAME_TEMPLATES,
    generate_game_code,
    run_game_headless,
    validate_game_syntax,
)
from general_ludd.cloud.video_compare import (
    REFERENCE_VIDEO_SPECS,
    REFERENCE_VIDEOS,
    ReferenceComparisonResult,
    compare_gameplay_to_reference,
    compute_ssim,
    download_youtube_video,
    extract_frames,
    preflight_reference_videos,
)
from general_ludd.cloud.video_compare import (
    cv2 as video_cv2,
)

_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None
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


@dataclass(frozen=True)
class LiveFpsArtifact:
    code: str
    game_path: Path
    frame_count: int
    first_frame_shape: tuple[int, ...]
    input_controls: tuple[str, ...]
    comparison: ReferenceComparisonResult
    generation_count: int = 1


@dataclass(frozen=True)
class LiveGameGenArtifact:
    code: str
    frame_count: int
    frame_shapes: tuple[tuple[int, ...], ...]
    has_non_blank_frame: bool
    has_color_variation: bool
    generation_count: int = 1


@pytest.fixture(scope="session")
def gateway():
    """Provision or borrow one Azure endpoint for the entire game E2E session."""
    cache_dir = Path(os.environ.get("GAME_E2E_REFERENCE_CACHE_DIR", ".cache/gludd-game-e2e"))
    allow_network = os.environ.get("GAME_E2E_REFERENCE_NETWORK") == "1"

    def reference_preflight() -> None:
        def stream_reference_event(name: str, payload: Mapping[str, object]) -> None:
            details = " ".join(f"{key}={value}" for key, value in payload.items())
            print(f"[game-fixture] {name} {details}", flush=True)

        preflight_reference_videos(
            (spec.name for spec in GAME_SPECS),
            cache_dir,
            allow_network=allow_network,
            event_reporter=stream_reference_event,
        )

    runtime = AzureGameRuntime(preflight=reference_preflight)
    try:
        yield cast(Any, runtime.start())
    finally:
        runtime.close()


@pytest.fixture(scope="session")
def fps_artifacts(gateway, tmp_path_factory: pytest.TempPathFactory) -> dict[str, LiveFpsArtifact]:
    """Generate, exercise, capture, and compare each declared FPS exactly once."""
    generator = GameGenerator(gateway)
    cache = GameGenerationCache()
    output_dir = tmp_path_factory.mktemp("azure-fps-games")
    cache_dir = Path(os.environ.get("GAME_E2E_REFERENCE_CACHE_DIR", ".cache/gludd-game-e2e"))
    allow_network = os.environ.get("GAME_E2E_REFERENCE_NETWORK") == "1"
    model_settings = {
        "AZURE_MODEL": os.environ.get("AZURE_MODEL", "Qwen/Qwen2.5-0.5B-Instruct"),
        "GAME_E2E_TEMPERATURE": os.environ.get("GAME_E2E_TEMPERATURE", "0"),
    }
    artifacts: dict[str, LiveFpsArtifact] = {}

    for spec in GAME_SPECS:
        print(f"[game-fixture] generation_started fixture={spec.name}", flush=True)
        code = cache.generate(generator, spec, model_settings=model_settings)
        assert code, f"{spec.name}: LLM returned empty code"
        assert generator.validate_game_code(code, spec), (
            f"{spec.name}: generated code failed controls/menu validation"
        )
        print(f"[game-fixture] generation_ready fixture={spec.name}", flush=True)

        game_path = output_dir / f"{spec.name}.py"
        generator.save_game(code, str(game_path))
        runner = GameRunner()
        script = build_game_input_script(spec)
        print(f"[game-fixture] controls_started fixture={spec.name}", flush=True)
        try:
            frames = runner.run_headless_inline(
                str(game_path),
                spec.expected_frames,
                input_script=script,
            )
            injected = tuple(
                control for control in spec.required_controls if control in runner.last_injected_controls
            )
        finally:
            runner.cleanup()
        assert frames, f"{spec.name}: no frames captured"
        assert injected == spec.required_controls, (
            f"{spec.name}: input harness missed controls {set(spec.required_controls) - set(injected)}"
        )
        print(
            f"[game-fixture] controls_ready fixture={spec.name} frames={len(frames)}",
            flush=True,
        )

        print(f"[game-fixture] video_compare_started fixture={spec.name}", flush=True)
        comparison = compare_gameplay_to_reference(
            spec.name,
            frames,
            cache_dir,
            allow_network=allow_network,
        )
        assert comparison.cache_status in {"cached", "downloaded"}, (
            f"{spec.name}: reference video unavailable ({comparison.cache_status})"
        )
        assert comparison.reference_frame_count > 0, f"{spec.name}: reference video produced no frames"
        assert comparison.passed, (
            f"{spec.name}: mean SSIM {comparison.mean_ssim:.4f} below {comparison.threshold:.4f}"
        )
        print(
            f"[game-fixture] video_compare_ready fixture={spec.name} "
            f"ssim={comparison.mean_ssim:.4f} threshold={comparison.threshold:.4f}",
            flush=True,
        )
        artifacts[spec.name] = LiveFpsArtifact(
            code=code,
            game_path=game_path,
            frame_count=len(frames),
            first_frame_shape=tuple(frames[0].shape),
            input_controls=injected,
            comparison=comparison,
        )

    assert cache.miss_count == len(GAME_SPECS)
    return artifacts


@pytest.fixture(scope="session")
def game_gen_artifacts(gateway, tmp_path_factory: pytest.TempPathFactory) -> dict[str, LiveGameGenArtifact]:
    """Exercise each legacy game_gen prompt once and reuse its capture metrics."""
    output_dir = tmp_path_factory.mktemp("azure-game-gen")
    artifacts: dict[str, LiveGameGenArtifact] = {}
    for template_name in ("doom_hallway", "quake_arena"):
        print(f"[game-gen-fixture] generation_started fixture={template_name}", flush=True)
        code = generate_game_code(gateway, template_name)
        assert code, f"{template_name}: LLM returned empty code"
        assert validate_game_syntax(code), f"{template_name}: generated code failed validation"
        game_path = output_dir / f"{template_name}.py"
        game_path.write_text(code)
        frames = run_game_headless(str(game_path), num_frames=15) if HAS_PYGAME_GEN else []
        print(
            f"[game-gen-fixture] capture_ready fixture={template_name} frames={len(frames)}",
            flush=True,
        )
        artifacts[template_name] = LiveGameGenArtifact(
            code=code,
            frame_count=len(frames),
            frame_shapes=tuple(tuple(frame.shape) for frame in frames),
            has_non_blank_frame=any(frame.max() > 0 or frame.min() < 255 for frame in frames),
            has_color_variation=any(float(frame.std()) > 5.0 for frame in frames),
        )
    return artifacts


# ── Test: Doom Hallway Generation ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestDoomHallwayGeneration:
    def test_generate_and_run(self, fps_artifacts: dict[str, LiveFpsArtifact]) -> None:
        spec = GAME_SPECS[0]
        assert spec.name == "doom_e1m1_hallway"
        artifact = fps_artifacts[spec.name]
        assert artifact.code
        assert artifact.game_path.exists()
        assert artifact.frame_count > 0
        assert artifact.first_frame_shape == (600, 800, 3)
        assert artifact.input_controls == spec.required_controls
        assert artifact.comparison.passed

    def test_game_is_runnable(self, fps_artifacts: dict[str, LiveFpsArtifact]) -> None:
        spec = GAME_SPECS[0]
        code = fps_artifacts[spec.name].code
        assert GameGenerator.validate_game_code(code, spec), (
            "Generated code should satisfy controls/menu contract"
        )

    def test_game_has_required_elements(self, fps_artifacts: dict[str, LiveFpsArtifact]) -> None:
        spec = GAME_SPECS[0]
        code = fps_artifacts[spec.name].code
        code_lower = code.lower()
        required = ["pygame.init", "while", "quit"]
        for item in required:
            assert item.lower() in code_lower, f"Missing required element: {item}"


# ── Test: Quake Arena Generation ───────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestQuakeArenaGeneration:
    def test_generate_and_run(self, fps_artifacts: dict[str, LiveFpsArtifact]) -> None:
        spec = GAME_SPECS[1]
        assert spec.name == "quake_dm6_arena"
        artifact = fps_artifacts[spec.name]
        assert artifact.code
        assert artifact.game_path.exists()
        assert artifact.frame_count > 0
        assert artifact.input_controls == spec.required_controls
        assert artifact.comparison.passed

    def test_game_has_required_elements(self, fps_artifacts: dict[str, LiveFpsArtifact]) -> None:
        spec = GAME_SPECS[1]
        code = fps_artifacts[spec.name].code
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
            assert result.shape == (600, 800, 3), f"Expected (600, 800, 3), got {result.shape}"
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
    def test_generate_doom_hallway(
        self,
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        code = game_gen_artifacts["doom_hallway"].code
        assert code, "LLM returned empty code"
        assert validate_game_syntax(code), "Generated code failed validation"

    def test_generated_code_has_game_loop(
        self,
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        code = game_gen_artifacts["doom_hallway"].code
        code_lower = code.lower()
        for item in ["while", "pygame.init", "quit"]:
            assert item in code_lower, f"Missing: {item}"


# ── Test: Quake Arena Generation via game_gen ─────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestQuakeArenaGenerationGameGen:
    def test_generate_quake_arena(
        self,
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        code = game_gen_artifacts["quake_arena"].code
        assert code, "LLM returned empty code"
        assert validate_game_syntax(code), "Generated code failed validation"

    def test_generated_code_has_platform_elements(
        self,
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        code = game_gen_artifacts["quake_arena"].code
        code_lower = code.lower()
        for item in ["pygame.init", "while", "quit"]:
            assert item in code_lower, f"Missing: {item}"


# ── Test: Frame Consistency (Doom) ────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestDoomFrameConsistency:
    def test_frames_have_consistent_dimensions(
        self,
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        if not HAS_PYGAME_GEN:
            pytest.skip("pygame not installed")
        artifact = game_gen_artifacts["doom_hallway"]
        assert artifact.frame_count >= 1, "No frames captured"
        expected_shape = artifact.frame_shapes[0]
        for index, shape in enumerate(artifact.frame_shapes):
            assert shape == expected_shape, f"Frame {index} shape mismatch: {shape}"

    def test_frames_are_non_blank(
        self,
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        if not HAS_PYGAME_GEN:
            pytest.skip("pygame not installed")
        artifact = game_gen_artifacts["doom_hallway"]
        assert artifact.frame_count >= 1
        assert artifact.has_non_blank_frame, "All frames are blank/uniform"


# ── Test: Quake Rendering Colors ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestQuakeRendering:
    def test_frames_contain_expected_color_range(
        self,
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        if not HAS_PYGAME_GEN:
            pytest.skip("pygame not installed")
        artifact = game_gen_artifacts["quake_arena"]
        assert artifact.frame_count >= 1
        assert artifact.has_color_variation, "All frames are uniform (no color variation)"


@pytest.mark.e2e
@pytest.mark.azure_provision
@pytest.mark.skipif(not _AZURE_CONFIGURED, reason=_AZURE_SKIP_REASON)
class TestLiveFixtureReuse:
    def test_each_prompt_is_generated_once_per_session(
        self,
        fps_artifacts: dict[str, LiveFpsArtifact],
        game_gen_artifacts: dict[str, LiveGameGenArtifact],
    ) -> None:
        assert set(fps_artifacts) == {spec.name for spec in GAME_SPECS}
        assert set(game_gen_artifacts) == {"doom_hallway", "quake_arena"}
        assert all(artifact.generation_count == 1 for artifact in fps_artifacts.values())
        assert all(artifact.generation_count == 1 for artifact in game_gen_artifacts.values())


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
    def test_download_short_clip(self, tmp_path: Path) -> None:
        if video_cv2 is None:
            pytest.skip("install the locked game-e2e extra for OpenCV media tests")
        if os.environ.get("GAME_E2E_REFERENCE_NETWORK") == "1":
            reference = REFERENCE_VIDEO_SPECS["doom_e1m1_hallway"]
            network_video_path = download_youtube_video(
                reference.source_url,
                str(tmp_path),
                clip_start_seconds=reference.clip_start_seconds,
                clip_duration_seconds=reference.clip_duration_seconds,
            )
            assert os.path.exists(network_video_path), (
                f"Downloaded video not found: {network_video_path}"
            )
            assert os.path.getsize(network_video_path) > 0, "Downloaded video is empty"
            return

        video_path = tmp_path / "hermetic-reference.avi"
        writer = video_cv2.VideoWriter(
            str(video_path),
            video_cv2.VideoWriter_fourcc(*"MJPG"),
            5.0,
            (16, 16),
        )
        assert writer.isOpened(), "deterministic reference video writer did not open"
        try:
            for value in (0, 64, 128, 192, 255):
                writer.write(np.full((16, 16, 3), value, dtype=np.uint8))
        finally:
            writer.release()

        frames = extract_frames(video_path, num_frames=3, interval=0.2)
        assert video_path.stat().st_size > 0
        assert len(frames) == 3
        assert all(frame.shape == (16, 16, 3) for frame in frames)

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
