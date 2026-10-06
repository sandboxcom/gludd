"""DeepSeek game definitions and observable gateway state shared by the live harness."""

from __future__ import annotations

import importlib.util
import json
import os
import textwrap
import time
from pathlib import Path
from typing import Any, cast

# ---------------------------------------------------------------------------
# Key loading — read from env or .deepseek.key file
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _load_deepseek_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    key_file = _REPO_ROOT / ".deepseek.key"
    if key_file.exists():
        v = key_file.read_text().strip()
        return v if v else None
    return None


_SKIP_REASON = (
    "DEEPSEEK_API_KEY not set and .deepseek.key not found — "
    "set DEEPSEEK_API_KEY or place key in .deepseek.key to run game-building test"
)
_PROVIDER_SKIP_REASON = "langchain-openai is not installed — run make sync with provider dependencies"

_DS_BASE_URL = "https://api.deepseek.com/v1"
_E2E_TARGET_GAME = os.environ.get("E2E_TARGET_GAME", "").strip().lower()

_KEY_SENTINEL = object()
_KEY_CACHE: str | None | object = _KEY_SENTINEL
_GATEWAY_CACHE: dict[str, Any] = {}


def _get_deepseek_key() -> str | None:
    global _KEY_CACHE
    if _KEY_CACHE is _KEY_SENTINEL:
        _KEY_CACHE = _load_deepseek_key()
    return cast(str | None, _KEY_CACHE)


_DEEPSEEK_KEY = _get_deepseek_key()
_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None
_LIVE_SKIP_REASON = _SKIP_REASON if not _DEEPSEEK_KEY else _PROVIDER_SKIP_REASON


# ---------------------------------------------------------------------------
# Observability data collector
# ---------------------------------------------------------------------------

_OBSERVABILITY_DATA: dict[str, Any] = {}


def _export_observability_report(out_dir: Path) -> Path:
    """Write the accumulated observability data to a JSON report file in ``out_dir``."""
    report = {
        "report_generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "games": _OBSERVABILITY_DATA,
        "summary": _compute_obs_summary(),
    }
    out_path = out_dir / ".game-audit-report.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nObservability report written to {out_path}")
    return out_path


def _compute_obs_summary() -> dict[str, Any]:
    total_games = len(_OBSERVABILITY_DATA)
    if total_games == 0:
        return {"total_games": 0}

    games_imported = sum(1 for g in _OBSERVABILITY_DATA.values() if g.get("imported"))
    games_verified = sum(
        1 for g in _OBSERVABILITY_DATA.values() if g.get("checks_passed", 0) == g.get("checks_total", 0)
    )
    total_tokens_in = sum(g.get("tokens_in", 0) for g in _OBSERVABILITY_DATA.values())
    total_tokens_out = sum(g.get("tokens_out", 0) for g in _OBSERVABILITY_DATA.values())
    total_latency_ms = sum(
        (
            g.get("phases", {}).get("model_call", 0)
            + g.get("phases", {}).get("extract_code", 0)
            + g.get("phases", {}).get("ast_parse", 0)
            + g.get("phases", {}).get("game_verify", 0)
        )
        for g in _OBSERVABILITY_DATA.values()
    )

    games_by_tokens = sorted(
        _OBSERVABILITY_DATA.items(),
        key=lambda kv: kv[1].get("tokens_out", 0),
        reverse=True,
    )
    games_by_latency = sorted(
        _OBSERVABILITY_DATA.items(),
        key=lambda kv: sum(kv[1].get("phases", {}).values()),
        reverse=True,
    )

    return {
        "total_games": total_games,
        "games_imported": games_imported,
        "games_fully_verified": games_verified,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_latency_ms": total_latency_ms,
        "total_latency_s": round(total_latency_ms / 1000, 2),
        "most_tokens": games_by_tokens[0][0] if games_by_tokens else None,
        "most_latency": games_by_latency[0][0] if games_by_latency else None,
    }


def _init_game_obs(game_id: str) -> dict[str, Any]:
    entry = {
        "game_id": game_id,
        "imported": False,
        "instantiated": False,
        "checks_passed": 0,
        "checks_total": 0,
        "checks": {},
        "tokens_in": 0,
        "tokens_out": 0,
        "tool_calls": 0,
        "content_len": 0,
        "model": "",
        "phases": {
            "model_call": 0.0,
            "extract_code": 0.0,
            "ast_parse": 0.0,
            "game_verify": 0.0,
        },
        "errors": [],
        "gaps": [],
    }
    _OBSERVABILITY_DATA[game_id] = entry
    return entry


# ---------------------------------------------------------------------------
# Gateway builder (DeepSeek-specific)
# ---------------------------------------------------------------------------


def _build_deepseek_gateway() -> Any:
    if "gateway" in _GATEWAY_CACHE:
        return _GATEWAY_CACHE["gateway"]

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    key = _get_deepseek_key()
    assert key, "key must be set before building gateway"

    profile = ModelProfile(
        model_profile_id="deepseek_coder",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name="deepseek-chat",
        api_base_alias="DEEPSEEK_API_BASE",
        credential_alias="DEEPSEEK_API_KEY",
        context_window=65536,
        max_input_tokens=60000,
        max_output_tokens=8192,
        cost_per_input_token=0.00000027,
        cost_per_output_token=0.0000011,
        api_metered=True,
        run_budget_usd=5.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("DEEPSEEK_API_KEY", key)
    secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
    gateway = cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
    _GATEWAY_CACHE["gateway"] = gateway
    return gateway


# ---------------------------------------------------------------------------
# Game definitions — prompt + verification for each game
# ---------------------------------------------------------------------------

GAME_DEFINITIONS: dict[str, dict[str, Any]] = {
    "snake": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements the classic Snake game
            as a headless state machine. NO external dependencies except the stdlib. NO display
            code (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Snake`
            - `__init__(self, grid_w=20, grid_h=20)`: initialize grid, snake at center facing right,
              place first food at random position
            - `tick(self) -> bool`: advance one frame; move snake in current direction; return False
              if game over (wall or self collision), True otherwise
            - `input(self, action: str)`: change direction; accept "up"/"down"/"left"/"right";
              ignore reverse-direction (can't go back into self)
            - `render_state(self) -> dict`: return serializable state dict with keys:
              `grid_w`, `grid_h`, `snake` (list of [x,y] segments, head first),
              `food` (list of single [x,y]), `score` (int), `game_over` (bool), `length` (int)
            - `spawn_food(self)`: place food at random empty cell
            - Eating food: when head overlaps food, grow by 1, increment score, spawn new food

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int) starts at 0 and
              increments on positive events (eating food).
            - Game-over detection: when a lose condition triggers (wall or self collision),
              `state` transitions to "game_over" and `game_over` (bool) becomes True. `tick()`
              after game_over is a no-op (returns without changing state).
            - `restart()` method: resets ALL state (score=0, game_over=False, snake to initial
              center position, food respawned, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class Snake:`.
        """).strip(),
        "class_name": "Snake",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("tick_loop", "tick() returns bool for 100 frames without error"),
            ("direction_change", "input('down') then tick advances snake downward"),
            ("wall_collision", "snake hitting wall returns game_over True"),
            ("food_eating", "moving onto food cell increments score and length"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "tetris": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Tetris as a headless
            state machine. NO external dependencies except the stdlib. NO display code.
            NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Tetris`
            - `__init__(self, grid_w=10, grid_h=20)`: initialize empty grid, spawn first piece
            - Standard 7 tetrominoes (I,O,T,S,Z,J,L) with their shapes as 2D arrays
            - `tick(self) -> bool`: advance one frame; MUST auto-apply gravity (move piece down one row
              every tick WITHOUT requiring player input); pieces MUST fall ONE ROW on each tick()
              call even when no input is given. Return False if game over (piece locks above visible
              grid), True otherwise
            - `input(self, action: str)`: accept "left"/"right" (move), "down" (soft drop),
              "rotate_cw"/"rotate_ccw" (rotation), "hard_drop" (instant drop), "hold" (swap held piece)
            - `render_state(self) -> dict`: return dict with keys: `grid_w`, `grid_h`,
              `grid` (2D list of 0/color-index), `current_piece` (shape+position),
              `score` (int), `lines_cleared` (int), `game_over` (bool), `hold_piece` (str or None)
            - Line clearing: when a row is full, remove it, shift rows above down, increment score
              (100/300/500/800 for 1/2/3/4 lines)
            - Wall kick: basic wall kick on rotation near walls
            - Piece preview: next piece shown via render_state

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int) starts at 0 and
              increments on positive events (clearing one or more rows).
            - Game-over detection: when a lose condition triggers (piece locks above visible
              grid), `state` transitions to "game_over" and `game_over` (bool) becomes True.
              `tick()` after game_over is a no-op (returns without changing state).
            - `restart()` method: resets ALL state (score=0, game_over=False, grid cleared,
              new piece spawned, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class Tetris:`.
        """).strip(),
        "class_name": "Tetris",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("tick_gravity", "tick() moves piece down one row"),
            ("line_clear", "filling a row clears it and increments score"),
            ("rotation", "rotate_cw changes piece orientation"),
            ("hard_drop", "hard_drop instantly places piece"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "minesweeper": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Minesweeper as a headless
            state machine. NO external dependencies except the stdlib. NO display code.
            NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Minesweeper`
            - `__init__(self, grid_w=10, grid_h=10, num_mines=10)`: initialize grid, place mines
              randomly (NOT on first click — place at init), compute adjacency counts
            - `reveal(self, x: int, y: int) -> str`: reveal cell at (x,y); return "ok" (safe, shows
              number), "mine" (hit a mine = game over), "already_revealed", "out_of_bounds"
            - When revealing a 0 cell, recursively auto-reveal all adjacent cells (flood fill)
            - `flag(self, x: int, y: int) -> str`: toggle flag on cell; return "flagged", "unflagged",
              "already_revealed", "out_of_bounds"
            - `render_state(self) -> dict`: return dict with keys: `grid_w`, `grid_h`,
              `num_mines` (int), `flags_placed` (int), `cells_revealed` (int),
              `game_over` (bool), `won` (bool), `grid` (2D list of cell states:
              dict with x, y, revealed(bool), flagged(bool), adjacent_mines(int),
              is_mine(bool))
            - Win condition: all non-mine cells revealed
            - Game over: mine revealed

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": revealing cells advances the game; `score` (int, e.g.
              cells_revealed) starts at 0 and increments on positive events (revealing safe cell).
            - Game-over detection: when a lose condition triggers (mine revealed), `state`
              transitions to "game_over" and `game_over` (bool) becomes True. Revealing cells
              after game_over is a no-op.
            - Win detection: when a win condition triggers (all non-mine cells revealed),
              `state` transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False, grid
              reset with new random mines, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class Minesweeper:`.
        """).strip(),
        "class_name": "Minesweeper",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("reveal_safe", "revealing a non-mine cell returns 'ok'"),
            ("reveal_mine", "revealing a mine cell returns 'mine' and game_over=True"),
            ("flood_fill", "revealing a 0 cell auto-reveals adjacent cells"),
            ("flag_toggle", "flag() toggles between flagged/unflagged"),
            ("win_detection", "revealing all non-mine cells sets won=True"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "checkers": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Checkers (English draughts)
            as a headless state machine. NO external dependencies except the stdlib. NO display code.
            NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Checkers`
            - 8x8 board, 12 pieces per player, dark squares (positions [row+col] % 2 == 1)
            - `__init__(self)`: initialize standard starting position
            - `move(self, from_sq: str, to_sq: str) -> dict`: execute a move; squares are algebraic
              notation "a1"-"h8" (col letter + row number, row 1 = bottom/player 1 side).
              Return dict with keys: `valid` (bool), `captured` (list of captured piece squares),
              `promoted` (bool if piece became king), `error` (str explaining why move is invalid)
            - Movement: regular pieces move diagonally forward one square to empty dark square.
              Kings move diagonally forward OR backward one square.
            - Capture: jump over adjacent opponent piece to empty square behind it (forward for
              regular, any diagonal for kings). Mandatory capture rule: if capture available, must
              take it. Multiple consecutive captures required when possible.
            - King promotion: piece reaching opponent's back rank becomes king
            - `get_valid_moves(self, from_sq: str) -> list[str]`: return list of valid destination
              squares for the piece at from_sq
            - `render_state(self) -> dict`: return dict with keys: `board` (8x8 2D list:
              None=empty, "P1"=player1 regular, "P1K"=player1 king, "P2"=player2 regular,
              "P2K"=player2 king), `current_player` (1 or 2), `game_over` (bool),
              `winner` (int or None), `p1_pieces` (int), `p2_pieces` (int)
            - Game over: when a player has no valid moves

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `move()` advances the game; `score` (int, e.g. opponent pieces
              captured) starts at 0 and increments on positive events (capturing piece).
            - Game-over detection: when a lose condition triggers (current player has no valid
              moves), `state` transitions to "game_over" and `game_over` (bool) becomes True.
              `move()` after game_over is a no-op.
            - Win detection: when a win condition triggers (opponent has no pieces or no valid
              moves), `state` transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False, board
              to standard starting position, current_player=1, state="ready"). Reusable.

            Output ONLY the Python code. Start with `class Checkers:`.
        """).strip(),
        "class_name": "Checkers",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("valid_move", "valid diagonal move returns valid=True"),
            ("invalid_move", "moving to occupied square returns valid=False"),
            ("capture", "jump capture removes opponent piece"),
            ("king_promotion", "piece reaching back rank becomes king"),
            ("game_over", "no valid moves sets game_over=True"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "skifree": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a SkiFree-like downhill
            skiing game as a headless state machine. NO external dependencies except the stdlib.
            NO display code. NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `SkiFree`
            - `__init__(self, course_w=40, course_h=200)`: initialize course; skier at center-top
              (x=course_w//2, y=0); generate trees at random positions (not on skier start);
              generate obstacles (rocks) at random positions
            - `tick(self) -> bool`: advance one frame; skier auto-moves down 1 row per tick;
              check collision with trees/rocks; return False if crashed, True if still skiing.
              Course scrolls: new obstacles spawn ahead, old ones removed behind.
              The FIRST lines of tick() MUST be a terminal-state guard:
              if state is not "playing" OR crashed/game_over/finished/won is true, return
              immediately before changing skier_x, skier_y, score, distance_traveled, speed,
              trees, rocks, obstacles, or any other mutable state.
            - `input(self, action: str)`: accept "left"/"right" (move skier 1 cell horizontally,
              clamped to bounds); "speed_up"/"slow_down" (change auto-scroll rate)
            - `render_state(self) -> dict`: return dict with keys: `course_w`, `course_h`,
              `skier_x`, `skier_y`, `distance_traveled` (int, rows passed), `speed` (int, rows/tick),
              `crashed` (bool), `trees` (list of [x,y]), `rocks` (list of [x,y]),
              `finished` (bool, true when y >= course_h)
            - Collision: skier position overlapping any tree or rock = crash
            - Trees are 1 cell wide; rocks can be 2 cells wide (2 adjacent positions)
            - Difficulty curve: obstacle density increases as distance_traveled increases

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int, e.g.
              distance_traveled) starts at 0 and increments on positive events (row traveled).
            - Game-over detection: when a lose condition triggers (collision with tree or
              rock), `state` transitions to "game_over" and `crashed`/`game_over` (bool) is
              True. After game_over, EVERY subsequent call to `tick()` MUST return immediately
              without changing ANY mutable state: score, position (skier_x, skier_y), distance,
              obstacle lists, speed, or any other attribute. A frozen game must never un-freeze.
            - Win detection: when a win condition triggers (reaching course bottom / y >=
              course_h), `state` transitions to "won" and `finished`/`won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False,
              crashed=False, skier at center-top, obstacles regenerated, state="ready").
              The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class SkiFree:`.
        """).strip(),
        "class_name": "SkiFree",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("tick_movement", "tick() moves skier down the course"),
            ("left_right", "input('left') changes skier x position"),
            ("tree_collision", "overlapping a tree sets crashed=True"),
            ("course_completion", "reaching bottom sets finished=True"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "banana": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a Gorillas/Banana.bas
            style artillery game as a headless state machine. NO external dependencies except stdlib.
            NO display code. NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Banana`
            - Two gorillas on rooftops in a city skyline
            - `__init__(self, city_w=80, city_h=25)`: generate random skyline (building heights),
              place gorilla 1 on leftmost building, gorilla 2 on rightmost building.
              Random wind speed (-5 to 5).
            - `throw(self, angle_deg: float, velocity: float) -> dict`: simulate a banana throw.
              Calculate trajectory using physics: x(t) = v*cos(angle)*t + 0.5*wind*t^2,
              y(t) = v*sin(angle)*t - 0.5*g*t^2 (g=9.8). Check collision with buildings
              (x within building width, y <= building height at that x) and gorillas
              (distance to gorilla position < 1.5). Return dict with keys:
              `hit` (bool), `hit_type` ("building"/"gorilla1"/"gorilla2"/"ground"/"sky"/"out_of_bounds"),
              `trajectory` (list of [x,y] positions along path),
              `distance_to_target` (float, euclidean from landing to target gorilla),
              `winner` (int or None)
            - `render_state(self) -> dict`: return dict with keys: `city_w`, `city_h`,
              `skyline` (list of building heights), `gorilla1_x` (int), `gorilla2_x` (int),
              `current_player` (1 or 2), `wind` (float),
              `throws` (list of last 5 throw dicts), `game_over` (bool), `winner` (int or None)
            - Turns alternate between players
            - Game over: when a gorilla is hit
            - Banana must travel in an arc; angle 0=right, 90=straight up; velocity in m/s

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `throw()` advances the game; `score` (int, e.g. successful
              hits) starts at 0 and increments on positive events (hitting opponent gorilla).
            - Game-over detection: when a lose condition triggers (your gorilla is hit),
              `state` transitions to "game_over" and `game_over` (bool) becomes True.
              `throw()` after game_over is a no-op.
            - Win detection: when a win condition triggers (opponent gorilla is hit), `state`
              transitions to "won", `won` (bool) becomes True, and `winner` is set.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False,
              winner=None, skyline regenerated, gorillas repositioned, current_player=1,
              wind randomized, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import math` and `class Banana:`.
        """).strip(),
        "class_name": "Banana",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("throw_arc", "throw(45, 10) produces trajectory with multiple points"),
            ("building_hit", "throw at low angle hits a building"),
            ("gorilla_hit", "direct hit on gorilla returns hit_type='gorilla1' or 'gorilla2'"),
            ("turn_alternation", "current_player changes after each throw"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "pong": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements the classic Pong game
            as a headless state machine. NO external dependencies except the stdlib. NO display
            code (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Pong`
            - `__init__(self, board_w=40, board_h=20)`: initialize board; ball_x=board_w//2,
              ball_y=board_h//2; ball_dx randomly ±1; ball_dy randomly ±1; paddle1_y (left paddle)
              at board_h//2 - 2; paddle2_y (right paddle) at board_h//2 - 2; score1=0, score2=0;
              paddle_height=4
            - `tick(self) -> bool`: advance one frame. Move ball by (ball_dx, ball_dy). Ball bounces
              off top wall (y < 0 → ball_dy = -ball_dy, y = 0) and bottom wall
              (y >= board_h → ball_dy = -ball_dy, y = board_h - 1). Check paddle contact:
              if ball_x == 1 and paddle1_y <= ball_y < paddle1_y + paddle_height → bounce right
              (ball_dx = abs(ball_dx)). If ball_x == board_w - 2 and
              paddle2_y <= ball_y < paddle2_y + paddle_height → bounce left
              (ball_dx = -abs(ball_dx)). If ball_x < 0: score2 += 1, reset ball to center.
              If ball_x >= board_w: score1 += 1, reset ball to center. Always return True.
            - `input(self, action: str)`: accept "p1_up"/"p1_down" (move left paddle by ±1,
              clamped to [0, board_h - paddle_height]) and "p2_up"/"p2_down" (move right paddle)
            - `render_state(self) -> dict`: return dict with keys: `board_w`, `board_h`,
              `ball_x`, `ball_y`, `ball_dx`, `ball_dy`, `paddle1_y`, `paddle2_y`,
              `score1`, `score2`, `paddle_height`

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score1`/`score2` (ints) start at 0
              and increment on positive events (ball passes opponent paddle).
            - Game-over detection: Pong is endless by default; `game_over` stays False.
              (Optional: if a score cap is implemented, transition to "game_over" when reached.)
            - `restart()` method: resets ALL state (score1=0, score2=0, ball centered with
              random direction, paddles centered, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class Pong:`.
        """).strip(),
        "class_name": "Pong",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("pong_ball_move", "ball position changes after tick()"),
            ("pong_wall_bounce", "ball bounces off top or bottom wall (dy flips)"),
            ("pong_paddle_move", "paddle moves in response to input"),
            ("pong_scoring", "score increments when ball passes paddle"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "breakout": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Breakout as a headless
            state machine. NO external dependencies except the stdlib. NO display code
            (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Breakout`
            - `__init__(self, board_w=20, board_h=20)`: initialize. Paddle at bottom center:
              paddle_x = board_w//2 - 2, paddle_y = board_h - 1, paddle_width = 4. Ball starts on
              paddle: ball_x = paddle_x + 2, ball_y = paddle_y - 1, ball_dx = 1, ball_dy = -1.
              Bricks: 2D list board_h x board_w of bool. Fill rows 0-3 (first 4 rows) with True
              except skip ~20% random cells for gaps. score=0, lives=3, game_over=False, won=False.
            - `tick(self) -> bool`: advance one frame. Move ball (ball_x += ball_dx,
              ball_y += ball_dy). Bounce off left wall (x < 0 → invert dx), right wall
              (x >= board_w → invert dx), top wall (y < 0 → invert dy). If ball at bottom
              (y >= board_h): lives -= 1; if lives == 0 → game_over=True, return False;
              else reset ball to paddle center. Brick collision: if ball is within bounds and
              bricks[ball_y][ball_x] is True → set to False, score += 10, invert ball_dy.
              If no bricks left → won=True, game_over=True, return False. Paddle bounce:
              if ball_y == paddle_y - 1 and paddle_x <= ball_x < paddle_x + paddle_width →
              ball_dy = -abs(ball_dy) (bounce upward). Return True if not over.
            - `input(self, action: str)`: accept "left"/"right" (move paddle_x by ±2,
              clamped to [0, board_w - paddle_width])
            - `render_state(self) -> dict`: return dict with keys: `board_w`, `board_h`,
              `paddle_x`, `paddle_y`, `paddle_width`, `ball_x`, `ball_y`, `ball_dx`, `ball_dy`,
              `bricks` (2D list of bool), `score`, `lives`, `game_over`, `won`

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int) starts at 0 and
              increments on positive events (destroying a brick).
            - Game-over detection: when a lose condition triggers (lives reach 0), `state`
              transitions to "game_over" and `game_over` (bool) becomes True. `tick()` after
              game_over is a no-op.
            - Win detection: when a win condition triggers (all bricks destroyed), `state`
              transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False, ball
              on paddle, bricks regenerated, lives=3, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class Breakout:`.
        """).strip(),
        "class_name": "Breakout",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("brk_ball_moves", "ball position changes after tick()"),
            ("brk_brick_smash", "tick loop destroys at least one brick"),
            ("brk_paddle_bounce", "ball bounces when hitting paddle area"),
            ("brk_life_loss", "lives decrement when ball passes bottom"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "maze_runner": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a maze runner game
            as a headless state machine. NO external dependencies except the stdlib. NO display
            code (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `MazeRunner`
            - `__init__(self, grid_w=10, grid_h=10)`: generate a random maze on a 2D grid where
              0 = path (walkable), 1 = wall. Use a simple maze generation algorithm: start with
              all walls, then carve paths from (1,1) using randomized depth-first search or
              recursive backtracking. Set start=(1,1) and end=(grid_w-2, grid_h-2); ensure both
              are path cells. Place player at start. steps=0.
            - `input(self, action: str) -> bool`: accept "up"/"down"/"left"/"right". Move player
              one cell in that direction IF destination is within bounds (0 <= x < grid_w,
              0 <= y < grid_h) AND maze[y][x] == 0 (path). Return True if moved,
              False if blocked. After moving: steps += 1. If player reaches end position →
              won=True, game_over=True.
            - `render_state(self) -> dict`: return dict with keys: `grid_w`, `grid_h`,
              `maze` (2D list of int: 0=path, 1=wall), `player_x`, `player_y`,
              `start_x`, `start_y`, `end_x`, `end_y`, `won` (bool), `game_over` (bool),
              `steps` (int)

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `input()` advances the player; `score` (int, e.g. negative of
              steps or efficiency metric) starts at 0 and is tracked alongside `steps`.
            - Win detection: when a win condition triggers (player reaches end position),
              `state` transitions to "won" and `won` (bool) becomes True. `input()` after won
              is a no-op.
            - `restart()` method: resets ALL state (player at start, steps=0, won=False,
              game_over=False, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class MazeRunner:`.
        """).strip(),
        "class_name": "MazeRunner",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("maze_moves", "input('right') moves player on a path cell"),
            ("maze_wall_blocks", "moving into a wall cell blocks movement"),
            ("maze_reach_end", "reaching end position sets won=True"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "word_guesser": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a Hangman-style word
            guessing game as a headless state machine. NO external dependencies except the stdlib.
            NO display code (no pygame, no curses, no tkinter). NO prose, no markdown, no
            explanations.

            Requirements:
            - Class name: `WordGuesser`
            - `__init__(self)`: choose a random secret word from a built-in list of at least 20
              common English words (5-8 letters each). guessed_letters = [] (empty list),
              wrong_guesses = 0, max_guesses = 8, game_over = False, won = False.
            - `guess(self, letter: str) -> dict`: accept a single lowercase letter. If not a
              single lowercase letter → {"valid": False, "error": "invalid input"}. If already
              guessed → {"valid": False, "error": "already guessed"}. If letter IS in secret_word:
              add to guessed_letters; if all letters of secret_word are now in guessed_letters →
              game_over = True, won = True; return {"valid": True, "correct": True,
              "positions": [list of indices where letter appears]}. If letter NOT in secret_word:
              add to guessed_letters, wrong_guesses += 1; if wrong_guesses >= max_guesses →
              game_over = True, won = False; return {"valid": True, "correct": False,
              "positions": []}.
            - `render_state(self) -> dict`: return dict with keys: `secret_word` (str),
              `guessed_letters` (sorted list of str), `wrong_guesses` (int), `max_guesses` (int),
              `game_over` (bool), `won` (bool), `display` (str with unguessed letters as "_",
              e.g. "h e _ _ o")

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `guess()` advances the game; `score` (int, e.g. correct letter
              count) starts at 0 and increments on positive events (correct letter guessed).
            - Game-over detection: when a lose condition triggers (wrong_guesses reaches
              max_guesses), `state` transitions to "game_over" and `game_over` (bool) becomes
              True with `won=False`. `guess()` after game_over is a no-op.
            - Win detection: when a win condition triggers (all letters of secret_word
              guessed), `state` transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (new secret word chosen, guessed_letters
              cleared, wrong_guesses=0, game_over=False, won=False, state="ready"). Reusable.

            Output ONLY the Python code. Start with `import random` and `class WordGuesser:`.
        """).strip(),
        "class_name": "WordGuesser",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("wg_correct_letter", "guessing a letter in the word reveals positions"),
            ("wg_wrong_letter", "guessing a wrong letter increments wrong_guesses"),
            ("wg_win_word", "guessing all letters sets won=True"),
            ("wg_lose_max", "reaching max_guesses sets game_over=True and won=False"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "memory_match": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a card-matching memory
            game as a headless state machine. NO external dependencies except the stdlib. NO
            display code (no pygame, no curses, no tkinter). NO prose, no markdown, no
            explanations.

            Requirements:
            - Class name: `MemoryMatch`
            - `__init__(self, pairs=8)`: create 2 * pairs cards. Values: use letters A-H for the
              pairs (each letter appears twice). Build cards list of dicts with keys:
              `id` (int 0..2*pairs-1), `value` (str), `flipped` (bool, default False),
              `matched` (bool, default False). Shuffle cards randomly. attempts = 0,
              game_over = False, first_flip = None.
            - `flip(self, card_id: int) -> dict`: flip the card at index card_id. If card already
              matched → {"valid": False, "error": "already matched"}. If card already flipped →
              {"valid": False, "error": "already flipped"}. Flip card (set flipped=True).
              If first_flip is None (first card of turn): set first_flip = card_id; return
              {"valid": True, "first": True, "card_id": card_id, "value": card value}. If
              first_flip is set (second card of turn): attempts += 1. Compare values:
              if cards[first_flip].value == cards[card_id].value → both matched = True,
              first_flip = None; check if all matched → game_over = True; return
              {"valid": True, "match": True}. If no match → schedule both to flip back
              (flipped = False), first_flip = None; return {"valid": True, "match": False}.
            - `render_state(self) -> dict`: return dict with keys: `cards` (list of card dicts
              with id, value, flipped, matched), `pairs` (int), `attempts` (int),
              `game_over` (bool), `first_flip` (int or None)

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `flip()` advances the game; `score` (int, e.g. pairs matched)
              starts at 0 and increments on positive events (matching a pair).
            - Win detection: when a win condition triggers (all pairs matched), `state`
              transitions to "won" and `won`/`game_over` (bool) becomes True.
            - `restart()` method: resets ALL state (cards reshuffled, flipped=False,
              matched=False, attempts=0, first_flip=None, game_over=False, state="ready").
              The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class MemoryMatch:`.
        """).strip(),
        "class_name": "MemoryMatch",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("mm_flip_cards", "flip() reveals a card and returns valid dict"),
            ("mm_match_pair", "matching two cards with same value sets matched=True"),
            ("mm_mismatch_flips", "non-matching pair flips back after second card"),
            ("mm_all_matched", "game ends when all cards are matched"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
    "tic_tac_toe": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Tic-Tac-Toe with an AI
            opponent as a headless state machine. NO external dependencies except the stdlib.
            NO display code (no pygame, no curses, no tkinter). NO prose, no markdown, no
            explanations.

            Requirements:
            - Class name: `TicTacToe`
            - `__init__(self)`: 3x3 board as 2D list of None (empty), "X", or "O".
              current_player = "X" (human), winner = None, game_over = False, draw = False.
            - `move(self, row: int, col: int) -> dict`: place current_player at (row, col).
              If game_over → {"valid": False, "error": "game over"}. If out of bounds →
              {"valid": False, "error": "out of bounds"}. If occupied → {"valid": False,
              "error": "occupied"}. Else: place mark, check_winner(). Return
              {"valid": True, "winner": winner, "game_over": game_over, "draw": draw}.
              If game not over after human move → call _ai_move(), check_winner(), return
              updated state.
            - `_ai_move(self) -> None`: AI (plays "O") uses simple strategy: (1) if AI can win
              this turn, take winning cell; (2) if opponent can win next turn, block it;
              (3) take center if free; (4) take corner if free; (5) take any free side.
              Random choice among equally good options.
            - `_check_winner(self)`: check rows, columns, and both diagonals for 3 matching
              non-None marks. If found → winner = that mark, game_over = True. Else if all cells
              filled → draw = True, game_over = True.
            - `render_state(self) -> dict`: return dict with keys: `board` (3x3 2D list of
              str or None), `current_player` (str), `winner` (str or None),
              `game_over` (bool), `draw` (bool)

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `move()` advances the game; `score` (int, e.g. marks placed by
              human) starts at 0 and increments on positive events (placing a mark).
            - Game-over detection: when an end condition triggers (win or draw), `state`
              transitions to "game_over" and `game_over` (bool) becomes True. `move()` after
              game_over is a no-op.
            - Win detection: when a win condition triggers (three in a row), `winner` is set
              and `state` becomes "won" (or "game_over" with winner populated).
            - `restart()` method: resets ALL state (board cleared, current_player="X",
              winner=None, game_over=False, draw=False, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class TicTacToe:`.
        """).strip(),
        "class_name": "TicTacToe",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("ttt_legal_move", "move on empty cell returns valid=True"),
            ("ttt_illegal_reject", "move on occupied cell returns valid=False"),
            ("ttt_three_in_row", "three in a row sets winner and game_over"),
            ("ttt_board_full_draw", "full board with no winner sets draw=True"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
        ],
    },
}


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------
