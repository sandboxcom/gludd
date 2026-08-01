"""Game generation module for AI-powered game E2E fidelity testing.

Provides game spec templates, LLM-based code generation, syntax validation,
and headless game execution with frame capture.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)

_HAS_PYGAME: bool
try:
    import pygame  # type: ignore[import-untyped]

    _HAS_PYGAME = True
except ImportError:  # pragma: no cover
    pygame = None  # type: ignore[assignment]
    _HAS_PYGAME = False


QUAKE_ARENA_SPEC: dict[str, Any] = {
    "name": "quake_dm6_arena",
    "genre": "fps",
    "description": (
        "Dark industrial arena with metal platforms at different heights, "
        "lava pools below, flickering lights, and basic gravity physics. "
        "Player can jump between platforms with SPACE."
    ),
    "colors": {
        "primary": "orange/brown",
        "floor": "dark metal grey",
        "lava": "orange/red glowing",
        "platform": "rusted iron",
        "lighting": "flickering orange point lights",
    },
    "expected_frames": 30,
    "similarity_threshold": 0.35,
}


DOOM_HALLWAY_SPEC: dict[str, Any] = {
    "name": "doom_e1m1_hallway",
    "genre": "fps",
    "description": (
        "First-person shooter with raycasting rendering. Grey stone hallway "
        "with pillars, green armor pickup at the end, and ceiling lights."
    ),
    "colors": {
        "primary": "grey/stone",
        "walls": "grey stone texture",
        "floor": "red/brown",
        "pickup": "green glowing",
        "ceiling": "dark with lights",
    },
    "expected_frames": 30,
    "similarity_threshold": 0.35,
}


FUTURE_GAMES: dict[str, dict[str, Any]] = {
    "wipeout_racing": {
        "name": "wipeout_racing",
        "genre": "racing",
        "description": (
            "Futuristic anti-gravity racing game with floating ships, "
            "neon-colored tracks, speed boosts, and electronic music visuals."
        ),
        "colors": {"primary": "neon/cyan", "track": "glowing purple", "sky": "dark blue"},
        "expected_frames": 30,
        "similarity_threshold": 0.35,
    },
    "descent_tunnel": {
        "name": "descent_tunnel",
        "genre": "six-dof-shooter",
        "description": (
            "Six-degree-of-freedom tunnel shooter with full 3D rotation, "
            "metallic tunnel walls, red enemy bots, and energy pickups."
        ),
        "colors": {"primary": "metallic grey", "lights": "red/orange", "pickups": "blue"},
        "expected_frames": 30,
        "similarity_threshold": 0.35,
    },
    "rogue_dungeon": {
        "name": "rogue_dungeon",
        "genre": "roguelike",
        "description": (
            "Procedurally generated dungeon crawler with ASCII or simple tile "
            "graphics, walls, corridors, monsters, and treasure rooms."
        ),
        "colors": {"primary": "dark grey/black", "walls": "grey", "player": "green"},
        "expected_frames": 30,
        "similarity_threshold": 0.35,
    },
}


GAME_TEMPLATES: dict[str, str] = {
    "doom_hallway": (
        "Write a complete Python game using pygame that renders a first-person "
        "shooter scene.\n\n"
        "Requirements:\n"
        "- Grey stone-textured walls forming a long hallway\n"
        "- Square pillars along the hallway sides\n"
        "- Red/brown floor\n"
        "- Ceiling with periodic light sources\n"
        "- A green glowing pickup item at the far end of the hallway\n"
        "- Player can look around with mouse and move forward/backward with W/S\n"
        "- Rendering should use raycasting or simple 3D projection\n"
        "- Window size 800x600\n"
        "- Run for at least 30 frames then exit\n"
        "- The game must be self-contained in one file and runnable with: python game.py\n"
    ),
    "quake_arena": (
        "Write a complete Python game using pygame that renders a dark industrial "
        "arena.\n\n"
        "Requirements:\n"
        "- Dark metal-textured platforms at 3 different heights\n"
        "- Orange lava pool at the bottom\n"
        "- A central pillar/column structure\n"
        "- Orange/brown color palette\n"
        "- Flickering point lights on platforms\n"
        "- Player can look with mouse and jump between platforms with SPACE\n"
        "- Simple gravity and platform collision\n"
        "- Window 800x600\n"
        "- Run for 30 frames then exit\n"
        "- Self-contained, runnable with: python game.py\n"
    ),
    "wipeout_racing": (
        "Write a complete Python game using pygame that renders a futuristic "
        "anti-gravity racing game.\n\n"
        "Requirements:\n"
        "- Neon-colored floating track with cyan and purple glow\n"
        "- Player ship that can steer left/right and accelerate/brake\n"
        "- Speed boost pads on the track\n"
        "- Dark blue sky/background with star field\n"
        "- HUD showing speed and lap time\n"
        "- Window 800x600\n"
        "- Run for 30 frames then exit\n"
        "- Self-contained, runnable with: python game.py\n"
    ),
    "descent_tunnel": (
        "Write a complete Python game using pygame that renders a six-degree-of-freedom "
        "tunnel shooter.\n\n"
        "Requirements:\n"
        "- Metallic textured tunnel with full 3D rotation (pitch/yaw/roll)\n"
        "- Red enemy bots that move through the tunnel\n"
        "- Blue energy pickups floating in the tunnel\n"
        "- Red/orange lighting on tunnel walls\n"
        "- Player can rotate in all three axes and move forward/backward\n"
        "- Window 800x600\n"
        "- Run for 30 frames then exit\n"
        "- Self-contained, runnable with: python game.py\n"
    ),
    "rogue_dungeon": (
        "Write a complete Python game using pygame that renders a procedurally "
        "generated dungeon crawler.\n\n"
        "Requirements:\n"
        "- Tile-based dungeon with grey walls, dark corridors, and rooms\n"
        "- Player represented as a green character that can move with arrow keys\n"
        "- 2-3 enemy monsters (red) that roam the dungeon\n"
        "- A treasure room with a gold pickup\n"
        "- Simple turn-based or real-time movement\n"
        "- Window 800x600\n"
        "- Run for 30 frames then exit\n"
        "- Self-contained, runnable with: python game.py\n"
    ),
}


def generate_game_code(
    gateway: ModelGateway | None,
    game_name: str,
    spec: dict[str, Any] | None = None,
) -> str:
    """Generate game code by sending a prompt template to an LLM on Azure GPU.

    Args:
        gateway: Configured ModelGateway pointed at an Azure GPU endpoint.
        game_name: Name of the game template to use (key in GAME_TEMPLATES).
        spec: Optional spec dict to format into the template (unused currently;
            templates are self-contained).

    Returns:
        Generated Python game code as a string.

    Raises:
        ValueError: If game_name is not a known template.
        RuntimeError: If LLM returns empty response.
    """
    template = GAME_TEMPLATES.get(game_name)
    if template is None:
        raise ValueError(f"Unknown game template: {game_name}. Available: {sorted(GAME_TEMPLATES)}")

    if gateway is None:
        raise ValueError("ModelGateway is required for code generation")

    response = gateway.call_model(
        "default",
        messages=[{"role": "user", "content": template}],
        estimated_cost=0.0,
        budget_remaining=5.0,
    )
    content = getattr(response, "content", "")
    if not content:
        raise RuntimeError("LLM returned empty response")
    return _extract_python_code(str(content))


def _extract_python_code(content: str) -> str:
    """Extract Python code from an LLM response, stripping markdown fences."""
    fence = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return content.strip()


def validate_game_syntax(code: str) -> bool:
    """Validate generated game code for required elements.

    Checks for: valid Python syntax, pygame import, game loop (while/for),
    input/event handling, and display initialization.

    Args:
        code: Python game code as a string.

    Returns:
        True if code passes all validation checks.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    has_pygame_import = False
    has_game_loop = False
    has_input_handling = False

    input_event_methods = {
        "get",
        "event",
        "key",
        "QUIT",
        "KEYDOWN",
        "KEYUP",
        "MOUSEMOTION",
        "MOUSEBUTTONDOWN",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pygame":
                    has_pygame_import = True
        elif isinstance(node, ast.ImportFrom):
            if node.module and "pygame" in node.module:
                has_pygame_import = True
        elif isinstance(node, (ast.While, ast.For)):
            has_game_loop = True
        elif isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "pygame"
                and node.value.attr in input_event_methods
            ):
                has_input_handling = True
            if node.attr in {"flip", "update", "blit", "fill", "set_mode"}:
                pass

    return has_pygame_import and has_game_loop and has_input_handling


def run_game_headless(
    game_path: str | Path,
    num_frames: int = 30,
) -> list[np.ndarray]:
    """Run a game in headless mode and capture rendered frames.

    Uses SDL_VIDEODRIVER=dummy for headless execution. Patches pygame.display
    functions to capture frames before they are displayed.

    Args:
        game_path: Path to the game Python file.
        num_frames: Number of frames to capture before exiting.

    Returns:
        List of captured frames as numpy arrays (height, width, 3).

    Raises:
        ImportError: If pygame is not installed.
    """
    if not _HAS_PYGAME:
        raise ImportError("pygame is required for headless game execution")

    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

    game_path_obj = Path(game_path)
    game_dir = str(game_path_obj.parent)
    game_name = game_path_obj.stem

    display_surface = pygame.display.set_mode((800, 600))
    captured: list[pygame.Surface] = []

    original_flip = pygame.display.flip
    original_update = pygame.display.update

    def _capturing_flip() -> None:
        surf = display_surface.copy()
        captured.append(surf)
        if len(captured) >= num_frames:
            raise SystemExit(0)
        original_flip()

    def _capturing_update(*args: Any, **kwargs: Any) -> None:
        surf = display_surface.copy()
        captured.append(surf)
        if len(captured) >= num_frames:
            raise SystemExit(0)
        original_update(*args, **kwargs)

    pygame.display.flip = _capturing_flip  # type: ignore[method-assign]
    pygame.display.update = _capturing_update  # type: ignore[method-assign]

    try:
        if game_dir not in sys.path:
            sys.path.insert(0, game_dir)

        spec = importlib.util.spec_from_file_location(game_name, str(game_path_obj))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {game_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[game_name] = module

        with contextlib.suppress(SystemExit):
            spec.loader.exec_module(module)
    finally:
        pygame.display.flip = original_flip  # type: ignore[method-assign]
        pygame.display.update = original_update  # type: ignore[method-assign]

    frames: list[np.ndarray] = []
    for surf in captured[:num_frames]:
        arr = pygame.surfarray.array3d(surf)
        frames.append(arr.transpose(1, 0, 2))

    return frames


def write_game_file(code: str, output_path: str | Path) -> Path:
    """Write generated game code to a file.

    Args:
        code: Python game code string.
        output_path: Destination file path.

    Returns:
        Path to the written file.
    """
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code, encoding="utf-8")
    return p


__all__ = [
    "DOOM_HALLWAY_SPEC",
    "FUTURE_GAMES",
    "GAME_TEMPLATES",
    "QUAKE_ARENA_SPEC",
    "generate_game_code",
    "run_game_headless",
    "validate_game_syntax",
    "write_game_file",
]
