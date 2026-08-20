"""E2E: Model matrix pipeline — test all 24 local GGUF models + 4 cloud models.

Matrix dimensions:
  A. 24 local models: download→serve→single-game (snake) test. Report
     pass/fail, latency, token usage, error category.
  B. 8 coding models as CODER role: Small general model as planner+reviewer,
     snake game generation via MultiModelGamePipeline.
  C. 4 cloud tiers: DeepSeek, OpenRouter, self-hosted, Anthropic — single-model
     snake generation. Report pass/fail, latency, token usage.

Produces /tmp/gludd-model-matrix-report.json with per-model rows.
Filter via E2E_LOCAL_MODEL (single model), CI_SAFE_ONLY=1 (6 CI-safe models).
Skip when dependencies or hardware insufficient.

Bash = make targets only. Return ≤10 lines: matrix structure, key findings.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from tests.e2e._local_model_configs import (
    _MODELS,
    E2EModelEntry,
    category_counts,
    get_e2e_configs,
    get_models_by_role,
    list_models,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_REPORT_PATH = Path("/tmp/gludd-model-matrix-report.json")

_SNAKE_DESCRIPTION = (
    "Snake game: a snake moves on a 20x20 grid eating food to grow. "
    "The snake is a list of (x,y) tuples, head first. "
    "Score starts at 0, increments by 1 when snake eats food. "
    "Game over when snake hits a wall (x<0, x>=20, y<0, y>=20) or itself. "
    "Game over is idempotent. tick() while game_over does nothing. "
    "Required methods: __init__, start, tick(direction), score->int, is_game_over->bool, restart. "
    "Output Python code only, no prose."
)

# --- env filter knobs ---
_E2E_LOCAL_MODEL = os.environ.get("E2E_LOCAL_MODEL", "").strip()
_CI_SAFE_ONLY = os.environ.get("CI_SAFE_ONLY", "").strip() in ("1", "true", "yes")
_LIVE_MODEL_E2E = os.environ.get("GLUDD_LIVE_MODEL_E2E") == "1"

# --- cloud model knobs ---
_DS_BASE_URL = "https://api.deepseek.com/v1"
_OR_BASE_URL = "https://openrouter.ai/api/v1"
_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"

_LOCAL_BASE_URL = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")
_LOCAL_MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", "qwen2.5:0.5b")
_LOCAL_MODEL_KEY = os.environ.get("LOCAL_MODEL_KEY", "")


# ---------------------------------------------------------------------------
# Dep / hardware checks
# ---------------------------------------------------------------------------


def _has_llama_cpp() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except ImportError:
        return False


def _has_huggingface_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401

        return True
    except ImportError:
        return False


def _free_ram_mb() -> int:
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
    except Exception:
        pass
    return 99999


def _free_disk_gb() -> float:
    try:
        result = subprocess.run(
            ["df", "-k", str(_REPO_ROOT)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 4:
                return int(parts[3]) / (1024 * 1024)
    except Exception:
        pass
    return 99999.0


def _deps_reason() -> str | None:
    if not _LIVE_MODEL_E2E:
        return "set GLUDD_LIVE_MODEL_E2E=1 to load the local inference runtime"
    missing: list[str] = []
    if not _has_llama_cpp():
        missing.append("llama-cpp-python")
    if not _has_huggingface_hub():
        missing.append("huggingface_hub")
    if missing:
        return f"Missing deps: {', '.join(missing)}"
    free_ram = _free_ram_mb()
    if 0 < free_ram < 1024:
        return f"Insufficient free RAM: {free_ram}MB (need >= 1GB)"
    free_disk = _free_disk_gb()
    if free_disk < 2.0:
        return f"Insufficient disk: {free_disk:.1f}GB free (need >= 2GB)"
    return None


_LOCAL_DEPS_SKIP = _deps_reason()


# ---------------------------------------------------------------------------
# Key loading (cloud models)
# ---------------------------------------------------------------------------

_KEY_SENTINEL = object()
_DS_KEY_CACHE: str | None | object = _KEY_SENTINEL
_OR_KEY_CACHE: str | None | object = _KEY_SENTINEL
_ANTHROPIC_KEY_CACHE: str | None | object = _KEY_SENTINEL


def _load_key(env_var: str, filename: str) -> str | None:
    key = os.environ.get(env_var)
    if key:
        return key
    kf = _REPO_ROOT / filename
    if kf.exists():
        v = kf.read_text().strip()
        return v if v else None
    return None


def _get_ds_key() -> str | None:
    global _DS_KEY_CACHE
    if _DS_KEY_CACHE is _KEY_SENTINEL:
        _DS_KEY_CACHE = _load_key("DEEPSEEK_API_KEY", ".deepseek.key")
    return cast(str | None, _DS_KEY_CACHE)


def _get_or_key() -> str | None:
    global _OR_KEY_CACHE
    if _OR_KEY_CACHE is _KEY_SENTINEL:
        _OR_KEY_CACHE = _load_key("OPENROUTER_API_KEY", ".openrouter.key")
    return cast(str | None, _OR_KEY_CACHE)


def _get_anthropic_key() -> str | None:
    global _ANTHROPIC_KEY_CACHE
    if _ANTHROPIC_KEY_CACHE is _KEY_SENTINEL:
        _ANTHROPIC_KEY_CACHE = _load_key("ANTHROPIC_API_KEY", ".anthropic.key")
    return cast(str | None, _ANTHROPIC_KEY_CACHE)


def _probe_local_endpoint() -> bool:
    import urllib.error
    import urllib.request

    try:
        url = f"{_LOCAL_BASE_URL}/models" if "/v1" in _LOCAL_BASE_URL else f"{_LOCAL_BASE_URL}/v1/models"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            pass
        return True
    except Exception:
        return False


_DS_KEY = _get_ds_key()
_OR_KEY = _get_or_key()
_ANTHROPIC_KEY = _get_anthropic_key()
_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None
_LOCAL_REACHABLE = _probe_local_endpoint()


# ---------------------------------------------------------------------------
# Model filtering
# ---------------------------------------------------------------------------


def _local_models_for_matrix() -> list[E2EModelEntry]:
    models = list(_MODELS)
    if _CI_SAFE_ONLY:
        models = [m for m in models if m.ci_safe]
    if _E2E_LOCAL_MODEL:
        name_lower = _E2E_LOCAL_MODEL.lower()
        models = [m for m in models if m.name.lower() == name_lower or name_lower in m.aliases]
    return models


def _coding_models_for_matrix() -> list[E2EModelEntry]:
    return [m for m in _local_models_for_matrix() if m.category == "coding"]


def _general_models_for_matrix() -> list[E2EModelEntry]:
    return [m for m in _local_models_for_matrix() if m.category == "general"]


def _cloud_tiers_for_matrix() -> list[tuple[str, str, str | None]]:
    tiers: list[tuple[str, str, str | None]] = []
    ds_skip = (
        "DEEPSEEK_API_KEY not set"
        if not _DS_KEY
        else ("langchain-openai not installed" if not _HAS_LANGCHAIN_OPENAI else None)
    )
    tiers.append(("deepseek", "DeepSeek (PaaS)", ds_skip))
    or_skip = (
        "OPENROUTER_API_KEY not set"
        if not _OR_KEY
        else ("langchain-openai not installed" if not _HAS_LANGCHAIN_OPENAI else None)
    )
    tiers.append(("openrouter", "OpenRouter (PaaS)", or_skip))
    local_skip = None if _LOCAL_REACHABLE else f"Local endpoint unreachable at {_LOCAL_BASE_URL}"
    tiers.append(("local", "Self-hosted (IaaS)", local_skip))
    anthro_skip = (
        "ANTHROPIC_API_KEY not set"
        if not _ANTHROPIC_KEY
        else ("langchain-openai not installed" if not _HAS_LANGCHAIN_OPENAI else None)
    )
    tiers.append(("anthropic", "Anthropic (PaaS)", anthro_skip))
    return tiers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _find_cached_gguf(cache_dir: str, filename: str) -> str | None:
    cache = pathlib.Path(cache_dir)
    if not cache.is_dir():
        return None
    direct = cache / filename
    if direct.is_file():
        return str(direct)
    for f in cache.iterdir():
        if f.suffix == ".gguf" and f.is_file():
            return str(f)
    return None


async def _wait_for_server(base_url: str, timeout: float = 60.0) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        for _attempt in range(int(timeout)):
            try:
                resp = await client.get("/health")
                if resp.status_code == 200:
                    warmup = await client.post("/v1/completions", json={"prompt": "Hello", "max_tokens": 1})
                    if warmup.status_code == 200:
                        return
            except httpx.TransportError:
                pass
            await asyncio.sleep(1.0)
    raise RuntimeError(f"Server /health not 200 within {timeout}s at {base_url}")


def _verify_snake_code(code: str, tmpdir: str) -> tuple[bool, list[str]]:
    failures: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"Syntax error: {e}"]

    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if not classes:
        return False, ["No class found"]

    methods: set[str] = set()
    for cls in classes:
        for node in ast.walk(cls):
            if isinstance(node, ast.FunctionDef):
                methods.add(node.name)

    alt_start = {"start", "play", "begin", "new_game", "start_game", "reset"}
    alt_over = {"is_game_over", "game_over", "over", "crashed", "finished", "ended", "dead", "done"}
    alt_restart = {"restart", "reset", "new_game", "start_over", "play_again"}

    if "__init__" not in methods:
        failures.append("missing __init__")
    if "tick" not in methods:
        failures.append("missing tick")
    if "score" not in methods:
        failures.append("missing score")
    if not (methods & alt_start):
        failures.append("missing start")
    if not (methods & alt_over):
        failures.append("missing is_game_over")
    if not (methods & alt_restart):
        failures.append("missing restart")

    if failures:
        return False, failures

    game_path = pathlib.Path(tmpdir) / "game_snake.py"
    game_path.write_text(code)
    spec = importlib.util.spec_from_file_location("game_snake", str(game_path))
    if spec is None or spec.loader is None:
        return False, ["could not create module spec"]

    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        return False, [f"module execution failed: {type(exc).__name__}: {exc}"]

    game_cls = None
    for name in [c.name for c in classes]:
        if hasattr(mod, name):
            game_cls = getattr(mod, name)
            break
    if game_cls is None:
        return False, ["no game class in module"]

    try:
        game = game_cls()
    except Exception as exc:
        return False, [f"instantiation failed: {type(exc).__name__}: {exc}"]

    try:
        if hasattr(game, "start"):
            game.start()
        if hasattr(game, "score"):
            s = game.score()
            if not isinstance(s, int):
                failures.append(f"score() returned {type(s).__name__}, expected int")
        if hasattr(game, "is_game_over"):
            go = game.is_game_over()
            if not isinstance(go, bool):
                failures.append(f"is_game_over() returned {type(go).__name__}, expected bool")
        if hasattr(game, "tick") and hasattr(game, "is_game_over"):
            for _ in range(5):
                if not game.is_game_over():
                    game.tick("right")
        if hasattr(game, "restart"):
            game.restart()
            if hasattr(game, "score") and game.score() != 0:
                failures.append("score not 0 after restart")
    except Exception as exc:
        failures.append(f"runtime error: {type(exc).__name__}: {exc}")

    return len(failures) == 0, failures


def _call_model_phase(gateway: Any, profile_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    t0 = time.time()
    try:
        response = gateway.call_model(
            profile_id,
            messages=messages,
            estimated_cost=0.0,
            budget_remaining=10.0,
        )
    except Exception as exc:
        return {
            "content": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": int((time.time() - t0) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    latency_ms = int((time.time() - t0) * 1000)
    usage = response.usage_metadata or {}
    return {
        "content": response.content,
        "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "latency_ms": latency_ms,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Data model — one row in the matrix report
# ---------------------------------------------------------------------------


class StopSentinel:
    pass


_FAIL_FAST = bool(os.environ.get("MATRIX_FAIL_FAST", "").strip() in ("1", "true", "yes"))


@dataclass
class MatrixRow:
    model_name: str
    tier: str
    category: str = ""
    role: str = ""
    passed: bool = False
    error_category: str | None = None
    error_detail: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    code_generated: bool = False
    ast_valid: bool = False
    method_checks: dict[str, bool] = field(default_factory=dict)
    runnable: bool = False
    code_quality_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "tier": self.tier,
            "category": self.category,
            "role": self.role,
            "passed": self.passed,
            "error_category": self.error_category,
            "error_detail": self.error_detail,
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "code_generated": self.code_generated,
            "ast_valid": self.ast_valid,
            "method_checks": self.method_checks,
            "runnable": self.runnable,
            "code_quality_score": self.code_quality_score,
        }


# ---------------------------------------------------------------------------
# Report accumulator
# ---------------------------------------------------------------------------


def _load_report() -> list[dict[str, Any]]:
    if _REPORT_PATH.exists():
        try:
            payload = json.loads(_REPORT_PATH.read_text())
            if isinstance(payload, list):
                return cast(list[dict[str, Any]], payload)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_report(rows: list[dict[str, Any]]) -> None:
    _REPORT_PATH.write_text(json.dumps(rows, indent=2))


def _append_row(row: MatrixRow) -> None:
    existing = _load_report()
    existing.append(row.to_dict())
    _save_report(existing)


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

_TMPDIR: str | None = None


def teardown_module() -> None:
    if _TMPDIR is not None:
        shutil.rmtree(_TMPDIR, ignore_errors=True)


# ===========================================================================
# A. Local model matrix — download→serve→snake test
# ===========================================================================


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.skipif(
    not _LIVE_MODEL_E2E,
    reason="set GLUDD_LIVE_MODEL_E2E=1 to run model downloads and inference",
)
@pytest.mark.skipif(_LOCAL_DEPS_SKIP is not None, reason=_LOCAL_DEPS_SKIP or "unknown dep issue")
@pytest.mark.parametrize(
    "model_entry",
    [pytest.param(m, id=m.name) for m in _local_models_for_matrix()],
)
class TestLocalModelMatrixDownloadServe:
    """For each local GGUF model: download, serve via llama.cpp, generate snake, verify."""

    @pytest.mark.asyncio
    async def test_model_download_serve_generate(self, model_entry: E2EModelEntry) -> None:
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServer, LocalServerConfig
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager
        from general_ludd.small_models.download import DownloadedModel, DownloadSource, ModelDownloader

        global _TMPDIR

        cfg = model_entry
        tmpdir = tempfile.mkdtemp(prefix=f"gludd-matrix-{cfg.name}-")
        _TMPDIR = tmpdir

        row = MatrixRow(
            model_name=cfg.name,
            tier="local",
            category=cfg.category,
            role="coder",
        )
        mgr: LocalInferenceManager | None = None
        server: LocalServer | None = None

        try:
            # --- RAM guard ---
            free_ram = _free_ram_mb()
            needed = int(cfg.size_mb * 1.5) + 512
            if 0 < free_ram < needed:
                row.error_category = "OOM"
                row.error_detail = f"Need ~{needed}MB, have {free_ram}MB free"
                _append_row(row)
                if _FAIL_FAST:
                    pytest.fail(str(row.error_detail))
                pytest.skip(row.error_detail)

            # --- Disk guard ---
            free_disk = _free_disk_gb()
            needed_gb = cfg.size_mb / 1024 + 1.0
            if free_disk < needed_gb:
                row.error_category = "download_fail"
                row.error_detail = f"Need ~{needed_gb:.1f}GB, have {free_disk:.1f}GB free"
                _append_row(row)
                if _FAIL_FAST:
                    pytest.fail(row.error_detail)
                pytest.skip(row.error_detail)

            # --- Step 1: Download ---
            cache_dir = f"/tmp/gludd-{cfg.name}-e2e-model"
            cached = _find_cached_gguf(cache_dir, cfg.filename)
            if cached is not None and os.path.isfile(cached):
                model = DownloadedModel(
                    model_id=cfg.repo,
                    local_path=cached,
                    source=DownloadSource.CACHE,
                    filename=os.path.basename(cached),
                    size_bytes=os.path.getsize(cached),
                )
            else:
                downloader = ModelDownloader(cache_dir=tmpdir)
                try:
                    model = downloader.download_gguf(cfg.repo, cfg.filename)
                except Exception as exc:
                    row.error_category = "download_fail"
                    row.error_detail = f"{type(exc).__name__}: {exc}"
                    _append_row(row)
                    if _FAIL_FAST:
                        pytest.fail(row.error_detail)
                    pytest.skip(row.error_detail)

            assert model.local_path and os.path.isfile(model.local_path)
            assert model.size_bytes > 0

            # --- Step 2: Serve ---
            port = _find_free_port()
            base_url = f"http://localhost:{port}"

            mgr = LocalInferenceManager()
            config = LocalServerConfig(
                engine="llamacpp",
                model_path=model.local_path,
                host="localhost",
                port=port,
                gpu_layers=0,
                context_size=cfg.context_size,
                startup_timeout=120.0,
            )
            server = mgr.create_server(config)

            try:
                await mgr.start_server(server.server_id)
            except RuntimeError as exc:
                row.error_category = "runtime_error"
                row.error_detail = f"Server start failed: {type(exc).__name__}: {exc}"
                _append_row(row)
                if _FAIL_FAST:
                    pytest.fail(row.error_detail)
                pytest.skip(row.error_detail)

            assert server.is_running, f"server status: {server.status}"
            await _wait_for_server(base_url, timeout=90.0)

            # --- Step 3: Generate ---
            profile_id = f"matrix-{cfg.name.replace(' ', '-').lower()}"
            registry = ProviderRegistry()
            registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
            secrets = EnvSecretsManager()
            secrets.set(f"MATRIX_{profile_id.upper()}_BASE", f"http://localhost:{port}/v1")
            secrets.set(f"MATRIX_{profile_id.upper()}_KEY", "not-needed")

            profile = ModelProfile(
                model_profile_id=profile_id,
                provider="openai",
                provider_package="langchain_openai",
                provider_class_hint="ChatOpenAI",
                model_name="local-model",
                api_base_alias=f"MATRIX_{profile_id.upper()}_BASE",
                credential_alias=f"MATRIX_{profile_id.upper()}_KEY",
                context_window=2048,
                max_input_tokens=1500,
                max_output_tokens=1024,
                cost_per_input_token=0.0,
                cost_per_output_token=0.0,
                api_metered=False,
                run_budget_usd=0.0,
                enabled=True,
                resource_profile="ai_light",
                roles=["coder"],
                latency_class="medium",
                quality_class="variable",
            )
            gateway = ModelGateway(
                profiles=[profile],
                provider_registry=registry,
                secrets_manager=secrets,
            )

            t0 = time.time()
            result = _call_model_phase(
                gateway,
                profile_id,
                [{"role": "user", "content": _SNAKE_DESCRIPTION}],
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            row.latency_ms = result.get("latency_ms", elapsed_ms)
            row.tokens_in = result.get("tokens_in", 0)
            row.tokens_out = result.get("tokens_out", 0)

            if result.get("error"):
                row.error_category = "runtime_error"
                row.error_detail = result["error"]
                _append_row(row)
                if _FAIL_FAST:
                    pytest.fail(row.error_detail)
                pytest.fail(row.error_detail)

            code = result["content"]
            assert isinstance(code, str) and len(code) > 30, f"code too short: {len(code)} chars"
            row.code_generated = True

            # --- Step 4: Verify ---
            passed, failures = _verify_snake_code(code, tmpdir)
            row.ast_valid = "Syntax error" not in str(failures)
            row.runnable = passed
            row.method_checks = {
                "init": "__init__" not in str(failures),
                "tick": "tick" not in str(failures),
                "score": "score" not in str(failures),
                "start": "start" not in str(failures),
                "is_game_over": "is_game_over" not in str(failures),
                "restart": "restart" not in str(failures),
            }
            if passed:
                row.code_quality_score = 1.0
                row.passed = True
            else:
                row.code_quality_score = max(0.0, 1.0 - len(failures) * 0.15)
                row.error_category = "runtime_error"
                row.error_detail = "; ".join(failures[:5])

            _append_row(row)

            assert passed, f"Verification failures: {failures}"
            assert row.code_generated

            await mgr.stop_all()
            assert server.status == "stopped"

        finally:
            _append_row(row)
            if mgr is not None:
                with contextlib.suppress(Exception):
                    await mgr.stop_all()
            _TMPDIR = None
            shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# B. Coding models as CODER role in multi-model pipeline
# ===========================================================================


_CODER_PLANNER_PROMPT = """You are a GAME DESIGN PLANNER. Given a brief game description, produce a
structured design specification. Output each field on its own line in field:value format:

name:<game-name>
genre:<genre>
architecture:<architecture>
components:<comma-separated>
tech:<comma-separated>
acceptance:<comma-separated criteria>

Be specific and concrete. Only output these fields, nothing else."""

_CODER_CODE_PROMPT = """You are a GAME CODER. Write complete, runnable Python Snake game code from the
design spec. The code must be self-contained, use ONLY the stdlib, and include ALL
components listed in the spec.

Output ONLY the Python code, no explanation, no markdown fences."""

_CODER_REVIEW_PROMPT = """You are a GAME CODE REVIEWER. Review the provided game code against the design
spec. Output a structured review in field:value format:

issues:<comma-separated issues, or empty>
fixes:<comma-separated fixes, or empty>
score:<0.0-1.0 quality score>
passed:<true or false>

Check: syntax, all required components, acceptance criteria met."""


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.skipif(
    not _LIVE_MODEL_E2E,
    reason="set GLUDD_LIVE_MODEL_E2E=1 to run model downloads and inference",
)
@pytest.mark.skipif(_LOCAL_DEPS_SKIP is not None, reason=_LOCAL_DEPS_SKIP or "unknown dep issue")
@pytest.mark.parametrize(
    "coder_model",
    [pytest.param(m, id=m.name) for m in _coding_models_for_matrix()],
)
class TestCodingModelAsCoderRole:
    """Each coding model as CODER; use smallest general model as planner+reviewer."""

    @pytest.mark.asyncio
    async def test_coder_role_snake_generation(self, coder_model: E2EModelEntry) -> None:
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager
        from general_ludd.small_models.download import DownloadedModel, DownloadSource, ModelDownloader

        global _TMPDIR

        # --- Select planner/reviewer model — smallest general model ---
        generals = _general_models_for_matrix()
        if not generals:
            pytest.skip("No general models available for planner/reviewer")
        planner_model = min(generals, key=lambda m: m.size_mb)
        assert planner_model is not None

        if _E2E_LOCAL_MODEL:
            coder_names = {m.name for m in _coding_models_for_matrix()}
            if _E2E_LOCAL_MODEL not in coder_names:
                pytest.skip(f"E2E_LOCAL_MODEL={_E2E_LOCAL_MODEL} not in coding models")

        tmpdir = tempfile.mkdtemp(prefix=f"gludd-matrix-coder-{coder_model.name}-")
        _TMPDIR = tmpdir

        mgr = LocalInferenceManager()
        servers: list[object] = []
        ports: dict[str, int] = {}

        try:
            # --- Download planner + coder ---
            downloaded: dict[str, DownloadedModel] = {}
            for role, entry in [("planner", planner_model), ("coder", coder_model)]:
                cache_dir = f"/tmp/gludd-{entry.name}-e2e-model"
                cached = _find_cached_gguf(cache_dir, entry.filename)
                if cached is not None and os.path.isfile(cached):
                    downloaded[role] = DownloadedModel(
                        model_id=entry.repo,
                        local_path=cached,
                        source=DownloadSource.CACHE,
                        filename=entry.filename,
                        size_bytes=os.path.getsize(cached),
                    )
                else:
                    downloader = ModelDownloader(cache_dir=tmpdir)
                    try:
                        downloaded[role] = downloader.download_gguf(entry.repo, entry.filename)
                    except Exception as exc:
                        pytest.skip(f"Download failed for {entry.name}: {exc}")

                mdl = downloaded[role]
                assert mdl.local_path and os.path.isfile(mdl.local_path)
                assert mdl.size_bytes > 0

            # --- Serve planner + coder on separate ports ---
            role_specs = [("planner", planner_model), ("coder", coder_model)]
            for role, entry in role_specs:
                port = _find_free_port()
                ports[role] = port

                config = LocalServerConfig(
                    engine="llamacpp",
                    model_path=downloaded[role].local_path,
                    host="localhost",
                    port=port,
                    gpu_layers=0,
                    context_size=entry.context_size,
                    startup_timeout=120.0,
                )
                server = mgr.create_server(config)
                servers.append(server)

                try:
                    await mgr.start_server(server.server_id)
                except RuntimeError as exc:
                    await mgr.stop_all()
                    pytest.skip(f"Server start failed for {entry.name}: {exc}")

                assert server.is_running
                await _wait_for_server(f"http://localhost:{port}", timeout=90.0)

            # --- Build gateway with 2 profiles ---
            registry = ProviderRegistry()
            registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
            secrets = EnvSecretsManager()

            profiles: list[ModelProfile] = []
            for role in ("planner", "coder"):
                port = ports[role]
                pid = f"coder-matrix-{role}"
                secrets.set(f"CODER_MATRIX_{role.upper()}_BASE", f"http://localhost:{port}/v1")
                secrets.set(f"CODER_MATRIX_{role.upper()}_KEY", "not-needed")
                profiles.append(
                    ModelProfile(
                        model_profile_id=pid,
                        provider="openai",
                        provider_package="langchain_openai",
                        provider_class_hint="ChatOpenAI",
                        model_name="local-model",
                        api_base_alias=f"CODER_MATRIX_{role.upper()}_BASE",
                        credential_alias=f"CODER_MATRIX_{role.upper()}_KEY",
                        context_window=2048,
                        max_input_tokens=1500,
                        max_output_tokens=1024,
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

            gateway = ModelGateway(
                profiles=profiles,
                provider_registry=registry,
                secrets_manager=secrets,
            )

            # --- Pipeline: planner→coder ---
            t0 = time.time()

            plan_result = _call_model_phase(
                gateway,
                "coder-matrix-planner",
                [{"role": "system", "content": _CODER_PLANNER_PROMPT}, {"role": "user", "content": _SNAKE_DESCRIPTION}],
            )
            if plan_result.get("error"):
                row = MatrixRow(
                    model_name=coder_model.name,
                    tier="local",
                    category="coding",
                    role="coder",
                    error_category="runtime_error",
                    error_detail=f"Planner failed: {plan_result['error']}",
                )
                _append_row(row)
                if _FAIL_FAST:
                    pytest.fail(str(row.error_detail))
                pytest.skip(f"Planner failed: {plan_result['error']}")

            design_spec = plan_result["content"]
            assert design_spec and len(design_spec) > 20

            code_result = _call_model_phase(
                gateway,
                "coder-matrix-coder",
                [{"role": "system", "content": _CODER_CODE_PROMPT}, {"role": "user", "content": design_spec}],
            )
            elapsed_ms = int((time.time() - t0) * 1000)

            row = MatrixRow(
                model_name=coder_model.name,
                tier="local",
                category="coding",
                role="coder",
                latency_ms=elapsed_ms,
                tokens_in=plan_result.get("tokens_in", 0) + code_result.get("tokens_in", 0),
                tokens_out=code_result.get("tokens_out", 0),
            )

            if code_result.get("error"):
                row.error_category = "runtime_error"
                row.error_detail = f"Coder failed: {code_result['error']}"
                _append_row(row)
                if _FAIL_FAST:
                    pytest.fail(row.error_detail)
                pytest.fail(row.error_detail)

            code = code_result["content"]
            assert isinstance(code, str) and len(code) > 30, f"code too short: {len(code)}"
            row.code_generated = True

            # --- Clean code (strip markdown fences) ---
            cleaned = code.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                cleaned = "\n".join(lines)

            passed, failures = _verify_snake_code(cleaned, tmpdir)
            row.ast_valid = "Syntax error" not in str(failures)
            row.runnable = passed
            row.method_checks = {
                "init": "__init__" not in str(failures),
                "tick": "tick" not in str(failures),
                "score": "score" not in str(failures),
                "start": "start" not in str(failures),
                "is_game_over": "is_game_over" not in str(failures),
                "restart": "restart" not in str(failures),
            }
            if passed:
                row.code_quality_score = 1.0
                row.passed = True
            else:
                row.code_quality_score = max(0.0, 1.0 - len(failures) * 0.15)
                row.error_category = "runtime_error"
                row.error_detail = "; ".join(failures[:5])

            _append_row(row)
            assert passed, f"Coder role verification: {failures}"

            await mgr.stop_all()

        finally:
            with contextlib.suppress(Exception):
                await mgr.stop_all()
            _TMPDIR = None
            shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# C. Cloud model matrix — single-model snake generation
# ===========================================================================


def _build_cloud_gateway(tier_key: str) -> tuple[str, Any]:
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()

    if tier_key == "deepseek":
        pid = "matrix-deepseek"
        secrets.set("DEEPSEEK_API_KEY", cast(str, _DS_KEY))
        secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
        profile = ModelProfile(
            model_profile_id=pid,
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="deepseek-chat",
            api_base_alias="DEEPSEEK_API_BASE",
            credential_alias="DEEPSEEK_API_KEY",
            context_window=65536,
            max_input_tokens=60000,
            max_output_tokens=8192,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            api_metered=True,
            run_budget_usd=5.0,
            enabled=True,
            resource_profile="ai_heavy",
            roles=["coder"],
            latency_class="fast",
            quality_class="high",
        )
        return pid, cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)

    elif tier_key == "openrouter":
        pid = "matrix-openrouter"
        secrets.set("OPENROUTER_API_KEY", cast(str, _OR_KEY))
        secrets.set("OPENROUTER_API_BASE", _OR_BASE_URL)
        profile = ModelProfile(
            model_profile_id=pid,
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="qwen/qwen2.5-coder-7b-instruct",
            api_base_alias="OPENROUTER_API_BASE",
            credential_alias="OPENROUTER_API_KEY",
            context_window=65536,
            max_input_tokens=60000,
            max_output_tokens=8192,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            api_metered=True,
            run_budget_usd=10.0,
            enabled=True,
            resource_profile="ai_heavy",
            roles=["coder"],
            latency_class="medium",
            quality_class="high",
        )
        return pid, cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)

    elif tier_key == "local":
        pid = f"matrix-local-{_LOCAL_MODEL_NAME.replace('/', '_').replace(':', '_')}"
        secrets.set("LOCAL_MODEL_BASE", _LOCAL_BASE_URL)
        if _LOCAL_MODEL_KEY:
            secrets.set("LOCAL_MODEL_KEY", _LOCAL_MODEL_KEY)
        profile = ModelProfile(
            model_profile_id=pid,
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name=_LOCAL_MODEL_NAME,
            api_base_alias="LOCAL_MODEL_BASE",
            credential_alias="LOCAL_MODEL_KEY",
            context_window=32768,
            max_input_tokens=28000,
            max_output_tokens=4096,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            api_metered=False,
            run_budget_usd=0.0,
            enabled=True,
            resource_profile="ai_light",
            roles=["coder"],
            latency_class="medium",
            quality_class="variable",
        )
        return pid, cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)

    elif tier_key == "anthropic":
        pid = "matrix-anthropic"
        secrets.set("ANTHROPIC_API_KEY", cast(str, _ANTHROPIC_KEY))
        secrets.set("ANTHROPIC_API_BASE", _ANTHROPIC_BASE_URL)
        profile = ModelProfile(
            model_profile_id=pid,
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="claude-3-haiku-20240307",
            api_base_alias="ANTHROPIC_API_BASE",
            credential_alias="ANTHROPIC_API_KEY",
            context_window=200000,
            max_input_tokens=180000,
            max_output_tokens=4096,
            cost_per_input_token=0.00000025,
            cost_per_output_token=0.00000125,
            api_metered=True,
            run_budget_usd=5.0,
            enabled=True,
            resource_profile="ai_heavy",
            roles=["coder"],
            latency_class="fast",
            quality_class="high",
        )
        return pid, cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)

    raise ValueError(f"Unknown tier: {tier_key}")


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.skipif(_LOCAL_DEPS_SKIP is not None, reason=_LOCAL_DEPS_SKIP or "unknown dep issue")
class TestCloudModelMatrix:
    """Snake generation on each available cloud tier."""

    def _run_cloud_single(self, tier_key: str, display: str) -> None:
        pid, gateway = _build_cloud_gateway(tier_key)

        row = MatrixRow(
            model_name=display,
            tier="cloud",
            category="coding",
            role="coder",
        )

        try:
            result = _call_model_phase(
                gateway,
                pid,
                [{"role": "user", "content": _SNAKE_DESCRIPTION}],
            )
            row.latency_ms = result.get("latency_ms", 0)
            row.tokens_in = result.get("tokens_in", 0)
            row.tokens_out = result.get("tokens_out", 0)

            if result.get("error"):
                row.error_category = "runtime_error"
                row.error_detail = result["error"]
                _append_row(row)
                pytest.fail(f"{display}: {result['error']}")

            code = result["content"]
            assert isinstance(code, str) and len(code) > 30
            row.code_generated = True

            with tempfile.TemporaryDirectory(prefix="gludd-matrix-cloud-") as td:
                passed, failures = _verify_snake_code(code, td)
                row.ast_valid = "Syntax error" not in str(failures)
                row.runnable = passed
                row.method_checks = {
                    "init": "__init__" not in str(failures),
                    "tick": "tick" not in str(failures),
                    "score": "score" not in str(failures),
                    "start": "start" not in str(failures),
                    "is_game_over": "is_game_over" not in str(failures),
                    "restart": "restart" not in str(failures),
                }
                if passed:
                    row.passed = True
                    row.code_quality_score = 1.0
                else:
                    row.code_quality_score = max(0.0, 1.0 - len(failures) * 0.15)
                    row.error_category = "runtime_error"
                    row.error_detail = "; ".join(failures[:5])

                _append_row(row)
                assert passed, f"{display} verification: {failures}"

        except Exception as exc:
            row.error_category = "runtime_error"
            row.error_detail = f"{type(exc).__name__}: {exc}"
            _append_row(row)
            raise

    def test_cloud_deepseek(self) -> None:
        tier = "deepseek"
        skip_reason = next((r for t, _, r in _cloud_tiers_for_matrix() if t == tier), None)
        if skip_reason:
            pytest.skip(skip_reason)
        self._run_cloud_single(tier, "DeepSeek (PaaS)")

    def test_cloud_openrouter(self) -> None:
        tier = "openrouter"
        skip_reason = next((r for t, _, r in _cloud_tiers_for_matrix() if t == tier), None)
        if skip_reason:
            pytest.skip(skip_reason)
        self._run_cloud_single(tier, "OpenRouter (PaaS)")

    def test_cloud_local_endpoint(self) -> None:
        tier = "local"
        skip_reason = next((r for t, _, r in _cloud_tiers_for_matrix() if t == tier), None)
        if skip_reason:
            pytest.skip(skip_reason)
        self._run_cloud_single(tier, "Self-hosted (IaaS)")

    def test_cloud_anthropic(self) -> None:
        tier = "anthropic"
        skip_reason = next((r for t, _, r in _cloud_tiers_for_matrix() if t == tier), None)
        if skip_reason:
            pytest.skip(skip_reason)
        self._run_cloud_single(tier, "Anthropic (PaaS)")


# ===========================================================================
# D. Structural tests — no hardware / API keys needed
# ===========================================================================


@pytest.mark.e2e
class TestModelMatrixStructural:
    """Shape of configs, model counts, report path, filter behavior."""

    def test_local_model_configs_available(self) -> None:
        counts = category_counts()
        assert counts["total"] == 24, f"Expected 24 local models, got {counts['total']}"
        assert counts["coding"] == 8
        assert counts["general"] == 16
        assert counts["ci_safe"] == 6

    def test_ci_safe_models_under_500mb(self) -> None:
        ci_models = list_models(ci_safe=True)
        for m in ci_models:
            assert m.size_mb <= 500, f"CI-safe model {m.name} is {m.size_mb}MB (must be <=500)"

    def test_coding_models_available_as_coder_role(self) -> None:
        coding = list_models(category="coding")
        assert len(coding) == 8
        assert all(m.category == "coding" for m in coding)

    def test_general_models_available_as_planner_reviewer(self) -> None:
        generals = list_models(category="general")
        assert len(generals) == 16
        assert all(m.category == "general" for m in generals)

    def test_role_map_has_all_three_roles(self) -> None:
        role_map = get_models_by_role()
        assert "PLANNER" in role_map
        assert "CODER" in role_map
        assert "REVIEWER" in role_map
        assert len(role_map["CODER"]) == 8

    def test_e2e_configs_filtered_by_env(self) -> None:
        cfgs = get_e2e_configs()
        assert isinstance(cfgs, list)
        if _CI_SAFE_ONLY:
            assert all(any(m.name == c.name and m.ci_safe for m in _MODELS) for c in cfgs)

    def test_cloud_tiers_have_skip_or_key(self) -> None:
        tiers = _cloud_tiers_for_matrix()
        assert len(tiers) == 4
        for tier_key, display, _skip_reason in tiers:
            assert tier_key in ("deepseek", "openrouter", "local", "anthropic")
            assert display

    def test_report_path_writable(self) -> None:
        _REPORT_PATH.write_text(json.dumps([{"_test": True}]))
        assert _REPORT_PATH.exists()
        data = json.loads(_REPORT_PATH.read_text())
        assert data[0].get("_test") is True

    def test_matrix_row_serialization_roundtrip(self) -> None:
        row = MatrixRow(
            model_name="TestModel",
            tier="local",
            category="coding",
            role="coder",
            passed=True,
            latency_ms=1234,
            tokens_in=50,
            tokens_out=200,
            code_generated=True,
            ast_valid=True,
            method_checks={"init": True, "tick": True, "score": True},
            runnable=True,
            code_quality_score=0.95,
        )
        d = row.to_dict()
        assert d["model"] == "TestModel"
        assert d["latency_ms"] == 1234
        assert d["passed"] is True
        assert d["code_quality_score"] == 0.95

    def test_filter_by_e2e_local_model_env(self) -> None:
        if not _E2E_LOCAL_MODEL:
            models = _local_models_for_matrix()
            if not _CI_SAFE_ONLY:
                assert len(models) == 24, f"Expected 24 models, got {len(models)}"

    def test_free_ram_detection_returns_int(self) -> None:
        ram = _free_ram_mb()
        assert isinstance(ram, int) and ram >= 0

    def test_free_disk_detection_returns_float(self) -> None:
        disk = _free_disk_gb()
        assert isinstance(disk, float) and disk >= 0.0

    def test_models_have_all_required_fields(self) -> None:
        for m in _MODELS:
            assert m.name, "empty name"
            assert m.repo, f"{m.name}: empty repo"
            assert m.filename, f"{m.name}: empty filename"
            assert m.size_mb > 0, f"{m.name}: size_mb={m.size_mb}"
            assert m.category in ("coding", "general"), f"{m.name}: bad category"
            assert m.context_size >= 512, f"{m.name}: context too small"


# ===========================================================================
# E. Aggregate report summary (structural + CLI-readable)
# ===========================================================================


@pytest.mark.e2e
class TestModelMatrixReportSummary:
    """Print unified summary from the JSON report."""

    def test_report_summary(self) -> None:
        rows = _load_report()
        if not rows:
            print("\n[MODEL MATRIX] No results yet. Run with E2E_LOCAL_MODEL=Qwen2.5-Coder-0.5B to generate.")
            return

        n_total = len(rows)
        n_pass = sum(1 for r in rows if r.get("passed"))
        n_ast = sum(1 for r in rows if r.get("ast_valid"))
        n_run = sum(1 for r in rows if r.get("runnable"))
        n_code = sum(1 for r in rows if r.get("code_generated"))

        by_tier: dict[str, dict[str, int]] = {}
        by_category: dict[str, dict[str, int]] = {}
        by_error: dict[str, int] = {}

        for r in rows:
            tier = r.get("tier", "unknown")
            cat = r.get("category", "unknown")
            err = r.get("error_category") or "none"

            by_tier.setdefault(tier, {"total": 0, "pass": 0})
            by_tier[tier]["total"] += 1
            if r.get("passed"):
                by_tier[tier]["pass"] += 1

            by_category.setdefault(cat, {"total": 0, "pass": 0})
            by_category[cat]["total"] += 1
            if r.get("passed"):
                by_category[cat]["pass"] += 1

            by_error.setdefault(err, 0)
            by_error[err] += 1

        print(f"\n{'=' * 60}")
        print("MODEL MATRIX REPORT SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Total results: {n_total}")
        print(f"  Passed:        {n_pass}/{n_total} ({100 * n_pass // max(n_total, 1)}%)")
        print(f"  AST valid:     {n_ast}/{n_total}")
        print(f"  Runnable:      {n_run}/{n_total}")
        print(f"  Code generated:{n_code}/{n_total}")
        print()
        print("  By tier:")
        for tier in sorted(by_tier):
            d = by_tier[tier]
            print(f"    {tier:15s}: {d['pass']}/{d['total']} pass")
        print("  By category:")
        for cat in sorted(by_category):
            d = by_category[cat]
            print(f"    {cat:15s}: {d['pass']}/{d['total']} pass")
        if by_error.get("none", 0) < n_total:
            print("  By error category:")
            for err, count in sorted(by_error.items(), key=lambda x: -x[1]):
                if err != "none":
                    print(f"    {err:20s}: {count}")
        print(f"\n  Full report: {_REPORT_PATH}")
        print(f"{'=' * 60}\n", flush=True)

        assert n_total >= 1, "No results in report — run at least one model first"
        assert isinstance(n_total, int)
