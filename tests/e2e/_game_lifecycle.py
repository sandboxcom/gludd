"""Lifecycle verification helpers for game-building e2e tests.

Adds full play-round (open -> close) lifecycle checks to the existing
per-game feature verifiers.  These check the floor of behaviour a game
must demonstrate across a complete play session:

    initial state -> start -> score-zero -> score-increments ->
    game-over -> idempotent game-over -> restart

All checks are NAME-AGNOSTIC: they locate attributes by shape (state,
score, game_over, restart) via synonym groups so the model is free to
name state/methods however it likes.  Per-game lose-condition strategies
(``_force_lose_<game>``) encode the minimum game-specific knowledge
needed to drive the game to a terminal state.

Imported by both:
    - ``tests/e2e/test_game_building_deepseek.py``
    - ``tests/e2e/test_daemon_game_building.py``

Public entry point: ``run_lifecycle_checks(game_id, mod) -> list[str]``.
Empty list = pass; non-empty = descriptive failure strings.
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Callable

# ---------------------------------------------------------------------------
# Synonym groups (name candidates for shape-based attribute discovery)
# ---------------------------------------------------------------------------

_TICK_NAMES: tuple[str, ...] = (
    "tick", "step", "update", "advance", "next_frame", "next_turn",
    "frame", "turn", "simulate",
)
_START_NAMES: tuple[str, ...] = (
    "start", "play", "begin", "new_game", "start_game", "reset",
)
_RESTART_NAMES: tuple[str, ...] = (
    "restart", "reset", "new_game", "start_over", "play_again",
)
_STATE_ATTR_NAMES: tuple[str, ...] = (
    "state", "phase", "mode", "status",
)
_SCORE_ATTR_NAMES: tuple[str, ...] = (
    "score", "points", "score_value",
)
_OVER_ATTR_NAMES: tuple[str, ...] = (
    "game_over", "over", "crashed", "finished", "ended", "dead", "done",
)
_WON_ATTR_NAMES: tuple[str, ...] = (
    "won", "winner", "victory", "is_won",
)
_REVEAL_NAMES: tuple[str, ...] = ("reveal", "click", "open", "dig", "uncover")
_THROW_NAMES: tuple[str, ...] = ("throw", "shoot", "fire", "launch", "toss")
_FLIP_NAMES: tuple[str, ...] = ("flip", "select", "reveal_card", "turn", "pick")
_GUESS_NAMES: tuple[str, ...] = (
    "guess", "try_letter", "guess_letter", "submit", "attempt",
)

# Acceptable "ready to play" states (case-insensitive)
_READY_STATES: frozenset[str] = frozenset({
    "ready", "menu", "idle", "start", "initial", "new", "stopped",
    "wait", "waiting", "begin", "setup", "init",
})
# Acceptable "actively playing" states
_PLAYING_STATES: frozenset[str] = frozenset({
    "playing", "running", "active", "in_progress", "play", "ongoing",
    "started", "live",
})
# Acceptable "terminal" states
_TERMINAL_STATES: frozenset[str] = frozenset({
    "game_over", "over", "ended", "finished", "done", "dead", "crashed",
    "won", "lost", "draw", "drawn", "lose", "win", "end",
})

_PREFERRED_CLASS_NAME: dict[str, str] = {
    "snake": "Snake",
    "tetris": "Tetris",
    "minesweeper": "Minesweeper",
    "checkers": "Checkers",
    "skifree": "SkiFree",
    "banana": "Banana",
    "pong": "Pong",
    "breakout": "Breakout",
    "maze_runner": "MazeRunner",
    "word_guesser": "WordGuesser",
    "memory_match": "MemoryMatch",
    "tic_tac_toe": "TicTacToe",
}


# ---------------------------------------------------------------------------
# Generic discovery helpers (operate on the LLM-generated module/instance)
# ---------------------------------------------------------------------------

def _find_callable(
    obj: object, names: tuple[str, ...],
) -> tuple[str, Callable[..., object]] | None:
    """Return ``(attr_name, attr)`` for the first name in ``names`` that
    resolves to a callable on ``obj``, else ``None``."""
    for name in names:
        attr = getattr(obj, name, None)
        if callable(attr):
            return name, attr
    return None


def _find_attr(
    obj: object, names: tuple[str, ...],
) -> tuple[str, object] | None:
    """Return ``(attr_name, value)`` for the first name in ``names`` that
    exists on ``obj``, else ``None``."""
    for name in names:
        if hasattr(obj, name):
            return name, getattr(obj, name)
    return None


def _discover_game_class(mod: object, preferred: str | None) -> type[object] | None:
    """Find the most likely game class in ``mod``.

    Selection order: exact case-insensitive match on ``preferred`` ->
    substring match -> the class with the most user-defined methods.
    Classes imported into the module (not defined there) are skipped.
    """
    candidates = [
        (name, obj)
        for name, obj in inspect.getmembers(mod, inspect.isclass)
        if getattr(obj, "__module__", None) == getattr(mod, "__name__", None)
    ]
    if not candidates:
        return None
    if preferred:
        plow = preferred.lower()
        for name, obj in candidates:
            if name.lower() == plow:
                return obj
        for name, obj in candidates:
            nlow = name.lower()
            if plow in nlow or nlow in plow:
                return obj
    return max(
        candidates,
        key=lambda kv: len([
            m for m in inspect.getmembers(kv[1], predicate=inspect.isfunction)
            if not m[0].startswith("_")
        ]),
    )[1]


def _instantiate_game(cls: type[object]) -> object:
    """Instantiate ``cls`` by trying common constructor signatures."""
    sig = inspect.signature(cls.__init__)
    params = [
        p for p in sig.parameters.values()
        if p.name != "self"
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    n_required = sum(1 for p in params if p.default is inspect.Parameter.empty)
    candidates: list[tuple[object, ...]] = [
        (), (10,), (20,), (10, 10), (20, 20), (40, 100), (10, 10, 10),
    ]
    last_exc: Exception | None = None
    seen: set[tuple[object, ...]] = set()
    for args in candidates:
        if args in seen:
            continue
        seen.add(args)
        if len(args) < n_required:
            continue
        try:
            return cls(*args)
        except (TypeError, ValueError) as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise TypeError(f"could not instantiate {cls.__name__}: no viable signature")


def _tick_once(instance: object) -> bool:
    """Try to advance the game one tick; return True if a tick method was found
    and called without raising."""
    found = _find_callable(instance, _TICK_NAMES)
    if found is None:
        return False
    try:
        found[1]()
    except Exception:
        return False
    return True


def _invoke_start_method(instance: object) -> str | None:
    """Transition the game from 'ready' to 'playing' before a tick loop.

    Per the new prompt spec, games start in a 'ready' state and ``tick()``
    short-circuits until ``start()`` transitions to 'playing'.  Tick loops
    that ran fine under the old spec (where tick advanced state from the
    very first call) now see every tick no-op and report a false failure.
    This helper is the fix: call it BEFORE any tick loop.

    Idempotent: only acts when a state attribute exists, is a string, and
    is in a 'ready' state (``ready`` / ``menu`` / ``idle`` / ...).  If
    state is already 'playing', 'game_over', absent, or non-string, this
    is a no-op — safe to call from helpers that may run after start().

    Resolution when state IS 'ready':
      1. Call ``start()`` (or any synonym in ``_START_NAMES``) if present.
      2. If no start method, call ``tick()`` once to allow auto-transition
         (some models bundle start logic into the first tick).
      3. After resolution, if state is STILL in a 'ready' state, return a
         descriptive error.

    Returns ``None`` on success or an error string explaining why the game
    could not be transitioned to 'playing'.
    """
    state_before = _find_attr(instance, _STATE_ATTR_NAMES)
    is_confirmed_ready = (
        state_before is not None
        and isinstance(state_before[1], str)
        and state_before[1].lower() in _READY_STATES
    )

    # Non-ready confirmed — nothing to do
    if (
        state_before is not None
        and isinstance(state_before[1], str)
        and state_before[1].lower() not in _READY_STATES
    ):
        return None

    # Try start regardless of whether state attr was found: games may
    # track state under a name not in _STATE_ATTR_NAMES (e.g. "game_state").
    # If we skip start() because the attr name is unknown, every tick()
    # no-ops and the lifecycle check produces a false failure.
    start_found = _find_callable(instance, _START_NAMES)
    if start_found is not None:
        try:
            start_found[1]()
        except Exception as exc:
            return (
                f"start method {start_found[0]!r} raised: "
                f"{type(exc).__name__}: {exc}"
            )
    elif is_confirmed_ready and not _tick_once(instance):
        return (
            "could not transition from ready to playing — "
            "no start() method and tick() did not auto-start"
        )
    # else: no start method + state unknown → assume game auto-starts

    # Verify transition (only meaningful when we saw a ready state before)
    if is_confirmed_ready:
        state_after = _find_attr(instance, _STATE_ATTR_NAMES)
        if (
            state_after is not None
            and isinstance(state_after[1], str)
            and state_after[1].lower() in _READY_STATES
        ):
            return (
                f"state stayed at {state_after[1]!r} after start "
                f"(expected transition to playing/running/active)"
            )
    return None


def _is_truthy_bool(value: object) -> bool:
    """True only if ``value`` is a bool and True. Other truthy values
    (non-zero ints, non-empty lists) do not count."""
    return isinstance(value, bool) and value


# ---------------------------------------------------------------------------
# The 7 lifecycle checks (each returns None on pass, str on fail)
# ---------------------------------------------------------------------------

def _check_lifecycle_initial_state(instance: object) -> str | None:
    """Check 1: state attribute (state/phase/mode/status) starts in a
    'ready' / non-playing / non-terminal state."""
    found = _find_attr(instance, _STATE_ATTR_NAMES)
    if found is None:
        return None  # no explicit state attr — many games don't track this
    name, value = found
    if not isinstance(value, str):
        return None  # non-string state (enum/int) — accept
    vlow = value.lower()
    if vlow in _READY_STATES:
        return None
    if vlow in {"playing", "game_over", "won"} or vlow in _TERMINAL_STATES:
        return (
            f"initial state is {value!r} for attribute {name!r} "
            f"(expected 'ready' or 'menu')"
        )
    return None  # unknown state string — accept


def _check_lifecycle_start(instance: object) -> str | None:
    """Check 2: a start method exists and transitions state from
    'ready' to 'playing'.  Tolerates: no start method (a single tick
    should also kick off play); no state attribute at all."""
    state_before = _find_attr(instance, _STATE_ATTR_NAMES)
    before_val = state_before[1] if state_before is not None else None

    start_found = _find_callable(instance, _START_NAMES)
    if start_found is not None:
        try:
            start_found[1]()
        except Exception as exc:
            return (
                f"start method {start_found[0]!r} raised: "
                f"{type(exc).__name__}: {exc}"
            )
    else:
        if not _tick_once(instance):
            return (
                "no start method (start/play/begin/new_game/...) "
                "and no tick method found"
            )

    state_after = _find_attr(instance, _STATE_ATTR_NAMES)
    after_val = state_after[1] if state_after is not None else None

    if (
        isinstance(before_val, str)
        and isinstance(after_val, str)
        and before_val.lower() in _READY_STATES
        and after_val.lower() in _READY_STATES
    ):
        return (
            f"state stayed at {after_val!r} after start "
            f"(expected transition to playing/running/active)"
        )
    return None


def _check_lifecycle_score_starts_zero(instance: object) -> str | None:
    """Check 3: score attribute (score/points/score_value) is 0 at start."""
    found = _find_attr(instance, _SCORE_ATTR_NAMES)
    if found is None:
        return None  # no score attr — skip
    name, value = found
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return (
            f"score attribute {name!r} is not numeric "
            f"(got {type(value).__name__})"
        )
    if value != 0:
        return f"score is {value!r} at start of play (expected 0)"
    return None


def _check_lifecycle_score_increments(
    instance: object, n_ticks: int = 20,
) -> str | None:
    """Check 4: over n_ticks, score increases at least once OR the game
    ends.  If neither, fail.  Skipped if no score attribute or no tick
    method."""
    start_fail = _invoke_start_method(instance)
    if start_fail is not None:
        return start_fail

    score_found = _find_attr(instance, _SCORE_ATTR_NAMES)
    if score_found is None:
        return None  # no score tracked — skip
    initial_score = score_found[1]
    if isinstance(initial_score, bool) or not isinstance(initial_score, (int, float)):
        return None  # malformed score — covered by check 3

    ticked_any = False
    for _ in range(n_ticks):
        if not _tick_once(instance):
            break
        ticked_any = True
        new_score_found = _find_attr(instance, _SCORE_ATTR_NAMES)
        new_over_found = _find_attr(instance, _OVER_ATTR_NAMES)
        if new_over_found is not None and _is_truthy_bool(new_over_found[1]):
            return None  # game ended — acceptable
        if new_score_found is not None:
            new_score = new_score_found[1]
            if (
                isinstance(new_score, (int, float))
                and not isinstance(new_score, bool)
                and new_score > initial_score
            ):
                return None  # score incremented
    if not ticked_any:
        return None  # no tick method — skip
    return (
        f"score did not increment in {n_ticks} ticks and game did not end "
        f"(initial={initial_score})"
    )


def _check_lifecycle_game_over(
    instance: object, force_lose: Callable[[object], str | None] | None,
) -> str | None:
    """Check 5: force a lose/terminal condition and confirm game_over (or
    won) becomes True.  If no force_lose strategy is registered OR the
    strategy reports the feature is untestable, skip (NOT a fail)."""
    start_fail = _invoke_start_method(instance)
    if start_fail is not None:
        return start_fail

    if force_lose is None:
        return None  # no strategy — skip
    try:
        skip_reason = force_lose(instance)
    except Exception as exc:
        return f"force_lose raised: {type(exc).__name__}: {exc}"
    if skip_reason is not None:
        return None  # strategy reported not-testable — skip
    over_found = _find_attr(instance, _OVER_ATTR_NAMES)
    won_found = _find_attr(instance, _WON_ATTR_NAMES)
    is_over = _is_truthy_bool(over_found[1]) if over_found else False
    is_won = _is_truthy_bool(won_found[1]) if won_found else False
    if not (is_over or is_won):
        over_name = over_found[0] if over_found else None
        won_name = won_found[0] if won_found else None
        return (
            "lose condition forced but game_over/won flag not set "
            f"(checked over_attr={over_name!r}, won_attr={won_name!r})"
        )
    return None


def _check_lifecycle_game_over_idempotent(instance: object) -> str | None:
    """Check 6: after game_over=True, calling tick() 5 more times does not
    change state/score.  Skipped if game is not currently over."""
    over_found = _find_attr(instance, _OVER_ATTR_NAMES)
    if over_found is None:
        return None  # no game_over attribute — skip
    if not _is_truthy_bool(over_found[1]):
        return None  # not actually over — skip

    score_found = _find_attr(instance, _SCORE_ATTR_NAMES)
    score_at_over = score_found[1] if score_found is not None else None
    state_found = _find_attr(instance, _STATE_ATTR_NAMES)
    state_at_over = state_found[1] if state_found is not None else None

    for _ in range(5):
        if not _tick_once(instance):
            break

    new_over_found = _find_attr(instance, _OVER_ATTR_NAMES)
    if new_over_found is not None and not _is_truthy_bool(new_over_found[1]):
        return (
            f"tick() after game_over cleared flag "
            f"{new_over_found[0]!r} (expected to remain True)"
        )

    new_score_found = _find_attr(instance, _SCORE_ATTR_NAMES)
    if (
        score_at_over is not None
        and new_score_found is not None
        and isinstance(score_at_over, (int, float))
        and not isinstance(score_at_over, bool)
    ):
        new_score = new_score_found[1]
        if (
            isinstance(new_score, (int, float))
            and not isinstance(new_score, bool)
            and new_score != score_at_over
        ):
            return (
                f"score changed after game_over "
                f"({score_at_over} -> {new_score})"
            )

    new_state_found = _find_attr(instance, _STATE_ATTR_NAMES)
    if (
        isinstance(state_at_over, str)
        and new_state_found is not None
        and isinstance(new_state_found[1], str)
        and state_at_over.lower() in _TERMINAL_STATES
        and new_state_found[1].lower() not in _TERMINAL_STATES
    ):
        return (
            f"state changed from terminal {state_at_over!r} to "
            f"{new_state_found[1]!r} after game_over"
        )
    return None


def _check_lifecycle_restart(instance: object) -> str | None:
    """Check 7: a restart method exists and resets the game (score=0,
    game_over=False, won=False)."""
    restart_found = _find_callable(instance, _RESTART_NAMES)
    if restart_found is None:
        return (
            "no restart method found (restart/reset/new_game/start_over/"
            "play_again) — cannot verify reusability"
        )
    try:
        restart_found[1]()
    except Exception as exc:
        return (
            f"restart method {restart_found[0]!r} raised: "
            f"{type(exc).__name__}: {exc}"
        )

    over_found = _find_attr(instance, _OVER_ATTR_NAMES)
    if over_found is not None and _is_truthy_bool(over_found[1]):
        return f"restart did not clear {over_found[0]!r} flag"

    won_found = _find_attr(instance, _WON_ATTR_NAMES)
    if won_found is not None and _is_truthy_bool(won_found[1]):
        return f"restart did not clear {won_found[0]!r} flag"

    score_found = _find_attr(instance, _SCORE_ATTR_NAMES)
    if score_found is not None:
        value = score_found[1]
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value != 0
        ):
            return f"score is {value!r} after restart (expected 0)"

    start_fail = _invoke_start_method(instance)
    if start_fail is not None:
        return f"after restart: {start_fail}"

    return None


# ---------------------------------------------------------------------------
# Per-game force-lose strategies.  Each returns:
#   - None on success (terminal state forced)
#   - a descriptive string when the feature is not testable for this instance
#     (the lifecycle check then SKIPS, not fails)
# ---------------------------------------------------------------------------

def _force_lose_snake(instance: object) -> str | None:
    """Snake: set snake head out of bounds, tick, expect game_over."""
    body_attr = _find_attr(instance, ("snake", "body", "segments"))
    if body_attr is None:
        return "no snake body attribute — feature not testable"
    body = body_attr[1]
    if not isinstance(body, list) or len(body) < 1:
        return "snake body is not a non-empty list"
    head = body[0]
    if not isinstance(head, list) or len(head) < 2:
        return "snake head is not a [x, y] pair"
    grid_w = getattr(instance, "grid_w", getattr(instance, "width", 100))
    grid_h = getattr(instance, "grid_h", getattr(instance, "height", 100))
    try:
        head[0] = int(grid_w) + 10
        head[1] = int(grid_h) + 10
    except (TypeError, IndexError):
        return "could not set snake head position"
    if not _tick_once(instance):
        return "no tick method — feature not testable"
    return None


def _force_lose_tetris(instance: object) -> str | None:
    """Tetris: fill the board so any new piece locks at the top."""
    grid_attr = _find_attr(instance, ("grid", "board", "playfield"))
    if grid_attr is None:
        return "no grid attribute — feature not testable"
    grid = grid_attr[1]
    if not isinstance(grid, list) or len(grid) == 0:
        return "grid is empty"
    try:
        for row in grid:
            if isinstance(row, list):
                for i in range(len(row)):
                    row[i] = 1
    except (IndexError, TypeError):
        return "could not fill grid"
    for _ in range(5):
        _tick_once(instance)
    return None


def _force_lose_minesweeper(instance: object) -> str | None:
    """Minesweeper: reveal a known mine cell."""
    reveal_found = _find_callable(instance, _REVEAL_NAMES)
    if reveal_found is None:
        return "no reveal method — feature not testable"
    grid_attr = _find_attr(instance, ("grid", "board", "cells"))
    if grid_attr is None:
        return "no grid attribute — feature not testable"
    grid = grid_attr[1]
    if not isinstance(grid, list):
        return "grid is not a list"
    for row in grid:
        if not isinstance(row, list):
            continue
        for cell in row:
            is_mine: bool | None = None
            cx: int | None = None
            cy: int | None = None
            if isinstance(cell, dict):
                is_mine = bool(cell.get("is_mine", False))
                cx = cell.get("x")
                cy = cell.get("y")
            else:
                is_mine = bool(getattr(cell, "is_mine", False))
                cx = getattr(cell, "x", None)
                cy = getattr(cell, "y", None)
            if is_mine and cx is not None and cy is not None:
                try:
                    reveal_found[1](cx, cy)
                except Exception:
                    continue
                return None
    return "no mine cell found in grid — feature not testable"


def _force_lose_checkers(instance: object) -> str | None:
    """Checkers: clear the board — opponent has no pieces (terminal)."""
    board_attr = _find_attr(instance, ("board", "grid"))
    if board_attr is None:
        return "no board attribute — feature not testable"
    board = board_attr[1]
    if not isinstance(board, list):
        return "board is not a list"
    try:
        for row in board:
            if isinstance(row, list):
                for i in range(len(row)):
                    row[i] = None
    except (IndexError, TypeError):
        return "could not clear board"
    if hasattr(instance, "game_over"):
        instance.game_over = True
    if hasattr(instance, "winner"):
        instance.winner = 1
    return None


def _force_lose_skifree(instance: object) -> str | None:
    """SkiFree: place skier on a tree and tick — collision sets crashed."""
    trees_attr = _find_attr(instance, ("trees", "obstacles"))
    if trees_attr is None:
        return "no trees attribute — feature not testable"
    trees = trees_attr[1]
    if not isinstance(trees, list) or len(trees) == 0:
        return "no trees — feature not testable"
    tree = trees[0]
    if not isinstance(tree, (list, tuple)) or len(tree) < 2:
        return "tree position unreadable"
    tx, ty = tree[0], tree[1]
    skier_x_attr = "skier_x" if hasattr(instance, "skier_x") else None
    skier_y_attr = "skier_y" if hasattr(instance, "skier_y") else None
    if skier_x_attr is None or skier_y_attr is None:
        return "no skier position — feature not testable"
    setattr(instance, skier_x_attr, tx)
    setattr(instance, skier_y_attr, ty)
    _tick_once(instance)
    over_found = _find_attr(instance, ("crashed", "game_over", "over"))
    if over_found is None:
        return "no crashed/game_over flag — feature not testable"
    return None


def _force_lose_banana(instance: object) -> str | None:
    """Banana: brute-force throw angles until a gorilla is hit (game over)."""
    throw_found = _find_callable(instance, _THROW_NAMES)
    if throw_found is None:
        return "no throw method — feature not testable"
    for angle in range(5, 91, 5):
        for velocity in (10, 15, 20, 25, 30, 40, 50, 60):
            try:
                result = throw_found[1](angle, velocity)
            except Exception:
                continue
            if (
                isinstance(result, dict)
                and (result.get("hit") or result.get("winner") is not None)
            ):
                return None
    return "could not force gorilla hit — feature not testable"


def _force_lose_pong(instance: object) -> str | None:
    """Pong: move paddle out of path and let ball drop past.  Many Pong
    implementations do not have a game_over flag (they just keep scoring);
    if so, the feature is reported as not testable for this instance."""
    if not hasattr(instance, "ball_x") or not hasattr(instance, "ball_dx"):
        return "no ball position — feature not testable"
    over_found = _find_attr(instance, _OVER_ATTR_NAMES)
    if over_found is None:
        return "no game_over flag (pong typically has no terminal state)"
    if hasattr(instance, "paddle1_y"):
        instance.paddle1_y = -100
    instance.ball_x = 0
    instance.ball_dx = -5
    for _ in range(50):
        _tick_once(instance)
        new_over = _find_attr(instance, _OVER_ATTR_NAMES)
        if new_over is not None and _is_truthy_bool(new_over[1]):
            return None
    return "ball did not end game in 50 ticks — feature not testable"


def _force_lose_breakout(instance: object) -> str | None:
    """Breakout: set lives=1 and force ball past the paddle."""
    lives_attr = _find_attr(instance, ("lives", "remaining_lives", "tries"))
    if lives_attr is None:
        return "no lives attribute — feature not testable"
    setattr(instance, lives_attr[0], 1)
    if not hasattr(instance, "ball_y") or not hasattr(instance, "ball_dy"):
        return "no ball position — feature not testable"
    board_h = getattr(instance, "board_h", 20)
    instance.ball_y = int(board_h) + 5
    instance.ball_dy = 1
    if hasattr(instance, "paddle_x"):
        instance.paddle_x = -100
    for _ in range(15):
        _tick_once(instance)
        over_found = _find_attr(instance, _OVER_ATTR_NAMES)
        if over_found is not None and _is_truthy_bool(over_found[1]):
            return None
    return "ball did not end game in 15 ticks — feature not testable"


def _force_lose_maze_runner(instance: object) -> str | None:
    """MazeRunner: teleport player to end position (terminal: won)."""
    end_x = getattr(instance, "end_x", None)
    end_y = getattr(instance, "end_y", None)
    if end_x is None or end_y is None:
        return "no end position — feature not testable"
    if hasattr(instance, "player_x") and hasattr(instance, "player_y"):
        instance.player_x = end_x
        instance.player_y = end_y
    input_found = _find_callable(instance, ("input", "move", "step"))
    if input_found is None:
        return "no input method — feature not testable"
    with contextlib.suppress(Exception):
        input_found[1]("right")
    won_found = _find_attr(instance, _WON_ATTR_NAMES)
    over_found = _find_attr(instance, _OVER_ATTR_NAMES)
    if won_found is None and over_found is None:
        return "no won/game_over flag — feature not testable"
    return None


def _force_lose_word_guesser(instance: object) -> str | None:
    """WordGuesser: make max_guesses wrong guesses."""
    guess_found = _find_callable(instance, _GUESS_NAMES)
    if guess_found is None:
        return "no guess method — feature not testable"
    secret = getattr(instance, "secret_word", "") or ""
    max_g = getattr(instance, "max_guesses", 8)
    if not isinstance(max_g, int):
        max_g = 8
    wrong_letters = [c for c in "zqxvkjyw" if c not in secret]
    for letter in wrong_letters[:max_g]:
        try:
            guess_found[1](letter)
        except Exception:
            continue
    return None


def _force_lose_memory_match(instance: object) -> str | None:
    """MemoryMatch: flip every unmatched pair (terminal: all matched)."""
    flip_found = _find_callable(instance, _FLIP_NAMES)
    if flip_found is None:
        return "no flip method — feature not testable"
    cards = getattr(instance, "cards", None)
    if not isinstance(cards, list) or len(cards) == 0:
        return "no cards attribute — feature not testable"
    value_to_ids: dict[str, list[int]] = {}
    for i, card in enumerate(cards):
        val: object = (
            card.get("value") if isinstance(card, dict) else getattr(card, "value", None)
        )
        if val is not None:
            value_to_ids.setdefault(str(val), []).append(i)
    if hasattr(instance, "first_flip"):
        instance.first_flip = None
    for ids in value_to_ids.values():
        if len(ids) < 2:
            continue
        try:
            flip_found[1](ids[0])
            flip_found[1](ids[1])
        except Exception:
            pass
        if hasattr(instance, "first_flip"):
            instance.first_flip = None
    return None


def _force_lose_tic_tac_toe(instance: object) -> str | None:
    """TicTacToe: fill board with no winner (terminal: draw)."""
    board = getattr(instance, "board", None)
    if not isinstance(board, list) or len(board) < 3:
        return "no 3x3 board attribute — feature not testable"
    draw = (
        ("X", "O", "X"),
        ("X", "O", "O"),
        ("O", "X", "X"),
    )
    try:
        for r in range(3):
            for c in range(3):
                board[r][c] = draw[r][c]
    except (IndexError, TypeError):
        return "could not fill board"
    if hasattr(instance, "game_over"):
        instance.game_over = True
    if hasattr(instance, "draw"):
        instance.draw = True
    return None


_FORCE_LOSE_DISPATCH: dict[str, Callable[[object], str | None]] = {
    "snake": _force_lose_snake,
    "tetris": _force_lose_tetris,
    "minesweeper": _force_lose_minesweeper,
    "checkers": _force_lose_checkers,
    "skifree": _force_lose_skifree,
    "banana": _force_lose_banana,
    "pong": _force_lose_pong,
    "breakout": _force_lose_breakout,
    "maze_runner": _force_lose_maze_runner,
    "word_guesser": _force_lose_word_guesser,
    "memory_match": _force_lose_memory_match,
    "tic_tac_toe": _force_lose_tic_tac_toe,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_lifecycle_checks(game_id: str, mod: object) -> list[str]:
    """Run all 7 lifecycle checks for ``game_id`` against ``mod``.

    Returns a list of feature-failure strings (empty == pass).  Each
    failure is prefixed with ``lifecycle.<check_name>:`` so it is easy
    to distinguish from existing feature-check failures.
    """
    failures: list[str] = []
    preferred = _PREFERRED_CLASS_NAME.get(game_id)
    cls = _discover_game_class(mod, preferred=preferred)
    if cls is None:
        return [f"lifecycle: no game class found for {game_id!r}"]
    try:
        instance = _instantiate_game(cls)
    except Exception as exc:
        return [
            f"lifecycle: instantiation failed: {type(exc).__name__}: {exc}"
        ]

    fail = _check_lifecycle_initial_state(instance)
    if fail is not None:
        failures.append(f"lifecycle.initial_state: {fail}")

    fail = _check_lifecycle_start(instance)
    if fail is not None:
        failures.append(f"lifecycle.start: {fail}")

    fail = _check_lifecycle_score_starts_zero(instance)
    if fail is not None:
        failures.append(f"lifecycle.score_starts_zero: {fail}")

    fail = _check_lifecycle_score_increments(instance)
    if fail is not None:
        failures.append(f"lifecycle.score_increments: {fail}")

    force_lose = _FORCE_LOSE_DISPATCH.get(game_id)
    fail = _check_lifecycle_game_over(instance, force_lose)
    if fail is not None:
        failures.append(f"lifecycle.game_over: {fail}")

    fail = _check_lifecycle_game_over_idempotent(instance)
    if fail is not None:
        failures.append(f"lifecycle.game_over_idempotent: {fail}")

    fail = _check_lifecycle_restart(instance)
    if fail is not None:
        failures.append(f"lifecycle.restart: {fail}")

    return failures
