"""E2E: full game-dev pipeline across ALL 24 local models.

Iterates every locally-available GGUF model through the complete
planner → coder → reviewer pipeline, generating 4 game types per model.
Records a pass/fail summary matrix and writes results to
/tmp/gludd-game-dev-pipeline-results.json.

Pipeline per model:
  1. Resolve GGUF files (planner/coder=<model under test>, reviewer=Qwen2.5-0.5B)
  2. Serve each on localhost via LocalInferenceManager
  3. Wire ModelGateway with role-specific profiles
  4. MultiModelGamePipeline.generate() for snake, pong, breakout, tetris
  5. AST-parse + method-signature verification + importability check

CI filters (LOCAL_MODEL_FILTER='ci-safe' / env var GAME_DEV_CI_SAFE=1):
    only models with ci_safe=True (6 of 24, ~1.4 GB total).

Run:
    GAME_DEV_CI_SAFE=1 uv run pytest tests/e2e/test_game_dev_full_pipeline.py -v -s
    make test-e2e-game-pipeline CI_SAFE=1
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import json
import os
import shutil
import socket
import tempfile
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._game_lifecycle import run_lifecycle_checks
from tests.e2e._local_model_configs import (
    E2EModelEntry,
    category_counts,
    model_count,
    require_model,
    select_models,
)

# ---------------------------------------------------------------------------
# Game prompts (4 types: snake, pong, breakout, platformer)
# ---------------------------------------------------------------------------

_TARGET_GAMES: dict[str, dict[str, Any]] = {
    "snake": {
        "class_name": "Snake",
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Snake as a headless
            state machine. NO external deps except stdlib. NO display code. NO prose/markdown.

            Requirements:
            - Class name: Snake
            - __init__(self, grid_w=20, grid_h=20): grid, snake centered facing right, food placed
            - tick(self) -> bool: advance frame; return False if game over (wall/self), else True
            - input(self, action: str): change direction; "up"/"down"/"left"/"right"
            - render_state(self) -> dict: keys grid_w, grid_h, snake (list of [x,y]), food ([x,y]),
              score (int), game_over (bool)
            - spawn_food(self): place food at random empty cell
            - Eating food: head overlaps food → grow 1, score+1, new food

            Lifecycle (MANDATORY):
            - state attribute starts "ready" (NOT "playing")
            - start() → "playing"
            - score starts 0, increments on food
            - game over → state="game_over", game_over=True; tick() after is no-op
            - restart() → score=0, game_over=False, state="ready"

            Output ONLY Python code. Start with: import random; class Snake:
        """).strip(),
    },
    "pong": {
        "class_name": "Pong",
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Pong as a headless
            state machine. NO external deps except stdlib. NO display code. NO prose/markdown.

            Requirements:
            - Class name: Pong
            - __init__(self, board_w=40, board_h=20): ball centered, random dx/dy ±1, left paddle
              (paddle1_y) at center, right paddle (paddle2_y) at center, score1=0, score2=0,
              paddle_height=4
            - tick(self) -> bool: move ball, bounce off top/bottom walls, bounce off paddles,
              score on out-of-bounds, reset ball. Always return True.
            - input(self, action: str): "p1_up"/"p1_down", "p2_up"/"p2_down"
            - render_state(self) -> dict: keys board_w, board_h, ball_x, ball_y, ball_dx, ball_dy,
              paddle1_y, paddle2_y, score1, score2, paddle_height

            Lifecycle (MANDATORY): state "ready" → start() → "playing" → score starts 0 →
            scores increment → restart() resets all.

            Output ONLY Python code. Start with: import random; class Pong:
        """).strip(),
    },
    "breakout": {
        "class_name": "Breakout",
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Breakout as a headless
            state machine. NO external deps except stdlib. NO display code. NO prose/markdown.

            Requirements:
            - Class name: Breakout
            - __init__(self, board_w=20, board_h=20): paddle at bottom center, width=4. Ball on
              paddle: ball_dx=1, ball_dy=-1. Bricks: 4 rows of bool, ~20% gaps. score=0, lives=3.
            - tick(self) -> bool: move ball, wall bounce, paddle bounce. If ball at bottom: lives-=1;
              lives==0 → game_over. Brick hit: brick=False, score+=10. No bricks left → won.
            - input(self, action: str): "left"/"right" (move paddle by ±2)
            - render_state(self) -> dict: keys board_w, board_h, paddle_x, paddle_y, paddle_width,
              ball_x, ball_y, ball_dx, ball_dy, bricks, score, lives, game_over, won

            Lifecycle (MANDATORY): state "ready" → start() → "playing" → score starts 0 →
            score increments on brick hit → game over / won → restart() resets all.

            Output ONLY Python code. Start with: import random; class Breakout:
        """).strip(),
    },
    "platformer": {
        "class_name": "Platformer",
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a simple platformer
            as a headless state machine. NO external deps except stdlib. NO display code.
            NO prose/markdown.

            Requirements:
            - Class name: Platformer
            - __init__(self, grid_w=40, grid_h=20): player at (2, grid_h-3). Platforms: 10 random
              horizontal lines 4-8 cells wide at varying heights. Goal at (grid_w-2, 1).
              score=0, lives=3, game_over=False, won=False.
            - tick(self) -> bool: apply gravity (player_y+=1 if not on platform). Check collision
              with platforms (player lands on top). Check goal (player at goal → won=True).
              Check fall off bottom (player_y >= grid_h → lives-=1; lives==0 → game_over).
              Advance one frame. Return True unless game_over or won.
            - input(self, action: str): "left"/"right" (move ±1), "jump" (player_y-=2 if on ground)
            - render_state(self) -> dict: keys grid_w, grid_h, player_x, player_y, platforms (list of
              {x, y, w}), goal_x, goal_y, score, lives, game_over, won

            Lifecycle (MANDATORY): state "ready" → start() → "playing" → score starts 0 →
            score increments on reaching goal → game over (lives=0) or won → restart() resets all.

            Output ONLY Python code. Start with: import random; class Platformer:
        """).strip(),
    },
}

_GAME_IDS = ("snake", "pong", "breakout", "platformer")

# ---------------------------------------------------------------------------
# Required method name groups (name-agnostic discovery)
# ---------------------------------------------------------------------------

_START_NAMES = frozenset({"start", "play", "begin", "new_game", "start_game"})
_RESTART_NAMES = frozenset({"restart", "reset", "new_game", "start_over", "play_again"})
_TICK_NAMES = frozenset({"tick", "step", "update", "advance", "next_frame", "frame"})
_OVER_NAMES = frozenset({"is_game_over", "game_over", "over", "crashed", "dead", "done"})
_SCORE_NAMES = frozenset({"score", "get_score", "points"})


# ---------------------------------------------------------------------------
# Dependency / RAM checks
# ---------------------------------------------------------------------------

_HAS_LLAMA = importlib.util.find_spec("llama_cpp") is not None
_HAS_HF = importlib.util.find_spec("huggingface_hub") is not None


def _free_ram_mb() -> int:
    import subprocess

    try:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        page_size = 16384
        free_pages = 0
        for line in result.stdout.splitlines():
            if "page size of" in line:
                page_size = int(line.strip().split()[-1])
            if "Pages free:" in line:
                free_pages = int(line.strip().split(":")[-1].strip().rstrip("."))
        if free_pages > 0:
            return (free_pages * page_size) // (1024 * 1024)
        return 0
    except Exception:
        return 0


def _deps_reason(model_list: list[E2EModelEntry]) -> str | None:
    missing: list[str] = []
    if not _HAS_LLAMA:
        missing.append("llama-cpp-python")
    if not _HAS_HF:
        missing.append("huggingface_hub")
    if missing:
        return f"Missing deps: {', '.join(missing)}"

    # Estimate RAM: planner (~224MB) + reviewer (~487MB) + models under test
    coders_ram = sum(m.size_mb for m in model_list)
    total_ram = 224 + 487 + coders_ram
    free_ram = _free_ram_mb()
    if 0 < free_ram < total_ram:
        return f"Insufficient RAM: {free_ram}MB free, need ~{total_ram}MB for {len(model_list) + 2} models"
    return None


# ---------------------------------------------------------------------------
# CI-safe filtering
# ---------------------------------------------------------------------------

_CI_SAFE_ONLY = os.environ.get("GAME_DEV_CI_SAFE", os.environ.get("CI_SAFE", "")).strip() in (
    "1",
    "true",
    "yes",
    "True",
    "YES",
)
_LIVE_MODEL_E2E = os.environ.get("GLUDD_LIVE_MODEL_E2E") == "1"
_TARGET_MODEL = os.environ.get("GAME_DEV_MODEL", "").strip()
_TARGET_GAME_ENV = os.environ.get("GAME_DEV_GAME", "").strip().lower()

_ALL_MODELS = select_models(ci_safe=_CI_SAFE_ONLY, target=_TARGET_MODEL)

# Fixed planner + reviewer are derived from the same registry as coder models.
_E2E_CONTEXT_CEILING = 8192
_E2E_OUTPUT_TOKEN_BUDGET = 1024
_LOCAL_MODEL_HOST = "127.0.0.1"


def _entry_config(model: E2EModelEntry) -> dict[str, Any]:
    return {
        "name": model.name,
        "repo": model.repo,
        "filename": model.filename,
        "context_size": min(model.context_size, _E2E_CONTEXT_CEILING),
    }


def _role_config(name_or_alias: str) -> dict[str, Any]:
    return _entry_config(require_model(name_or_alias))


def _role_configs_for_model(model: E2EModelEntry) -> dict[str, dict[str, Any]]:
    """Assign planning/coding to the evaluated model and a stable Qwen reviewer."""
    target_config = _entry_config(model)
    return {
        "planner": dict(target_config),
        "coder": dict(target_config),
        "reviewer": dict(_REVIEWER_CFG),
    }


def _group_roles_by_artifact(
    role_configs: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for role, config in role_configs.items():
        key = (str(config["repo"]), str(config["filename"]))
        groups.setdefault(key, []).append(role)
    return groups


def _group_roles_by_runtime(
    role_configs: dict[str, dict[str, Any]],
    local_paths: dict[str, str],
) -> dict[tuple[str, int], list[str]]:
    groups: dict[tuple[str, int], list[str]] = {}
    for role, config in role_configs.items():
        key = (local_paths[role], int(config["context_size"]))
        groups.setdefault(key, []).append(role)
    return groups


def _payload_limits(context_size: int) -> tuple[int, int]:
    """Reserve a bounded output budget while allowing review of generated code."""
    if context_size <= _E2E_OUTPUT_TOKEN_BUDGET:
        raise ValueError(
            f"context_size must exceed output budget {_E2E_OUTPUT_TOKEN_BUDGET}: {context_size}"
        )
    return context_size - _E2E_OUTPUT_TOKEN_BUDGET, _E2E_OUTPUT_TOKEN_BUDGET


async def _start_grouped_servers(
    manager: Any,
    role_configs: dict[str, dict[str, Any]],
    local_paths: dict[str, str],
    port_factory: Callable[[], int] | None = None,
) -> dict[str, int]:
    from general_ludd.infra.local_inference import LocalServerConfig

    ports: dict[str, int] = {}
    allocate_port = port_factory or _find_free_port
    try:
        for runtime_roles in _group_roles_by_runtime(role_configs, local_paths).values():
            role = runtime_roles[0]
            config_data = role_configs[role]
            port = allocate_port()
            server_config = LocalServerConfig(
                engine="llamacpp",
                model_path=local_paths[role],
                host=_LOCAL_MODEL_HOST,
                port=port,
                gpu_layers=0,
                context_size=int(config_data["context_size"]),
                startup_timeout=120.0,
            )
            server = manager.create_server(server_config)
            try:
                await manager.start_server(server.server_id)
            except Exception as exc:
                raise RuntimeError(f"Server start failed for {config_data['name']}: {exc}") from exc
            for runtime_role in runtime_roles:
                ports[runtime_role] = port
    except BaseException:
        with contextlib.suppress(Exception):
            await manager.stop_all()
        raise
    return ports


_REVIEWER_CFG = _role_config("Qwen2.5-0.5B")

_REASON = _deps_reason(_ALL_MODELS)
if _REASON is not None:
    pytestmark = pytest.mark.skip(reason=_REASON)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class GameResult:
    game_id: str
    ast_valid: bool
    importable: bool
    lines_of_code: int
    elapsed_ms: int
    error: str = ""


@dataclass
class ModelResult:
    model_name: str
    category: str
    size_mb: int
    ci_safe: bool
    games: dict[str, GameResult] = field(default_factory=dict)
    total_ms: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _find_cached_gguf(cache_dir: str, filename: str) -> str | None:
    cache = Path(cache_dir)
    if not cache.is_dir():
        return None
    direct = cache / filename
    if direct.is_file():
        return str(direct)
    for f in cache.iterdir():
        if f.suffix == ".gguf" and f.is_file():
            return str(f)
    return None


async def _wait_for_server(base_url: str, timeout: float = 120.0) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        for _attempt in range(int(timeout)):
            try:
                resp = await client.get("/health")
                if resp.status_code == 200:
                    warmup = await client.post(
                        "/v1/completions",
                        json={"prompt": "Hello", "max_tokens": 1},
                    )
                    if warmup.status_code == 200:
                        return
            except httpx.TransportError:
                pass
            await asyncio.sleep(1.0)
    raise RuntimeError(f"Server /health did not become 200 within {timeout}s at {base_url}")


def _verify_code(code: str, game_id: str, tmpdir: str) -> GameResult:
    """AST-parse, check method presence, attempt import, run lifecycle checks."""
    t0 = time.time()
    lines = code.count("\n") + 1

    try:
        tree = ast.parse(code)
        ast_valid = True
    except SyntaxError as exc:
        elapsed = int((time.time() - t0) * 1000)
        return GameResult(game_id, False, False, lines, elapsed, f"SyntaxError: {exc}")

    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if not classes:
        elapsed = int((time.time() - t0) * 1000)
        return GameResult(game_id, True, False, lines, elapsed, "No class found")

    methods: set[str] = set()
    attributes: set[str] = set()
    for cls in classes:
        for node in ast.walk(cls):
            if isinstance(node, ast.FunctionDef):
                methods.add(node.name)
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                attributes.add(node.attr)

    has_start = bool(methods & _START_NAMES)
    has_restart = bool(methods & _RESTART_NAMES)
    has_tick = bool(methods & _TICK_NAMES)
    has_score = bool((methods | attributes) & _SCORE_NAMES)
    has_over = bool((methods | attributes) & _OVER_NAMES)

    missing = []
    if not has_start:
        missing.append("start")
    if not has_restart:
        missing.append("restart")
    if not has_tick:
        missing.append("tick")
    if not has_score:
        missing.append("score")
    if not has_over:
        missing.append("game_over")
    if "__init__" not in methods:
        missing.append("__init__")

    if missing:
        elapsed = int((time.time() - t0) * 1000)
        return GameResult(game_id, True, False, lines, elapsed, f"Missing methods: {missing}")

    # Write to disk and try to import
    game_path = Path(tmpdir) / f"game_{game_id}.py"
    game_path.write_text(code)

    importable = False
    lifecycle_fails: list[str] = []
    try:
        spec = importlib.util.spec_from_file_location(f"game_{game_id}", str(game_path))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        importable = True

        lifecycle_fails = run_lifecycle_checks(game_id, mod)
    except Exception as exc:
        lifecycle_fails = [f"import/verify: {type(exc).__name__}: {exc}"]

    elapsed = int((time.time() - t0) * 1000)
    error = ""
    if lifecycle_fails:
        error = "; ".join(lifecycle_fails[:3])
    if not importable and not error:
        error = "Module not importable"

    return GameResult(game_id, ast_valid, importable, lines, elapsed, error)


def _candidate_is_usable(code: str, game_id: str, tmpdir: str) -> bool | str:
    """Return acceptance or bounded deterministic feedback for one candidate."""
    result = _verify_code(code, game_id, tmpdir)
    accepted = result.ast_valid and result.importable and not result.error
    reason = result.error.replace("\n", " ")[:160] if result.error else "none"
    print(
        f"phase=candidate-verify game={game_id} ast={result.ast_valid} "
        f"import={result.importable} accepted={accepted} reason={reason}",
        flush=True,
    )
    return True if accepted else reason


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

_TMPDIR: str | None = None


def teardown_module() -> None:
    if _TMPDIR is not None:
        shutil.rmtree(_TMPDIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Score matrix
# ---------------------------------------------------------------------------

_SCORES_FILE = Path("/tmp/gludd-game-dev-pipeline-results.json")


def _save_results(results: list[ModelResult]) -> None:
    data: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_models": len(results),
        "models_tested": len(results),
        "ci_safe_only": _CI_SAFE_ONLY,
        "games": list(_GAME_IDS),
        "models": [
            {
                "name": r.model_name,
                "category": r.category,
                "size_mb": r.size_mb,
                "ci_safe": r.ci_safe,
                "total_ms": r.total_ms,
                "games": {
                    gid: {
                        "ast_valid": gr.ast_valid,
                        "importable": gr.importable,
                        "lines_of_code": gr.lines_of_code,
                        "elapsed_ms": gr.elapsed_ms,
                        "error": gr.error,
                    }
                    for gid, gr in r.games.items()
                },
            }
            for r in results
        ],
    }
    _SCORES_FILE.write_text(json.dumps(data, indent=2))


def _print_summary(results: list[ModelResult]) -> None:
    """Print a pass/fail summary matrix to stdout."""
    n = len(results)
    total_games = n * len(_GAME_IDS)
    ast_ok = sum(1 for r in results for g in r.games.values() if g.ast_valid)
    imported = sum(1 for r in results for g in r.games.values() if g.importable)

    print("\n" + "=" * 90)
    print("GAME-DEV FULL PIPELINE — SUMMARY MATRIX")
    print("=" * 90)
    print(f"Models tested: {n}  Games per model: {len(_GAME_IDS)}  Total runs: {total_games}")
    print(f"AST-valid: {ast_ok}/{total_games}  Importable: {imported}/{total_games}")
    print(f"CI-safe only: {_CI_SAFE_ONLY}")
    print()

    col_w = max(max(len(r.model_name) for r in results), 16)
    game_col_w = 12

    header = ["Model".ljust(col_w)]
    for g in _GAME_IDS:
        header.append(g.ljust(game_col_w))
    header.append("Total".ljust(game_col_w))
    print("  " + "  ".join(header))
    print("  " + "  ".join("-" * col_w if i == 0 else "-" * game_col_w for i in range(len(header))))

    for r in results:
        vals = [r.model_name.ljust(col_w)]
        ok_count = 0
        for g in _GAME_IDS:
            gr = r.games.get(g)
            if gr is None:
                vals.append("SKIP".ljust(game_col_w))
            elif gr.importable:
                vals.append(f"+ OK {gr.lines_of_code}L".ljust(game_col_w))
                ok_count += 1
            elif gr.ast_valid:
                vals.append("~ AST".ljust(game_col_w))
            else:
                vals.append("- FAIL".ljust(game_col_w))
        vals.append(f"{ok_count}/{len(_GAME_IDS)}".ljust(game_col_w))
        print("  " + "  ".join(vals))

    print()
    print(f"Results written to {_SCORES_FILE}")
    print("=" * 90 + "\n", flush=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.skipif(
    not _LIVE_MODEL_E2E,
    reason="set GLUDD_LIVE_MODEL_E2E=1 to run model downloads and inference",
)
class TestGameDevFullPipeline:
    """Iterate all local models through the full game-dev pipeline."""

    async def _build_pipeline_for_model(
        self,
        model_entry: E2EModelEntry,
        tmpdir: str,
    ) -> tuple[Any, str, str, str, Any]:
        """Download, serve, build gateway → return (pipeline, planner_id, coder_id, reviewer_id, mgr)."""
        from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline
        from general_ludd.infra.local_inference import LocalInferenceManager
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager
        from general_ludd.small_models.download import DownloadedModel, DownloadSource, ModelDownloader

        mgr = LocalInferenceManager()
        downloader = ModelDownloader(cache_dir=tmpdir)

        role_configs = _role_configs_for_model(model_entry)

        downloaded: dict[str, DownloadedModel] = {}

        for artifact_roles in _group_roles_by_artifact(role_configs).values():
            role = artifact_roles[0]
            cfg = role_configs[role]
            cache_dir = f"/tmp/gludd-{cfg['name']}-e2e-model"
            cached = _find_cached_gguf(cache_dir, cfg["filename"])
            if cached is not None and os.path.isfile(cached):
                print(f"      phase=model-resolve status=legacy-cache-hit model={cfg['name']}", flush=True)
                artifact = DownloadedModel(
                    model_id=cfg["repo"],
                    local_path=cached,
                    source=DownloadSource.CACHE,
                    filename=cfg["filename"],
                    size_bytes=os.path.getsize(cached),
                )
            else:
                print(f"      phase=model-resolve status=cache-check model={cfg['name']}", flush=True)
                try:
                    artifact = downloader.download_gguf(
                        cfg["repo"],
                        cfg["filename"],
                        local_files_only=True,
                    )
                except Exception as cache_exc:
                    print(
                        f"      phase=model-resolve status=cache-miss model={cfg['name']} "
                        f"reason={type(cache_exc).__name__}",
                        flush=True,
                    )
                    print(f"      phase=model-resolve status=network-start model={cfg['name']}", flush=True)
                    try:
                        artifact = downloader.download_gguf(cfg["repo"], cfg["filename"])
                    except Exception as exc:
                        raise RuntimeError(f"Download failed for {cfg['name']}: {exc}") from exc
                    print(f"      phase=model-resolve status=network-complete model={cfg['name']}", flush=True)
                else:
                    print(f"      phase=model-resolve status=cache-hit model={cfg['name']}", flush=True)

            assert artifact.local_path and os.path.isfile(artifact.local_path), f"Missing: {cfg['name']}"
            assert artifact.size_bytes > 0
            for artifact_role in artifact_roles:
                downloaded[artifact_role] = artifact

        local_paths = {role: str(model.local_path) for role, model in downloaded.items()}
        ports = await _start_grouped_servers(mgr, role_configs, local_paths)

        try:
            registry = ProviderRegistry()
            registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
            secrets = EnvSecretsManager()

            profiles: list[ModelProfile] = []
            for role in ("planner", "coder", "reviewer"):
                port = ports[role]
                max_input_tokens, max_output_tokens = _payload_limits(
                    int(role_configs[role]["context_size"])
                )
                profile_id = f"local-gdfp-{model_entry.name}-{role}"
                secrets.set(f"GDFP_{role.upper()}_BASE", f"http://{_LOCAL_MODEL_HOST}:{port}/v1")
                secrets.set(f"GDFP_{role.upper()}_KEY", "not-needed")
                profiles.append(
                    ModelProfile(
                        model_profile_id=profile_id,
                        provider="openai",
                        provider_package="langchain_openai",
                        provider_class_hint="ChatOpenAI",
                        model_name="local-model",
                        api_base_alias=f"GDFP_{role.upper()}_BASE",
                        credential_alias=f"GDFP_{role.upper()}_KEY",
                        context_window=int(role_configs[role]["context_size"]),
                        max_input_tokens=max_input_tokens,
                        max_output_tokens=max_output_tokens,
                        cost_per_input_token=0.0,
                        cost_per_output_token=0.0,
                        api_metered=False,
                        run_budget_usd=0.0,
                        enabled=True,
                        resource_profile="ai_light",
                        roles=[role],
                        latency_class="medium",
                        quality_class="variable",
                    )
                )

            gateway = ModelGateway(profiles=profiles, provider_registry=registry, secrets_manager=secrets)
            pipeline = MultiModelGamePipeline(gateway)

            planner_profile = f"local-gdfp-{model_entry.name}-planner"
            coder_profile = f"local-gdfp-{model_entry.name}-coder"
            reviewer_profile = f"local-gdfp-{model_entry.name}-reviewer"
            return pipeline, planner_profile, coder_profile, reviewer_profile, mgr
        except BaseException:
            with contextlib.suppress(Exception):
                await mgr.stop_all()
            raise

    def _run_generation(
        self,
        pipeline: Any,
        planner_model: str,
        coder_model: str,
        reviewer_model: str,
        game_id: str,
        tmpdir: str,
    ) -> GameResult:
        """Run pipeline.generate() for one game, return GameResult."""
        game_def = _TARGET_GAMES[game_id]
        t0 = time.time()

        try:
            from general_ludd.cloud.game_generation import ensure_lifecycle_start_method

            code = pipeline.generate(
                game_def["prompt"],
                planner_model=planner_model,
                coder_model=coder_model,
                reviewer_model=reviewer_model,
                max_review_rounds=1,
                candidate_normalizer=lambda candidate: ensure_lifecycle_start_method(
                    candidate,
                    class_name=str(game_def["class_name"]),
                ),
                candidate_validator=lambda candidate: _candidate_is_usable(
                    candidate,
                    game_id,
                    tmpdir,
                ),
            )
        except Exception as exc:
            elapsed = int((time.time() - t0) * 1000)
            return GameResult(game_id, False, False, 0, elapsed, f"Pipeline error: {type(exc).__name__}: {exc}")

        elapsed = int((time.time() - t0) * 1000)

        if not isinstance(code, str) or len(code) < 20:
            return GameResult(
                game_id,
                False,
                False,
                len(code) if isinstance(code, str) else 0,
                elapsed,
                f"Output too short: {len(code) if isinstance(code, str) else 'not a string'}",
            )

        result = _verify_code(code, game_id, tmpdir)
        result.elapsed_ms = elapsed
        return result

    @pytest.mark.asyncio
    async def test_full_pipeline_summary(self) -> None:
        """Run all CI-safe models through the pipeline; report summary matrix."""
        global _TMPDIR

        if not _ALL_MODELS:
            pytest.skip("No models matched filters")

        tmpdir = tempfile.mkdtemp(prefix="gludd-game-dev-pipeline-")
        _TMPDIR = tmpdir

        results: list[ModelResult] = []
        games_to_run = [g for g in _GAME_IDS if not _TARGET_GAME_ENV or g == _TARGET_GAME_ENV]

        for mi, model_entry in enumerate(_ALL_MODELS):
            print(
                f"\n  [{mi + 1}/{len(_ALL_MODELS)}] {model_entry.name} "
                f"({model_entry.size_mb}MB, {model_entry.category}, ci_safe={model_entry.ci_safe})",
                flush=True,
            )

            mr = ModelResult(
                model_name=model_entry.name,
                category=model_entry.category,
                size_mb=model_entry.size_mb,
                ci_safe=model_entry.ci_safe,
            )
            t_model_start = time.time()

            pipeline = None
            planner_model = ""
            coder_model = ""
            reviewer_model = ""
            mgr = None

            try:
                pipeline, planner_model, coder_model, reviewer_model, mgr = await self._build_pipeline_for_model(
                    model_entry, tmpdir
                )

                for game_id in games_to_run:
                    print(f"      → {game_id} ...", end=" ", flush=True)
                    local_tmpdir = Path(tmpdir) / f"{model_entry.name}_{game_id}"
                    local_tmpdir.mkdir(parents=True, exist_ok=True)

                    gr = self._run_generation(
                        pipeline, planner_model, coder_model, reviewer_model, game_id, str(local_tmpdir)
                    )
                    mr.games[game_id] = gr

                    status = "+" if gr.importable else ("~" if gr.ast_valid else "-")
                    print(
                        f"{status} AST={gr.ast_valid} import={gr.importable} "
                        f"LOC={gr.lines_of_code} t={gr.elapsed_ms}ms "
                        f"{'err=' + gr.error[:60] if gr.error else ''}",
                        flush=True,
                    )

            except Exception as exc:
                print(f"\n      SKIP: {type(exc).__name__}: {exc}", flush=True)
                for game_id in games_to_run:
                    if game_id not in mr.games:
                        mr.games[game_id] = GameResult(
                            game_id, False, False, 0, 0, f"Model setup failed: {type(exc).__name__}: {exc}"
                        )
            finally:
                if mgr is not None:
                    with contextlib.suppress(Exception):
                        await mgr.stop_all()

            mr.total_ms = int((time.time() - t_model_start) * 1000)
            results.append(mr)

        _save_results(results)
        _print_summary(results)

        imported_count = sum(1 for r in results for g in r.games.values() if g.importable)
        total_count = len(results) * len(games_to_run)
        assert imported_count > 0, (
            f"0/{total_count} games importable across {len(results)} models — pipeline produced no working games"
        )

        print(f"\nPASS: {imported_count}/{total_count} games importable", flush=True)


# ---------------------------------------------------------------------------
# Structural tests (no model download needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestGameDevFullPipelineStructural:
    def test_model_registry_has_24_entries(self) -> None:
        assert model_count() == 24

    def test_ci_safe_models_exist(self) -> None:
        assert category_counts()["ci_safe"] >= 1, "Need at least 1 ci_safe model"

    def test_all_four_game_definitions_present(self) -> None:
        for g in _GAME_IDS:
            assert g in _TARGET_GAMES, f"{g} missing"
            d = _TARGET_GAMES[g]
            assert d["prompt"], f"{g}: empty prompt"
            assert d["class_name"], f"{g}: no class_name"
            assert len(d["prompt"]) > 200, f"{g}: prompt too short ({len(d['prompt'])} chars)"

    def test_game_prompts_contain_class_directive(self) -> None:
        for g in _GAME_IDS:
            prompt = _TARGET_GAMES[g]["prompt"].lower()
            assert "class " in prompt, f"{g}: prompt missing class directive"

    def test_models_are_categorized(self) -> None:
        counts = category_counts()
        coders = counts["coding"]
        general = counts["general"]
        assert coders > 0
        assert general > 0
        assert coders + general == counts["total"]

    @pytest.mark.parametrize("game_id", _GAME_IDS)
    def test_verify_can_parse_valid_code(self, game_id: str) -> None:
        """Verify _verify_code works on a syntactically valid minimal game."""
        minimal = textwrap.dedent(f"""\
            import random
            class {_TARGET_GAMES[game_id]["class_name"]}:
                def __init__(self):
                    self.state = "ready"
                    self.score = 0
                    self.game_over = False
                def start(self):
                    self.state = "playing"
                def tick(self):
                    if self.state == "game_over":
                        return
                    self.score += 1
                def is_game_over(self):
                    return self.game_over
                def restart(self):
                    self.score = 0
                    self.game_over = False
                    self.state = "ready"
        """)
        result = _verify_code(minimal, game_id, tempfile.mkdtemp(prefix="gludd-"))
        assert result.ast_valid, f"{game_id}: should be AST-valid"
        assert result.importable, f"{game_id}: attribute-based score/game_over contract should import"
