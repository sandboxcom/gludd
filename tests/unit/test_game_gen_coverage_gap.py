"""Coverage gap tests for game_gen.py — targets untested paths to >85%."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cloud.game_gen import (
    _HAS_PYGAME,
    DOOM_HALLWAY_SPEC,
    FUTURE_GAMES,
    GAME_TEMPLATES,
    QUAKE_ARENA_SPEC,
    _require_pygame,
    generate_game_code,
    run_game_headless,
    validate_game_syntax,
    write_game_file,
)


def _make_mock_pygame() -> MagicMock:
    pg = MagicMock()
    display_surface = MagicMock()
    display_surface.copy.return_value = MagicMock()
    pg.display.set_mode.return_value = display_surface
    pg.display.flip = MagicMock()
    pg.display.update = MagicMock()
    pg.surfarray.array3d.return_value.transpose.return_value = MagicMock()
    return pg


def _make_mock_spec() -> MagicMock:
    spec = MagicMock()
    spec.loader = MagicMock()
    return spec


# ── _require_pygame ───────────────────────────────────────────────────


class TestRequirePygame:
    def test_require_pygame_returns_module_when_available(self) -> None:
        if not _HAS_PYGAME:
            pytest.skip("pygame not installed")
        mod = _require_pygame()
        assert mod is not None
        assert hasattr(mod, "display")

    def test_require_pygame_raises_when_pygame_is_none(self) -> None:
        with patch("general_ludd.cloud.game_gen.pygame", None), pytest.raises(ImportError, match="pygame is required"):
            _require_pygame()


# ── generate_game_code deep ────────────────────────────────────────────


class TestGenerateGameCodeDeep:
    def test_generate_game_code_passes_spec_dict(self) -> None:
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content="```python\nprint(42)\n```",
        )
        result = generate_game_code(gateway, "doom_hallway", spec={"name": "ignored"})
        assert result == "print(42)"

    def test_generate_game_code_all_five_template_names(self) -> None:
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content="```python\nx = 1\n```",
        )
        for name in sorted(GAME_TEMPLATES):
            result = generate_game_code(gateway, name)
            assert result == "x = 1"

    def test_generate_game_code_call_model_args(self) -> None:
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content="```python\ny = 2\n```",
        )
        generate_game_code(gateway, "quake_arena")
        args, kwargs = gateway.call_model.call_args
        assert args[0] == "default"
        assert kwargs["messages"] == [{"role": "user", "content": GAME_TEMPLATES["quake_arena"]}]
        assert kwargs["estimated_cost"] == 0.0
        assert kwargs["budget_remaining"] == 5.0

    def test_generate_game_code_utf8_content(self) -> None:
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content="```python\n# émoji test\npass\n```",
        )
        result = generate_game_code(gateway, "rogue_dungeon")
        assert result == "# émoji test\npass"


# ── validate_game_syntax deep ──────────────────────────────────────────


class TestValidateGameSyntaxDeep:
    def test_validate_import_from_pygame_locals(self) -> None:
        code = """
from pygame.locals import *
pygame.init()
screen = pygame.display.set_mode((800, 600))
while True:
    for event in pygame.event.get():
        if event.type == QUIT:
            break
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is True

    def test_validate_import_from_pygame_something(self) -> None:
        code = """
from pygame import display
import pygame
display.init()
screen = pygame.display.set_mode((800, 600))
while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            break
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is True

    def test_validate_for_loop_as_game_loop(self) -> None:
        code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
for frame in range(30):
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            break
    screen.fill((0, 0, 0))
    pygame.display.flip()
pygame.quit()
"""
        assert validate_game_syntax(code) is True

    def test_validate_empty_string(self) -> None:
        assert validate_game_syntax("") is False

    def test_validate_whitespace_only(self) -> None:
        assert validate_game_syntax("   \n\n  ") is False

    def test_validate_mousebuttondown_event(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    for event in pygame.event.get():
        if event.type == pygame.MOUSEBUTTONDOWN:
            pass
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is True

    def test_validate_mousemotion_event(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    for event in pygame.event.get():
        if event.type == pygame.MOUSEMOTION:
            pass
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is True

    def test_validate_keydown_event(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN:
            break
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is True

    def test_validate_keyup_event(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    for event in pygame.event.get():
        if event.type == pygame.KEYUP:
            break
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is True

    def test_validate_key_attribute(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    for event in pygame.event.get():
        if event.key == pygame.K_SPACE:
            break
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is True

    def test_validate_display_only_no_input(self) -> None:
        code = """
import pygame
pygame.init()
while True:
    pygame.display.flip()
"""
        assert validate_game_syntax(code) is False

    def test_validate_set_mode_only_no_input(self) -> None:
        code = """
import pygame
pygame.init()
pygame.display.set_mode((800, 600))
while True:
    pass
"""
        assert validate_game_syntax(code) is False

    def test_validate_no_loop_no_input(self) -> None:
        code = """
import pygame
pygame.init()
"""
        assert validate_game_syntax(code) is False

    @pytest.mark.parametrize("attr", ["flip", "update", "blit", "fill", "set_mode"])
    def test_validate_display_methods_dont_count_as_input(self, attr: str) -> None:
        code = f"""
import pygame
pygame.init()
pygame.display.{attr}()
"""
        assert validate_game_syntax(code) is False


# ── run_game_headless error/edge paths ─────────────────────────────────


class TestRunGameHeadlessErrors:
    def test_run_headless_spec_none_raises_importerror(self) -> None:
        pg = _make_mock_pygame()
        with (
            patch("general_ludd.cloud.game_gen._require_pygame", return_value=pg),
            patch.object(importlib.util, "spec_from_file_location", return_value=None),
            pytest.raises(ImportError, match="Cannot load module"),
        ):
            run_game_headless("/tmp/fake.py", num_frames=5)

    def test_run_headless_spec_loader_none_raises_importerror(self) -> None:
        pg = _make_mock_pygame()
        bad_spec = MagicMock()
        bad_spec.loader = None
        with (
            patch("general_ludd.cloud.game_gen._require_pygame", return_value=pg),
            patch.object(importlib.util, "spec_from_file_location", return_value=bad_spec),
            pytest.raises(ImportError, match="Cannot load module"),
        ):
            run_game_headless("/tmp/fake.py", num_frames=5)

    def test_run_headless_num_frames_zero(self) -> None:
        pg = _make_mock_pygame()
        mock_spec = _make_mock_spec()
        game_dir = "/tmp/fake_run_dir"
        game_path = Path(game_dir) / "game.py"
        with (
            patch("general_ludd.cloud.game_gen._require_pygame", return_value=pg),
            patch.object(importlib.util, "spec_from_file_location", return_value=mock_spec),
        ):
            frames = run_game_headless(str(game_path), num_frames=0)
            assert frames == []

    def test_run_headless_systemexit_suppressed(self) -> None:
        pg = _make_mock_pygame()

        def _raise_systemexit(*_a, **_kw):
            raise SystemExit(0)

        pg.display.flip = _raise_systemexit
        mock_spec = _make_mock_spec()
        with (
            patch("general_ludd.cloud.game_gen._require_pygame", return_value=pg),
            patch.object(importlib.util, "spec_from_file_location", return_value=mock_spec),
        ):
            frames = run_game_headless("/tmp/sys_exit.py", num_frames=10)
            assert frames == []

    def test_run_headless_path_object(self) -> None:
        pg = _make_mock_pygame()
        mock_spec = _make_mock_spec()
        game_path = Path("/tmp/path_obj/game.py")
        with (
            patch("general_ludd.cloud.game_gen._require_pygame", return_value=pg),
            patch.object(importlib.util, "spec_from_file_location", return_value=mock_spec),
        ):
            frames = run_game_headless(game_path, num_frames=0)
            assert frames == []

    def test_run_headless_sets_env_vars(self, monkeypatch) -> None:
        pg = _make_mock_pygame()
        mock_spec = _make_mock_spec()
        old_sdl = os.environ.get("SDL_VIDEODRIVER")
        old_hide = os.environ.get("PYGAME_HIDE_SUPPORT_PROMPT")
        try:
            with (
                patch("general_ludd.cloud.game_gen._require_pygame", return_value=pg),
                patch.object(importlib.util, "spec_from_file_location", return_value=mock_spec),
            ):
                run_game_headless("/tmp/env_test.py", num_frames=0)
            assert os.environ.get("SDL_VIDEODRIVER") == "dummy"
            assert os.environ.get("PYGAME_HIDE_SUPPORT_PROMPT") == "1"
        finally:
            if old_sdl is not None:
                monkeypatch.setenv("SDL_VIDEODRIVER", old_sdl)
            else:
                monkeypatch.delenv("SDL_VIDEODRIVER", raising=False)
            if old_hide is not None:
                monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", old_hide)
            else:
                monkeypatch.delenv("PYGAME_HIDE_SUPPORT_PROMPT", raising=False)

    def test_run_headless_restores_flip_and_update(self) -> None:
        pg = _make_mock_pygame()
        original_flip = pg.display.flip
        original_update = pg.display.update
        mock_spec = _make_mock_spec()
        with (
            patch("general_ludd.cloud.game_gen._require_pygame", return_value=pg),
            patch.object(importlib.util, "spec_from_file_location", return_value=mock_spec),
        ):
            run_game_headless("/tmp/restore_test.py", num_frames=0)
        assert pg.display.flip is original_flip
        assert pg.display.update is original_update


# ── write_game_file deep ───────────────────────────────────────────────


class TestWriteGameFileDeep:
    def test_write_game_file_string_path(self, tmp_path: Path) -> None:
        code = "import pygame\n"
        p = write_game_file(code, str(tmp_path / "game.py"))
        assert p.exists()
        assert p.read_text() == code

    def test_write_game_file_overwrite_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "game.py"
        p.write_text("old content")
        result = write_game_file("new content", p)
        assert result.read_text() == "new content"

    def test_write_game_file_parent_exists(self, tmp_path: Path) -> None:
        code = "x = 1\n"
        p = write_game_file(code, tmp_path / "game.py")
        assert p.exists()
        assert p.read_text() == code


# ── Constants deep validation ──────────────────────────────────────────


class TestSpecsDeep:
    def test_doom_spec_colors_all_keys_present(self) -> None:
        required = {"primary", "walls", "floor", "pickup", "ceiling"}
        assert required <= set(DOOM_HALLWAY_SPEC["colors"])

    def test_quake_spec_colors_all_keys_present(self) -> None:
        required = {"primary", "floor", "lava", "platform", "lighting"}
        assert required <= set(QUAKE_ARENA_SPEC["colors"])

    def test_each_future_game_has_colors_dict(self) -> None:
        for name, spec in FUTURE_GAMES.items():
            assert isinstance(spec["colors"], dict), f"{name}: not a dict"
            assert len(spec["colors"]) >= 3, f"{name}: need >=3 color keys"

    def test_all_specs_have_valid_threshold(self) -> None:
        all_specs = [DOOM_HALLWAY_SPEC, QUAKE_ARENA_SPEC, *list(FUTURE_GAMES.values())]
        for spec in all_specs:
            assert 0 < spec["similarity_threshold"] <= 1.0
            assert spec["expected_frames"] > 0

    def test_doom_spec_expected_values(self) -> None:
        assert DOOM_HALLWAY_SPEC["expected_frames"] == 30
        assert DOOM_HALLWAY_SPEC["similarity_threshold"] == 0.35

    def test_quake_spec_expected_values(self) -> None:
        assert QUAKE_ARENA_SPEC["expected_frames"] == 30
        assert QUAKE_ARENA_SPEC["similarity_threshold"] == 0.35


class TestTemplatesDeep:
    def test_template_has_genre_specific_keywords(self) -> None:
        hints = {
            "doom_hallway": "first-person",
            "quake_arena": "industrial",
            "wipeout_racing": "anti-gravity",
            "descent_tunnel": "six-degree",
            "rogue_dungeon": "dungeon",
        }
        for name, hint in hints.items():
            assert hint in GAME_TEMPLATES[name].lower(), f"{name}: missing '{hint}'"

    def test_all_templates_mention_pygame(self) -> None:
        for name, template in GAME_TEMPLATES.items():
            assert "pygame" in template.lower(), f"{name}: missing pygame"

    def test_all_templates_mention_800x600(self) -> None:
        for name, template in GAME_TEMPLATES.items():
            assert "800x600" in template, f"{name}: missing 800x600"

    def test_all_templates_mention_self_contained(self) -> None:
        for name, template in GAME_TEMPLATES.items():
            lower = template.lower()
            assert "self-contained" in lower or "one file" in lower, f"{name}: missing self-contained"

    def test_all_templates_are_plain_strings(self) -> None:
        for name, template in GAME_TEMPLATES.items():
            assert isinstance(template, str)
            assert len(template) > 200, f"{name}: template too short ({len(template)} chars)"


# ── Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_generate_game_code_unknown_template_error_message(self) -> None:
        gw = MagicMock()
        with pytest.raises(ValueError) as exc_info:
            generate_game_code(gw, "pacman")
        msg = str(exc_info.value)
        assert "Unknown game template: pacman" in msg
        assert "Available:" in msg

    def test_future_games_entry_count(self) -> None:
        assert len(FUTURE_GAMES) == 3

    def test_game_templates_exactly_five(self) -> None:
        assert len(GAME_TEMPLATES) == 5


# ── Test count self-pin ────────────────────────────────────────────────


def test_coverage_gap_test_count() -> None:
    import re

    source = Path(__file__).read_text()
    count = len(re.findall(r"^\s*def test_", source, re.MULTILINE))
    assert count >= 20, f"Expected >=20 test functions, found {count}"
