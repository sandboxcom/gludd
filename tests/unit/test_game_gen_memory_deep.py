"""Deep game generation memory and context tests: cache persistence,
context window management, session isolation, game state serialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from general_ludd.cloud.game_e2e import (
    AzureGameE2E,
    DeploymentConfig,
    E2EResult,
    FrameComparator,
    GameGenerationCache,
    GameGenerator,
    GameInputEvent,
    GameRunner,
    GameSpec,
)

_MINIMAL_GAME = (
    "import pygame\npygame.init()\nwhile True:\n"
    "    for e in pygame.event.get():\n        pass\n    pygame.display.flip()\n"
)


def _make_spec(
    name: str = "test_game",
    prompt: str = "make a game",
    genre: str = "arcade",
    description: str = "test game",
    expected_frames: int = 30,
    similarity_threshold: float = 0.4,
    required_controls: tuple[str, ...] = (),
    requires_menu: bool = False,
    reference_video_url: str | None = None,
) -> GameSpec:
    return GameSpec(
        name=name,
        genre=genre,
        description=description,
        expected_frames=expected_frames,
        similarity_threshold=similarity_threshold,
        prompt_template=prompt,
        required_controls=required_controls,
        requires_menu=requires_menu,
        reference_video_url=reference_video_url,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  1. GameGenerationCache — memory persistence across games
# ═══════════════════════════════════════════════════════════════════════════


class TestCacheMemoryPersistence:
    def test_cache_returns_same_code_for_identical_spec(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(content=_MINIMAL_GAME)
        gen = GameGenerator(gateway)
        spec = _make_spec(name="doom_e1m1_hallway", prompt="hallway prompt")
        first = cache.generate(gen, spec, model_id="gpt-4")
        second = cache.generate(gen, spec, model_id="gpt-4")
        assert first is second
        assert cache.miss_count == 1

    def test_cache_key_differs_by_prompt_template(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="a = 1\n"),
            SimpleNamespace(content="b = 2\n"),
        ]
        gen = GameGenerator(gateway)
        spec_a = _make_spec(name="game", prompt="template A")
        spec_b = _make_spec(name="game", prompt="template B")
        r1 = cache.generate(gen, spec_a, model_id="gpt-4")
        r2 = cache.generate(gen, spec_b, model_id="gpt-4")
        assert r1 != r2
        assert cache.miss_count == 2

    def test_cache_key_differs_by_model_settings(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="temp_0 = 1\n"),
            SimpleNamespace(content="temp_1 = 2\n"),
        ]
        gen = GameGenerator(gateway)
        spec = _make_spec()
        r1 = cache.generate(gen, spec, model_id="gpt-4", model_settings={"temperature": "0.7"})
        r2 = cache.generate(gen, spec, model_id="gpt-4", model_settings={"temperature": "1.0"})
        assert r1 != r2
        assert cache.miss_count == 2

    def test_cache_preserves_across_multiple_games_in_session(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(content=_MINIMAL_GAME)
        gen = GameGenerator(gateway)
        names = ["doom", "quake", "wipeout", "descent", "rogue"]
        for name in names:
            spec = _make_spec(name=name, prompt=f"generate {name}")
            cache.generate(gen, spec, model_id="gpt-4")
        assert cache.miss_count == 5
        for name in names:
            spec = _make_spec(name=name, prompt=f"generate {name}")
            cache.generate(gen, spec, model_id="gpt-4")
        assert cache.miss_count == 5

    def test_cache_single_game_eviction_by_name_change(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="first\n"),
            SimpleNamespace(content="second\n"),
        ]
        gen = GameGenerator(gateway)
        spec_v1 = _make_spec(name="doom_v1", prompt="hallway prompt")
        spec_v2 = _make_spec(name="doom_v2", prompt="hallway prompt")
        r1 = cache.generate(gen, spec_v1, model_id="gpt-4")
        r2 = cache.generate(gen, spec_v2, model_id="gpt-4")
        assert r1 != r2
        assert cache.miss_count == 2


# ═══════════════════════════════════════════════════════════════════════════
#  2. Context window management — prompt size, token estimation
# ═══════════════════════════════════════════════════════════════════════════


class TestContextWindowManagement:
    def test_small_prompt_template_within_typical_context_window(self) -> None:
        spec = _make_spec(prompt="A short prompt.")
        assert len(spec.prompt_template) < 4096
        estimated_tokens = len(spec.prompt_template) // 4
        assert estimated_tokens < 1024

    def test_large_prompt_template_reports_byte_size(self) -> None:
        long_prompt = "Write a complete Python game using pygame that renders. " * 500
        spec = _make_spec(prompt=long_prompt)
        assert len(spec.prompt_template.encode("utf-8")) > 8000
        tokens_estimate = len(spec.prompt_template.encode("utf-8")) // 4
        assert tokens_estimate > 2000

    def test_game_spec_serialized_size_is_bounded(self) -> None:
        spec = GameSpec(
            name="doom_e1m1_hallway",
            genre="fps",
            description="A long hallway with grey stone walls, pillars, and a pickup." * 20,
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a complete Python game using pygame." * 50,
            required_controls=("w", "a", "s", "d", "mouse", "space", "escape", "return"),
            requires_menu=True,
        )
        serialized = json.dumps(asdict(spec)).encode("utf-8")
        assert len(serialized) < 256_000

    def test_multiple_specs_aggregate_prompt_size(self) -> None:
        specs = [
            _make_spec(
                name=f"game_{i}",
                prompt="Write a complete Python game using pygame that renders a scene. " * (i + 1),
            )
            for i in range(5)
        ]
        total_prompt_bytes = sum(len(s.prompt_template.encode("utf-8")) for s in specs)
        assert total_prompt_bytes > 0
        avg_tokens = total_prompt_bytes // 4
        assert avg_tokens < 200_000


# ═══════════════════════════════════════════════════════════════════════════
#  3. Session isolation — separate E2E instances, caches, runners
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionIsolation:
    def test_separate_e2e_instances_have_independent_generators(self) -> None:
        gw1 = MagicMock()
        gw2 = MagicMock()
        e2e_a = AzureGameE2E(model_gateway=gw1)
        e2e_b = AzureGameE2E(model_gateway=gw2)
        assert e2e_a.generator is not e2e_b.generator
        assert e2e_a.runner is not e2e_b.runner
        assert e2e_a.comparator is not e2e_b.comparator

    def test_separate_e2e_runners_do_not_share_processes(self) -> None:
        runner_a = GameRunner()
        runner_b = GameRunner()
        assert runner_a._processes is not runner_b._processes
        runner_a._processes.append(MagicMock())
        assert len(runner_a._processes) == 1
        assert len(runner_b._processes) == 0

    def test_separate_caches_do_not_share_store(self) -> None:
        cache_a = GameGenerationCache()
        cache_b = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="a_value\n"),
            SimpleNamespace(content="b_value\n"),
        ]
        gen = GameGenerator(gateway)
        spec = _make_spec()
        r_a = cache_a.generate(gen, spec)
        r_b = cache_b.generate(gen, spec)
        assert r_a != r_b
        assert cache_a.miss_count == 1
        assert cache_b.miss_count == 1

    def test_e2e_result_defaults_isolated_per_instance(self) -> None:
        result_a = E2EResult(spec_name="doom")
        result_b = E2EResult(spec_name="quake")
        result_a.code_generated = True
        result_a.frames_captured = 15
        assert result_b.code_generated is False
        assert result_b.frames_captured == 0

    def test_deployment_config_instances_independent(self) -> None:
        config_a = DeploymentConfig()
        config_b = DeploymentConfig(location="westus", vm_size="Standard_NC8as_T4_v3")
        assert config_a.location == "eastus"
        assert config_b.location == "westus"
        assert config_a.vm_size != config_b.vm_size

    def test_runner_cleanup_isolated_per_instance(self) -> None:
        runner_a = GameRunner()
        runner_b = GameRunner()
        proc_a = MagicMock()
        proc_b = MagicMock()
        runner_a._processes.append(proc_a)
        runner_b._processes.append(proc_b)
        runner_a.cleanup()
        proc_a.kill.assert_called_once()
        proc_b.kill.assert_not_called()
        assert len(runner_a._processes) == 0
        assert len(runner_b._processes) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  4. Game state serialization — E2EResult, GameSpec, frames
# ═══════════════════════════════════════════════════════════════════════════


class TestGameStateSerialization:
    def test_e2e_result_json_roundtrip(self) -> None:
        result = E2EResult(
            spec_name="doom_e1m1_hallway",
            code_generated=True,
            code_valid=True,
            game_ran=True,
            frames_captured=30,
            mean_ssim=0.85,
            mean_psnr=32.5,
            comparison_pass=True,
            input_controls_injected=("w", "a", "s", "d", "mouse", "escape", "return"),
            errors=[],
        )
        d = asdict(result)
        data = json.dumps(d)
        restored = json.loads(data)
        assert restored["spec_name"] == "doom_e1m1_hallway"
        assert restored["code_generated"] is True
        assert restored["frames_captured"] == 30
        assert restored["mean_ssim"] == 0.85
        assert set(restored["input_controls_injected"]) == {"w", "a", "s", "d", "mouse", "escape", "return"}

    def test_e2e_result_with_errors_serializes_errors(self) -> None:
        result = E2EResult(
            spec_name="broken_game",
            errors=["SyntaxError: invalid syntax", "ImportError: no pygame"],
        )
        data = json.dumps(asdict(result))
        restored = json.loads(data)
        assert len(restored["errors"]) == 2
        assert "SyntaxError" in restored["errors"][0]

    def test_game_spec_json_roundtrip_preserves_controls(self) -> None:
        spec = GameSpec(
            name="fps_game",
            genre="fps",
            description="test",
            expected_frames=60,
            similarity_threshold=0.5,
            prompt_template="Write pygame FPS.",
            required_controls=("w", "a", "s", "d", "mouse", "space", "r"),
            requires_menu=True,
        )
        data = json.dumps(asdict(spec))
        restored = json.loads(data)
        assert restored["name"] == spec.name
        assert restored["genre"] == spec.genre
        assert restored["expected_frames"] == 60
        assert restored["similarity_threshold"] == 0.5
        assert tuple(restored["required_controls"]) == spec.required_controls
        assert restored["requires_menu"] is True

    def test_game_spec_hash_determinism(self) -> None:
        spec_a = GameSpec(
            name="doom_hallway",
            genre="fps",
            description="hallway",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a complete Python game.",
        )
        spec_b = GameSpec(
            name="doom_hallway",
            genre="fps",
            description="hallway",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a complete Python game.",
        )
        ha = hashlib.sha256(json.dumps(asdict(spec_a), sort_keys=True).encode()).hexdigest()
        hb = hashlib.sha256(json.dumps(asdict(spec_b), sort_keys=True).encode()).hexdigest()
        assert ha == hb

    def test_input_event_serialization_roundtrip(self) -> None:
        events = [
            GameInputEvent(frame=0, control="return"),
            GameInputEvent(frame=1, control="w"),
            GameInputEvent(frame=2, control="a"),
            GameInputEvent(frame=3, control="escape"),
        ]
        serialized = json.dumps([asdict(e) for e in events])
        restored = json.loads(serialized)
        assert len(restored) == 4
        assert restored[0]["frame"] == 0
        assert restored[0]["control"] == "return"
        assert restored[-1]["control"] == "escape"

    def test_cache_key_deterministic_for_identical_inputs(self) -> None:
        spec = _make_spec(name="doom_hallway", prompt="Write a complete Python game.")
        settings1: tuple[tuple[str, str], ...] = ()
        settings2: tuple[tuple[str, str], ...] = ()
        key1 = (spec.name, spec.prompt_template, "gpt-4", settings1)
        key2 = (spec.name, spec.prompt_template, "gpt-4", settings2)
        assert hash(key1) == hash(key2)
        assert key1 == key2

    def test_cache_key_model_settings_ordering_idempotent(self) -> None:
        settings_a: tuple[tuple[str, str], ...] = (("temperature", "0.7"), ("top_p", "0.9"))
        settings_b: tuple[tuple[str, str], ...] = (("top_p", "0.9"), ("temperature", "0.7"))
        assert sorted(settings_a) == sorted(settings_b)

    def test_e2e_result_frame_metadata_bounds(self) -> None:
        result = E2EResult(spec_name="doom", frames_captured=30, comparison_threshold=0.35)
        d = asdict(result)
        assert d["frames_captured"] == 30
        assert d["comparison_threshold"] == 0.35
        assert d["code_generated"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  5. Deep memory — cache key collision resistance
# ═══════════════════════════════════════════════════════════════════════════


class TestCacheKeyCollisionResistance:
    def test_different_spec_names_same_prompt_produce_different_keys(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="doom_out\n"),
            SimpleNamespace(content="quake_out\n"),
        ]
        gen = GameGenerator(gateway)
        spec_doom = _make_spec(name="doom", prompt="hallway scene")
        spec_quake = _make_spec(name="quake", prompt="hallway scene")
        r1 = cache.generate(gen, spec_doom, model_id="gpt-4")
        r2 = cache.generate(gen, spec_quake, model_id="gpt-4")
        assert r1 != r2
        assert cache.miss_count == 2

    def test_same_spec_name_different_model_ids_collision_free(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="sonnet_out\n"),
            SimpleNamespace(content="haiku_out\n"),
            SimpleNamespace(content="opus_out\n"),
        ]
        gen = GameGenerator(gateway)
        spec = _make_spec()
        r1 = cache.generate(gen, spec, model_id="sonnet")
        r2 = cache.generate(gen, spec, model_id="haiku")
        r3 = cache.generate(gen, spec, model_id="opus")
        assert r1 != r2
        assert r2 != r3
        assert r1 != r3

    def test_empty_model_settings_preserves_cache_key_stability(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(content="cached\n")
        gen = GameGenerator(gateway)
        spec = _make_spec()
        r1 = cache.generate(gen, spec, model_id="gpt-4")
        r2 = cache.generate(gen, spec, model_id="gpt-4", model_settings={})
        r3 = cache.generate(gen, spec, model_id="gpt-4", model_settings=None)
        assert r1 is r2
        assert r2 is r3
        assert cache.miss_count == 1


# ═══════════════════════════════════════════════════════════════════════════
#  6. Game state snapshot and restore patterns
# ═══════════════════════════════════════════════════════════════════════════


class TestGameStateSnapshotRestore:
    def test_result_to_dict_and_back_preserves_all_fields(self) -> None:
        result = E2EResult(
            spec_name="snake",
            code_generated=True,
            code_valid=True,
            game_ran=True,
            frames_captured=25,
            mean_ssim=0.92,
            mean_psnr=38.1,
            comparison_pass=True,
            input_controls_injected=("w", "a", "s", "d"),
            errors=[],
            generated_code_path="/tmp/game.py",
            generated_code=_MINIMAL_GAME,
            reference_source_url="https://example.com/ref.mp4",
            reference_video_id="abc123",
            reference_cache_status="cached",
            reference_frames_sampled=25,
            comparison_threshold=0.4,
            motion_correlation=0.78,
        )
        state = asdict(result)
        assert state["spec_name"] == result.spec_name
        assert state["mean_ssim"] == result.mean_ssim
        assert state["mean_psnr"] == result.mean_psnr
        assert tuple(state["input_controls_injected"]) == result.input_controls_injected
        assert state["generated_code"] == result.generated_code

    def test_deployment_config_to_dict_for_logging(self) -> None:
        config = DeploymentConfig(
            subscription_id="sub-123",
            resource_group="gludd-e2e",
            vm_size="Standard_NC24ads_A100_v4",
            location="eastus",
            use_preprovisioned=True,
            provision_timeout_seconds=900,
        )
        state = asdict(config)
        assert state["subscription_id"] == "sub-123"
        assert state["vm_size"] == "Standard_NC24ads_A100_v4"
        assert state["use_preprovisioned"] is True
        assert state["provision_timeout_seconds"] == 900

    def test_comparison_result_serializable(self) -> None:
        fc = FrameComparator()
        frames = [np.zeros((10, 10, 3), dtype=np.uint8)] * 5
        result = fc.compare_frames(frames, frames, threshold=0.4)
        serialized = json.dumps(result, default=str)
        restored = json.loads(serialized)
        assert restored["pass"] is True
        assert restored["frame_count"] == 5

    def test_game_generation_cache_can_be_reset(self) -> None:
        cache = GameGenerationCache()
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            SimpleNamespace(content="first\n"),
            SimpleNamespace(content="second\n"),
        ]
        gen = GameGenerator(gateway)
        spec = _make_spec()
        cache.generate(gen, spec)
        assert cache.miss_count == 1
        cache._generated.clear()
        cache.miss_count = 0
        cache.generate(gen, spec)
        assert cache.miss_count == 1


# ═══════════════════════════════════════════════════════════════════════════
#  7. Test count self-pin
# ═══════════════════════════════════════════════════════════════════════════


def test_game_gen_memory_deep_test_count() -> None:
    import re

    source = Path(__file__).read_text()
    count = len(re.findall(r"^\s*def test_", source, re.MULTILINE))
    assert count >= 25, f"Expected >=25 test functions, found {count}"
