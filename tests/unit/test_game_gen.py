"""Unit tests for game_gen module — cloud/game_gen.py."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.game_gen import (
    _HAS_PYGAME,
    DOOM_HALLWAY_SPEC,
    FUTURE_GAMES,
    GAME_TEMPLATES,
    QUAKE_ARENA_SPEC,
    generate_game_code,
    run_game_headless,
    validate_game_syntax,
    write_game_file,
)


class TestSpecs:
    def test_doom_spec_has_required_fields(self) -> None:
        required = {"name", "genre", "description", "colors", "expected_frames", "similarity_threshold"}
        for key in required:
            assert key in DOOM_HALLWAY_SPEC, f"Missing {key} in DOOM_HALLWAY_SPEC"
        assert DOOM_HALLWAY_SPEC["name"] == "doom_e1m1_hallway"
        assert DOOM_HALLWAY_SPEC["genre"] == "fps"

    def test_quake_spec_has_required_fields(self) -> None:
        required = {"name", "genre", "description", "colors", "expected_frames", "similarity_threshold"}
        for key in required:
            assert key in QUAKE_ARENA_SPEC, f"Missing {key} in QUAKE_ARENA_SPEC"
        assert QUAKE_ARENA_SPEC["name"] == "quake_dm6_arena"
        assert QUAKE_ARENA_SPEC["genre"] == "fps"

    def test_future_games_list_contains_entries(self) -> None:
        assert len(FUTURE_GAMES) >= 3
        assert "wipeout_racing" in FUTURE_GAMES
        assert "descent_tunnel" in FUTURE_GAMES
        assert "rogue_dungeon" in FUTURE_GAMES

    def test_future_games_all_have_required_keys(self) -> None:
        for name, spec in FUTURE_GAMES.items():
            for key in {"name", "genre", "description", "colors", "expected_frames", "similarity_threshold"}:
                assert key in spec, f"Missing {key} in FUTURE_GAMES[{name}]"


class TestTemplates:
    def test_game_templates_valid_python_fstrings(self) -> None:
        """All GAME_TEMPLATES values should be valid, renderable strings."""
        for name, template in GAME_TEMPLATES.items():
            assert isinstance(template, str), f"Template {name} is not a string"
            assert len(template) > 100, f"Template {name} is too short"
            try:
                template.format()
            except (KeyError, IndexError):
                pass
            except Exception as exc:
                pytest.fail(f"Template {name} raised unexpected error: {exc}")

    def test_templates_contain_required_elements(self) -> None:
        for name, template in GAME_TEMPLATES.items():
            lower = template.lower()
            assert "pygame" in lower, f"Template {name} missing 'pygame'"
            assert "800x600" in lower, f"Template {name} missing '800x600'"

    def test_template_keys_match_known_games(self) -> None:
        expected = {"doom_hallway", "quake_arena", "wipeout_racing", "descent_tunnel", "rogue_dungeon"}
        assert set(GAME_TEMPLATES) == expected, f"GAME_TEMPLATES keys {set(GAME_TEMPLATES)} != expected {expected}"


class TestValidateGameSyntax:
    def test_validate_game_syntax_valid(self) -> None:
        code = """
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
        assert validate_game_syntax(code) is True

    def test_validate_game_syntax_missing_pygame(self) -> None:
        code = """
screen = None
running = True
while running:
    running = False
"""
        assert validate_game_syntax(code) is False

    def test_validate_game_syntax_syntax_error(self) -> None:
        assert validate_game_syntax("this is not python {{{") is False

    def test_validate_game_syntax_no_loop(self) -> None:
        code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
pygame.quit()
"""
        assert validate_game_syntax(code) is False

    def test_validate_game_syntax_no_input(self) -> None:
        code = """
import pygame
pygame.init()
screen = pygame.display.set_mode((800, 600))
for i in range(30):
    screen.fill((0, 0, 0))
    pygame.display.flip()
pygame.quit()
"""
        assert validate_game_syntax(code) is False


class TestGenerateGameCode:
    def test_generate_game_code_normalizes_structured_provider_content(self) -> None:
        code = "import pygame\npygame.init()\nwhile True:\n    pygame.event.get()\n"
        blocks = [{"type": "output_text", "text": f"```Python\n{code}```"}]
        gateway = MagicMock()
        gateway.call_model.return_value = SimpleNamespace(
            content=str(blocks),
            raw_response=SimpleNamespace(content=blocks),
        )

        assert generate_game_code(gateway, "doom_hallway") == code.strip()

    def test_generate_game_code_requires_gateway(self) -> None:
        with pytest.raises(ValueError, match="ModelGateway"):
            generate_game_code(None, "doom_hallway")

    def test_generate_game_code_unknown_template(self) -> None:
        gw = MagicMock()
        with pytest.raises(ValueError, match="Unknown game template"):
            generate_game_code(gw, "nonexistent_game")


class TestRunGameHeadless:
    @pytest.mark.skipif(not _HAS_PYGAME, reason="pygame not installed")
    def test_run_game_headless_returns_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            game_path = Path(tmpdir) / "minimal_game.py"
            game_code = """
import pygame
try:
    pygame.init()
except Exception:
    pass
screen = pygame.display.set_mode((800, 600))
frame = 0
running = True
while running and frame < 30:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    screen.fill((frame, frame, frame))
    pygame.display.flip()
    frame += 1
pygame.quit()
"""
            game_path.write_text(game_code)
            frames = run_game_headless(str(game_path), num_frames=15)
            assert len(frames) > 0, "No frames captured"
            assert len(frames) <= 15
            for f in frames:
                assert f.shape == (600, 800, 3), f"Expected (600, 800, 3), got {f.shape}"


class TestWriteGameFile:
    def test_write_game_file(self, tmp_path: Path) -> None:
        code = "import pygame\n"
        p = write_game_file(code, tmp_path / "test_game.py")
        assert p.exists()
        assert p.read_text() == code

    def test_write_game_file_creates_dirs(self, tmp_path: Path) -> None:
        code = "import pygame\n"
        p = write_game_file(code, tmp_path / "nested" / "dirs" / "game.py")
        assert p.exists()
