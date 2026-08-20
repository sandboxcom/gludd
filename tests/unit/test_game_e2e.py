"""Unit tests for game_e2e module — cloud/game_e2e.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

import general_ludd.cloud.game_e2e as game_e2e
from general_ludd.cloud.game_e2e import (
    GAME_SPECS,
    AzureGameE2E,
    DeploymentConfig,
    FrameComparator,
    GameGenerationCache,
    GameGenerator,
    GameInputEvent,
    GameRunner,
    GameSpec,
    build_game_input_script,
)
from general_ludd.cloud.video_compare import REFERENCE_VIDEO_SPECS, ReferenceComparisonResult


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

    def test_fps_specs_require_complete_controls_and_menu(self) -> None:
        for spec in GAME_SPECS:
            if spec.genre != "fps":
                continue
            assert spec.requires_menu is True
            assert {"w", "a", "s", "d", "mouse", "escape", "return"} <= set(spec.required_controls)
            prompt = spec.prompt_template.lower()
            assert "menu" in prompt
            assert "w/a/s/d" in prompt
            assert "mouse" in prompt
            assert "return" in prompt
            assert "escape" in prompt

    def test_fps_specs_use_the_reference_video_manifest(self) -> None:
        for spec in GAME_SPECS:
            if spec.genre != "fps":
                continue
            source = REFERENCE_VIDEO_SPECS[spec.name]
            assert spec.reference_video_url == source.source_url
            assert spec.similarity_threshold == source.comparison_threshold


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
    def test_generate_game_normalizes_structured_provider_content(self) -> None:
        code = "import pygame\npygame.init()\nwhile True:\n    pygame.event.get()\n"
        blocks = [{"type": "text", "text": f"```python\n{code}```"}]

        class Gateway:
            def call_model(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(
                    content=str(blocks),
                    raw_response=SimpleNamespace(content=blocks),
                )

        generator = GameGenerator(cast(Any, Gateway()))

        assert generator.generate_game(GAME_SPECS[0]) == code.strip()

    def test_generate_game_owns_lifecycle_normalization(self) -> None:
        code = "class Snake:\n    def __init__(self):\n        self.state = 'ready'"

        class Gateway:
            def call_model(self, *args: Any, **kwargs: Any) -> str:
                return code

        generated = GameGenerator(cast(Any, Gateway())).generate_game(GAME_SPECS[0])
        namespace: dict[str, object] = {}
        exec(generated, namespace)
        snake = cast(Any, namespace["Snake"])()
        snake.start()

        assert snake.state == "playing"

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

    def test_validate_fps_contract_rejects_missing_controls_and_menu(self) -> None:
        gen = GameGenerator(None)  # type: ignore[arg-type]
        spec = GAME_SPECS[0]
        code = """
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
        assert gen.validate_game_code(code, spec) is False

    def test_validate_fps_contract_accepts_playable_controls_and_menu(self) -> None:
        gen = GameGenerator(None)  # type: ignore[arg-type]
        spec = GAME_SPECS[0]
        code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
running = True
state = "menu"
yaw = 0
player_x = 0
player_y = 0
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                state = "playing"
            elif event.key == pygame.K_ESCAPE:
                state = "menu"
        elif event.type == pygame.MOUSEMOTION and state == "playing":
            yaw += event.rel[0]
    keys = pygame.key.get_pressed()
    if state == "playing":
        if keys[pygame.K_w]:
            player_y -= 1
        if keys[pygame.K_s]:
            player_y += 1
        if keys[pygame.K_a]:
            player_x -= 1
        if keys[pygame.K_d]:
            player_x += 1
    if state == "menu":
        screen.fill((0, 0, 0))
    pygame.display.flip()
pygame.quit()
"""
        assert gen.validate_game_code(code, spec) is True

    def test_session_cache_generates_once_per_fixture_prompt_and_model_settings(self) -> None:
        calls: list[tuple[str, str]] = []

        class Generator:
            def generate_game(self, spec: GameSpec, model_id: str = "default") -> str:
                calls.append((spec.prompt_template, model_id))
                return f"# {model_id}\n{spec.prompt_template}"

        cache = GameGenerationCache()
        spec = GAME_SPECS[0]
        generator = cast(Any, Generator())

        first = cache.generate(
            generator,
            spec,
            model_id="default",
            model_settings={"AZURE_MODEL": "qwen", "temperature": "0"},
        )
        second = cache.generate(
            generator,
            spec,
            model_id="default",
            model_settings={"temperature": "0", "AZURE_MODEL": "qwen"},
        )
        changed_model = cache.generate(
            generator,
            spec,
            model_id="azure-game",
            model_settings={"AZURE_MODEL": "qwen", "temperature": "0"},
        )

        assert first is second
        assert changed_model != first
        assert calls == [
            (spec.prompt_template, "default"),
            (spec.prompt_template, "azure-game"),
        ]
        assert cache.miss_count == 2


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

    def test_fps_input_script_covers_every_declared_control_and_menu_transition(self) -> None:
        for spec in GAME_SPECS:
            script = build_game_input_script(spec)
            assert {event.control for event in script} == set(spec.required_controls)
            assert script[0] == GameInputEvent(frame=0, control="return")
            assert script[-1].control == "escape"

    def test_runner_injects_scripted_events_through_pygame_queue(self, tmp_path: Path) -> None:
        if not game_e2e._HAS_PYGAME:
            pytest.skip("pygame not installed")

        game_path = tmp_path / "scripted_game.py"
        game_path.write_text(
            """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
running = True
frame = 0
while running and frame < 12:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    screen.fill((frame, 0, 0))
    pygame.display.flip()
    frame += 1
pygame.quit()
"""
        )
        script = build_game_input_script(GAME_SPECS[0])
        runner = GameRunner()

        frames = runner.run_headless_inline(str(game_path), 12, input_script=script)

        assert frames
        assert runner.last_injected_controls == set(GAME_SPECS[0].required_controls)


class TestReferenceComparisonPipeline:
    def test_full_test_reports_cached_reference_provenance(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class Gateway:
            def call_model(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(content="import pygame")

        generated = [np.full((64, 64, 3), i * 10, dtype=np.uint8) for i in range(6)]
        source = REFERENCE_VIDEO_SPECS["doom_e1m1_hallway"]
        expected = ReferenceComparisonResult(
            game_name=source.game_name,
            source_url=source.source_url,
            source_video_id=source.video_id,
            cache_path=str(tmp_path / source.cache_filename),
            cache_status="cached",
            network_used=False,
            generated_frame_count=6,
            reference_frame_count=6,
            compared_frame_count=6,
            threshold=source.comparison_threshold,
            mean_ssim=0.91,
            motion_correlation=0.82,
            per_frame_ssim=(0.91,) * 6,
            passed=True,
        )

        def fake_compare(
            game_name: str,
            frames: list[np.ndarray],
            cache_dir: str,
            *,
            allow_network: bool,
        ) -> ReferenceComparisonResult:
            assert game_name == source.game_name
            assert frames is generated
            assert cache_dir == str(tmp_path)
            assert allow_network is False
            return expected

        monkeypatch.setattr(game_e2e, "_HAS_PYGAME", True)
        monkeypatch.setattr(game_e2e.GameGenerator, "validate_game_code", lambda code, spec: True)
        monkeypatch.setattr(game_e2e, "compare_gameplay_to_reference", fake_compare, raising=False)
        pipeline = AzureGameE2E(
            deploy_config=DeploymentConfig(
                reference_cache_dir=str(tmp_path),
                allow_reference_network=False,
            ),
            model_gateway=cast(Any, Gateway()),
        )
        def fake_run(
            path: str,
            count: int,
            *,
            input_script: tuple[GameInputEvent, ...],
        ) -> list[np.ndarray]:
            pipeline.runner.last_injected_controls = {event.control for event in input_script}
            return generated

        monkeypatch.setattr(pipeline.runner, "run_headless_inline", fake_run)

        result = pipeline.run_full_test(GAME_SPECS[0])
        report = pipeline.report_results(result)

        assert result.comparison_pass is True
        assert result.mean_ssim == 0.91
        assert result.reference_video_id == source.video_id
        assert result.reference_source_url == source.source_url
        assert result.reference_cache_status == "cached"
        assert result.reference_frames_sampled == 6
        assert result.comparison_threshold == source.comparison_threshold
        assert result.input_controls_injected == GAME_SPECS[0].required_controls
        assert report["reference_video_id"] == source.video_id
        assert report["reference_cache_status"] == "cached"
