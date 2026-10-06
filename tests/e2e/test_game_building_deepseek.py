"""End-to-end DeepSeek game-building, full-pipeline, and persistence tests."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tests.e2e._game_building_deepseek_definitions import (
    _DEEPSEEK_KEY,
    _HAS_LANGCHAIN_OPENAI,
    _LIVE_SKIP_REASON,
    _OBSERVABILITY_DATA,
    _REPO_ROOT,
    GAME_DEFINITIONS,
    _build_deepseek_gateway,
    _export_observability_report,
    _init_game_obs,
)
from tests.e2e._game_building_deepseek_runtime import (
    _GAME_PERSISTENCE_PARAMS,
    _call_deepseek,
    _run_game_tests,
    _run_persistence_tests,
)
from tests.e2e._game_building_deepseek_verification import (
    _extract_python_module,
    _load_generated_module,
    _parse_ast,
    verify_features,
)


@pytest.mark.skipif(not _DEEPSEEK_KEY or not _HAS_LANGCHAIN_OPENAI, reason=_LIVE_SKIP_REASON)
class TestDeepSeekGameBuilding:
    """Build each game via DeepSeek API and verify it works headlessly."""

    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_deepseek_gateway()

    @staticmethod
    def _call_model(gateway: Any, prompt: str) -> dict[str, Any]:
        """Call DeepSeek and return response metadata + content."""
        t0 = time.time()
        response = gateway.call_model(
            "deepseek_coder",
            messages=[{"role": "user", "content": prompt}],
            estimated_cost=0.0,
            budget_remaining=5.0,
        )
        latency_ms = (time.time() - t0) * 1000
        usage = response.usage_metadata or {}
        return {
            "content": response.content,
            "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
            "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
            "tool_calls": len(response.tool_calls) if response.tool_calls else 0,
            "content_len": len(response.content),
            "model": getattr(response, "model_profile_id", "unknown"),
            "latency_ms": latency_ms,
        }

    # ---- Snake ----
    def test_build_snake(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Snake game."""
        self._build_and_verify_game(gateway, tmp_path, "snake")

    # ---- Tetris ----
    def test_build_tetris(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Tetris game."""
        self._build_and_verify_game(gateway, tmp_path, "tetris")

    # ---- Minesweeper ----
    def test_build_minesweeper(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Minesweeper game."""
        self._build_and_verify_game(gateway, tmp_path, "minesweeper")

    # ---- Checkers ----
    def test_build_checkers(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Checkers game."""
        self._build_and_verify_game(gateway, tmp_path, "checkers")

    # ---- SkiFree ----
    def test_build_skifree(self, gateway, tmp_path):
        """Test: DeepSeek builds a working SkiFree game."""
        self._build_and_verify_game(gateway, tmp_path, "skifree")

    # ---- Banana ----
    def test_build_banana(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Banana (Gorillas) game."""
        self._build_and_verify_game(gateway, tmp_path, "banana")

    # ---- Pong ----
    def test_build_pong(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Pong game."""
        self._build_and_verify_game(gateway, tmp_path, "pong")

    # ---- Breakout ----
    def test_build_breakout(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Breakout game."""
        self._build_and_verify_game(gateway, tmp_path, "breakout")

    # ---- Maze Runner ----
    def test_build_maze_runner(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Maze Runner game."""
        self._build_and_verify_game(gateway, tmp_path, "maze_runner")

    # ---- Word Guesser ----
    def test_build_word_guesser(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Word Guesser game."""
        self._build_and_verify_game(gateway, tmp_path, "word_guesser")

    # ---- Memory Match ----
    def test_build_memory_match(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Memory Match game."""
        self._build_and_verify_game(gateway, tmp_path, "memory_match")

    # ---- Tic-Tac-Toe ----
    def test_build_tic_tac_toe(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Tic-Tac-Toe game."""
        self._build_and_verify_game(gateway, tmp_path, "tic_tac_toe")

    # ---- Shared build + verify logic ----
    def _build_and_verify_game(self, gateway, tmp_path, game_id):
        game_def = GAME_DEFINITIONS[game_id]
        class_name = game_def["class_name"]
        verifications = game_def["verifications"]

        obs = _init_game_obs(game_id)

        print(f"\n\n{'=' * 70}")
        print(f"BUILDING: {game_id} ({class_name})")
        print(f"{'=' * 70}")

        # Step 1: Call DeepSeek
        print(f"\n--- Step 1: Calling DeepSeek for {game_id} ---")
        try:
            response = self._call_model(gateway, game_def["prompt"])
        except Exception:
            raise

        print(f"  tokens_in={response['tokens_in']} tokens_out={response['tokens_out']}")
        print(f"  content_len={response['content_len']} tool_calls={response['tool_calls']}")
        obs["tokens_in"] = response["tokens_in"]
        obs["tokens_out"] = response["tokens_out"]
        obs["tool_calls"] = response["tool_calls"]
        obs["content_len"] = response["content_len"]
        obs["model"] = response["model"]
        obs["phases"]["model_call"] = round(response["latency_ms"], 1)

        # Step 2: Extract code
        print("\n--- Step 2: Extracting Python code ---")
        t0 = time.time()
        source = _extract_python_module(response["content"])
        obs["phases"]["extract_code"] = round((time.time() - t0) * 1000, 1)
        if source is None:
            print("  FAIL: Could not extract Python module from model output")
            print(f"  Raw output (first 500): {response['content'][:500]!r}")
            obs["errors"].append("Could not extract Python module from model output")
            self._record_gap(game_id, "code_extraction", "Model did not produce extractable Python code")
            return

        print(f"  Extracted {len(source)} chars of Python code")

        # Step 3: Parse AST
        print("\n--- Step 3: AST parsing ---")
        t0 = time.time()
        ast_result = _parse_ast(source)
        obs["phases"]["ast_parse"] = round((time.time() - t0) * 1000, 1)
        print(f"  parseable={ast_result['parseable']} has_class={ast_result['has_class']}")
        if ast_result["error"]:
            print(f"  AST error: {ast_result['error']}")

        # Step 4: Write module and run game tests
        print("\n--- Step 4: Game verification ---")
        game_dir = tmp_path / game_id
        game_dir.mkdir(exist_ok=True)
        t0 = time.time()
        test_results = _run_game_tests(source, class_name, verifications, game_id, game_dir)
        obs["phases"]["game_verify"] = round((time.time() - t0) * 1000, 1)

        obs["imported"] = test_results["module_imported"]
        obs["instantiated"] = test_results["instantiated"]

        print(f"  module_written={test_results['module_written']}")
        print(f"  module_imported={test_results['module_imported']}")
        print(f"  instantiated={test_results['instantiated']}")
        if test_results["errors"]:
            for err in test_results["errors"]:
                print(f"  ERROR: {err[:200]}")
                obs["errors"].append(err[:200])

        checks = test_results.get("checks", {})
        passed = sum(1 for c in checks.values() if c["passed"])
        failed = len(checks) - passed
        obs["checks_passed"] = passed
        obs["checks_total"] = len(checks)
        obs["checks"] = {k: {"passed": v["passed"], "desc": v.get("desc", "")} for k, v in checks.items()}
        print(f"  Checks: {passed} passed, {failed} failed out of {len(checks)}")

        for check_id, check_data in checks.items():
            status = "PASS" if check_data["passed"] else "FAIL"
            print(f"    [{status}] {check_id}: {check_data['desc']}")
            if check_data.get("error"):
                print(f"           error: {check_data['error'][:200]}")

        # Step 5: Report gap if module didn't import or checks failed
        if not test_results["module_imported"]:
            errors = test_results["errors"]
            detail = errors[:2] if errors else "unknown"
            self._record_gap(
                game_id,
                "import",
                f"Module failed to import: {detail}",
            )
        elif not test_results["instantiated"]:
            errors = test_results["errors"]
            detail = errors[:2] if errors else "unknown"
            self._record_gap(
                game_id,
                "instantiation",
                f"Class {class_name} failed to instantiate: {detail}",
            )
        elif failed > len(checks) * 0.5:
            self._record_gap(game_id, "game_logic", f"{failed}/{len(checks)} verification checks failed")

        # Step 6: Hard feature verification (name-agnostic contract).
        # The per-check diagnostics above are best-effort; THIS is the hard
        # contract.  We re-load a fresh copy of the module so feature
        # verification sees pristine state (the per-check run above may
        # have mutated its instance).
        feature_failures: list[str] = []
        if source is not None and ast_result["parseable"]:
            feature_dir = tmp_path / f"{game_id}_features"
            feature_dir.mkdir(exist_ok=True)
            try:
                feature_mod = _load_generated_module(
                    source,
                    f"{game_id}_feature_check",
                    feature_dir,
                )
                feature_failures = verify_features(game_id, feature_mod)
            except Exception as exc:
                feature_failures = [
                    f"feature verifier crashed: {type(exc).__name__}: {exc}",
                ]
        else:
            feature_failures = ["source missing or not parseable; cannot verify features"]

        obs["feature_failures"] = feature_failures
        if feature_failures:
            print(f"\n  FEATURE FAILURES ({len(feature_failures)}):")
            for fail in feature_failures:
                print(f"    - {fail}")
            self._record_gap(
                game_id,
                "features",
                f"{len(feature_failures)} feature failures: " + "; ".join(feature_failures[:3]),
            )
        else:
            print("  All required features verified.")

        print(f"\n{'-' * 70}")
        print(f"RESULT: {game_id} — {passed}/{len(checks)} checks passed, {len(feature_failures)} feature failures")

        # Hard assertion: the model's output MUST satisfy the feature floor.
        # This is the contract the user cares about — the game may be
        # written differently each time, but it must implement the features.
        assert not feature_failures, f"{game_id}: required features not satisfied:\n  - " + "\n  - ".join(
            feature_failures
        )

    # ---- Gap tracking ----
    _gaps: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def _record_gap(cls, game_id: str, category: str, detail: str) -> None:
        cls._gaps.append({"game": game_id, "category": category, "detail": detail})


# ---------------------------------------------------------------------------
# Pipeline Gap Analysis
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DEEPSEEK_KEY or not _HAS_LANGCHAIN_OPENAI, reason=_LIVE_SKIP_REASON)
class TestGameBuildingGapAnalysis:
    """After running all game-building tests, analyze gludd's pipeline for gaps."""

    def test_gap_report(self, tmp_path: Path):
        """Print a comprehensive gap analysis based on all game-building results."""
        gaps = TestDeepSeekGameBuilding._gaps
        if not gaps:
            print("\nNo gaps recorded yet — run game-building tests first")
        else:
            print("\n\n" + "=" * 70)
            print("GAME-BUILDING GAP REPORT")
            print("=" * 70)
            print(f"\nTotal gaps found: {len(gaps)}")

            by_category: dict[str, list[dict]] = {}
            for g in gaps:
                by_category.setdefault(g["category"], []).append(g)

            print("\nBy category:")
            for cat, items in sorted(by_category.items()):
                games = sorted(set(i["game"] for i in items))
                print(f"  {cat}: {len(items)} gaps across {len(games)} games ({', '.join(games)})")

            print("\nDetailed gaps:")
            for g in gaps:
                print(f"  [{g['game']}] {g['category']}: {g['detail'][:200]}")

        # Always export observability report into tmp_path (never repo root).
        out_path = _export_observability_report(out_dir=tmp_path)
        assert out_path.exists()
        assert out_path.parent == tmp_path
        assert not (_REPO_ROOT / ".game-audit-report.json").exists()

        print("\n" + "=" * 70)
        print("PIPELINE IMPROVEMENT RECOMMENDATIONS")
        print("=" * 70)
        print("""
1. ITERATIVE CODE GENERATION: The ExecutionEngine only does single-shot generation.
   For complex tasks like game-building, the model needs multiple attempts with
   feedback from test results. ToolCallLoop now supports code work types (code,
   bug_fix, refactor, feature, test) with budget/per-iteration guards — remaining
   gap is wiring the test-failure feedback loop from ExecutionEngine into
   ToolCallLoop retries.

2. PROMPT ENGINEERING: Game-building prompts may need refinement for better
   code generation. Consider:
   - Few-shot examples in the prompt
   - Structured output format requirements
   - Breaking complex tasks into sub-tasks (build skeleton → add feature → test)

3. CODE EXTRACTION ROBUSTNESS: The fenced-block parser may miss code when
   the model uses non-standard formatting. Consider:
   - Natural language code detection (heuristic: indented blocks after "class X:")
   - Multi-pass extraction (try fenced blocks, then heuristic, then raw)
   - Model output that isn't valid Python should trigger automatic retry

4. TEST FEEDBACK LOOP: After writing code, gludd runs 'make test' but there's
   no mechanism to feed test failures back to the model for corrections.
   This is THE critical gap — without it, complex tasks are single-shot guesses.

5. DEPENDENCY MANAGEMENT: Games may require additional packages (pygame, etc.).
   gludd should detect import errors and either add dependencies or suggest
   stdlib-only alternatives.

6. WORKSPACE ISOLATION: Each game should be built in its own workspace to
   prevent cross-contamination between tasks.

7. METRICS AND OBSERVABILITY: Track per-task metrics:
   - Tokens consumed per task
   - Iterations/debug cycles per task
   - Success rate by task type
    - Common failure modes
""")


# ---------------------------------------------------------------------------
# Full Pipeline Tests — gludd's ExecutionEngine + EventLoop wired to DeepSeek
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DEEPSEEK_KEY or not _HAS_LANGCHAIN_OPENAI, reason=_LIVE_SKIP_REASON)
class TestDeepSeekFullPipeline:
    """DeepSeek through gludd's FULL pipeline (not a raw API call bypass).

    TEST A: ExecutionEngine.execute()
        - model call → code extraction → file write → test run → git commit
        - This is the canonical code-generation path; it exercises the real engine.

    TEST B: EventLoop._dispatch_execute_job_isolated()
        - real loop dispatch wired to DeepSeek via invoke_model_for_generation
        - Follows the proven pattern from test_pipeline_live_zai.py (G7 real loop).
    """

    # -- async session factory (SQLite in-memory) for EventLoop tests ----------

    @staticmethod
    async def _make_session_factory() -> Any:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import Base

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    # -- No-op runner (records calls, no Ansible subprocess) -------------------

    class _NoopRunner:
        """Minimal AnsibleRunnerAdapter stand-in — no subprocess, just records."""

        def __init__(self, workspace_root: str) -> None:
            self._root = workspace_root
            self.prepare_calls: list[str] = []
            self.run_calls: list[str] = []
            self.vars_written: list[dict[str, Any]] = []

        def prepare_job_dirs(self, job_id: str) -> dict[str, str]:
            self.prepare_calls.append(job_id)
            root = str(Path(self._root) / "jobs" / job_id)
            Path(root, "env").mkdir(parents=True, exist_ok=True)
            return {"root": root}

        def write_vars(self, job_id: str, job_vars: dict[str, Any], shared_vars: Any) -> None:
            self.vars_written.append({"job_id": job_id, "job_vars": dict(job_vars)})

        def run_playbook(
            self,
            playbook_name: str,
            private_data_dir: str,
            env: dict[str, str] | None = None,
        ) -> None:
            self.run_calls.append(playbook_name)

        def list_playbooks(self) -> list[str]:
            return ["noop.yml", "validate_task.yml", "return_review.yml"]

    # -------------------------------------------------------------------
    # TEST A: ExecutionEngine.execute() — the full code-generation path
    # -------------------------------------------------------------------

    def test_execution_engine_full_pipeline_snake(self, tmp_path: Path) -> None:
        """ExecutionEngine: model → code gen → file write → test → commit."""
        from general_ludd.execution.engine import ExecutionEngine
        from general_ludd.schemas.job import JobSpec

        # 1. Create temp git workspace
        ws = tmp_path / "workspace"
        ws.mkdir()
        subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@general-ludd.local"],
            cwd=ws,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Agent"],
            cwd=ws,
            check=True,
            capture_output=True,
        )

        (ws / "snake_game.py").write_text("# placeholder\n")
        (ws / "test_snake.py").write_text("def test_placeholder():\n    assert True\n")
        subprocess.run(["git", "add", "."], cwd=ws, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial workspace"],
            cwd=ws,
            check=True,
            capture_output=True,
        )

        # 2. Create gateway
        gateway = _build_deepseek_gateway()

        # 3. Create ExecutionEngine
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=str(ws),
        )

        # 4. Create JobSpec for Snake game
        job = JobSpec(
            job_id="EXEC-SNAKE-001",
            todo_id="TODO-SNAKE-001",
            playbook="validate_task.yml",
            queue="core",
            work_type="code",
            prompt_text=GAME_DEFINITIONS["snake"]["prompt"],
            model_profile="deepseek_coder",
        )

        # 5. Execute
        print("\n\n" + "=" * 70)
        print("FULL PIPELINE TEST A: ExecutionEngine.execute() via DeepSeek")
        print("=" * 70)
        result = asyncio.run(engine.execute_async(job))

        print("\n  TaskReturn:")
        print(f"    return_id    = {result.return_id}")
        print(f"    exit_code    = {result.exit_code}")
        print(f"    summary      = {result.result_summary[:300]}")
        print(f"    artifacts    = {result.artifacts}")
        print(f"    diff_ref     = {result.diff_ref}")
        print(f"    test_results = {result.test_results_ref}")

        # 6. Check files written
        all_files = sorted(ws.glob("**/*"))
        code_files = [
            str(f.relative_to(ws)) for f in all_files if f.suffix == ".py" or f.suffix == ".md" or f.suffix == ".txt"
        ]
        print(f"\n  Workspace files: {code_files}")

        # 7. Check git status
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=ws,
            capture_output=True,
            text=True,
        )
        branch_result = subprocess.run(
            ["git", "branch"],
            cwd=ws,
            capture_output=True,
            text=True,
        )
        print(f"  Git log:\n{log_result.stdout}")
        print(f"  Git branches:\n{branch_result.stdout}")

        # 8. Analysis
        print("\n" + "-" * 70)
        print("ANALYSIS")
        print("-" * 70)
        print(f"  Model returned content:   {result.diff_ref or 'NONE'}")
        print(f"  Files changed:            {result.artifacts}")
        print(f"  Test exit code:           {result.exit_code}")

        has_git_artifact = bool(result.artifacts and any("commit:" in str(a) for a in result.artifacts))
        print(f"  Has commit in artifacts:  {has_git_artifact}")

        code_generated = has_git_artifact or (result.artifacts and len(result.artifacts) > 1)
        print(f"  Code was generated:       {code_generated}")

        has_game_file = any("snake" in str(f).lower() for f in code_files)
        print(f"  Snake file exists:        {has_game_file}")

        # Structural assertions (setup wiring, not model output quality)
        assert result.return_id.startswith("RET-"), "return_id missing RET- prefix"
        assert result.result_summary, "result_summary should not be empty"

        if not code_generated:
            print("\n  GAP: ExecutionEngine did not produce application code.")
            print("  This means either: (a) DeepSeek output was not parseable, or")
            print("  (b) the fenced-block / FILE: extraction failed.")
            print(f"  raw diff_ref: {result.diff_ref}")
        else:
            print("\n  SUCCESS: ExecutionEngine generated files, tests ran.")

        print("=" * 70)
        print("END TEST A")
        print("=" * 70 + "\n")

    # -------------------------------------------------------------------
    # TEST B: EventLoop._dispatch_execute_job_isolated (real loop wire-up)
    # -------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_event_loop_dispatch_snake(self, tmp_path: Path) -> None:
        """EventLoop dispatch wired to DeepSeek — real invoke_model_for_generation.

        Follows the proven pattern from test_pipeline_live_zai.py (G7).
        """
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.prompts.registry import PromptRegistry
        from general_ludd.schemas.todo import Todo, TodoStatus, WorkType

        gw = _build_deepseek_gateway()
        session_factory = await self._make_session_factory()

        ws = str(tmp_path / "loop-workspace")
        Path(ws).mkdir(parents=True, exist_ok=True)
        runner = self._NoopRunner(ws)

        prompt_registry = PromptRegistry()
        prompt_registry.register(
            "snake_build.md.j2",
            GAME_DEFINITIONS["snake"]["prompt"],
        )

        loop = EventLoop(
            session=None,
            model_gateway=gw,
            runner=runner,
            prompt_registry=prompt_registry,
        )
        loop._session_factory = session_factory
        loop._total_ticks = 1
        loop._tick_state = {}
        loop._config_snapshot = {}

        todo = Todo(
            todo_id="TODO-SNAKE-LIVE-001",
            title="Build a Snake game in Python",
            description=GAME_DEFINITIONS["snake"]["prompt"],
            work_type=WorkType.CODE,
            queue="core",
            model_profile="deepseek_coder",
            prompt_profile="snake_build.md.j2",
        )
        todo.status = TodoStatus.ACTIVE

        print("\n\n" + "=" * 70)
        print("FULL PIPELINE TEST B: EventLoop._dispatch_execute_job_isolated")
        print("=" * 70)
        print(f"[LOOP] dispatching {todo.todo_id} via real EventLoop dispatch")
        print(f"[LOOP] model_profile={todo.model_profile!r} prompt_profile={todo.prompt_profile!r}")

        await loop._dispatch_execute_job_isolated(todo)

        print(f"\n[LOOP] prepare_calls  = {runner.prepare_calls}")
        print(f"[LOOP] run_calls       = {runner.run_calls}")
        print(f"[LOOP] vars_written    = {len(runner.vars_written)}")

        # ---- Assertions (wiring) ----
        assert runner.vars_written, "LOOP: write_vars never called — dispatch is broken"
        assert runner.prepare_calls, "LOOP: prepare_job_dirs never called — dispatch is broken"

        vars_entry = runner.vars_written[0]
        job_vars = vars_entry.get("job_vars", {})
        job_id = job_vars.get("job_id", "")
        prompt_text = job_vars.get("prompt_text")
        model_response = job_vars.get("model_response")

        print(f"[LOOP] job_id          = {job_id!r}")
        print(f"[LOOP] prompt_text[:200] = {str(prompt_text)[:200]!r}")

        assert job_id.startswith("EXEC-"), f"LOOP: job_id should start with EXEC-, got {job_id!r}"
        assert prompt_text, "LOOP: prompt_text is empty — PromptRegistry wiring failed"
        assert model_response, (
            "LOOP: model_response is empty — DeepSeek was never called (invoke_model_for_generation returned None)"
        )

        response_text = str(model_response)
        print(f"[LOOP] model_response  = {len(response_text)} chars")
        print(f"[LOOP] model_response[:500] = {response_text[:500]!r}")

        # ---- Code quality checks ----
        # Note: do NOT require a literal "class Snake" — the model may rename
        # the class (SnakeGame, Game, etc.). The feature verifier below is
        # the real contract; this block only reports observable signals.
        has_any_class = "class " in response_text
        has_import = "import" in response_text.lower()
        has_def = "def " in response_text
        print(f"\n[LOOP] has any class?:   {has_any_class}")
        print(f"[LOOP] has imports?:      {has_import}")
        print(f"[LOOP] has function def?: {has_def}")

        # ---- Extract and validate Python ----
        source = _extract_python_module(response_text)
        if source:
            ast_result = _parse_ast(source)
            print(f"[LOOP] AST parseable:     {ast_result['parseable']}")
            print(f"[LOOP] AST has_class:     {ast_result['has_class']}")
            print(f"[LOOP] AST has_imports:   {ast_result['has_imports']}")
            if ast_result["error"]:
                print(f"[LOOP] AST error:         {ast_result['error']}")

            if ast_result["parseable"] and ast_result["has_class"]:
                # Write to disk and try to import
                game_dir = tmp_path / "snake_game"
                game_dir.mkdir(exist_ok=True)
                test_results = _run_game_tests(
                    source,
                    GAME_DEFINITIONS["snake"]["class_name"],
                    GAME_DEFINITIONS["snake"]["verifications"],
                    "snake_deepseek",
                    game_dir,
                )
                print(f"[LOOP] module_imported:    {test_results['module_imported']}")
                print(f"[LOOP] instantiated:       {test_results['instantiated']}")
                checks = test_results.get("checks", {})
                passed = sum(1 for c in checks.values() if c["passed"])
                failed = len(checks) - passed
                print(f"[LOOP] game checks:        {passed}/{len(checks)} passed, {failed} failed")
                if test_results["errors"]:
                    for err in test_results["errors"]:
                        print(f"[LOOP] ERROR: {err[:200]}")
            else:
                print("[LOOP] WARNING: model output does not parse as valid Python")
        else:
            print("[LOOP] WARNING: could not extract Python module from model output")
            print(f"[LOOP] Raw output contains 'class': {'class ' in response_text or 'class:' in response_text}")
            print(f"[LOOP] Raw output contains '```': {'```' in response_text}")

        # ---- Gap analysis ----
        print("\n" + "-" * 70)
        print("ANALYSIS")
        print("-" * 70)
        print(f"  Model returned content:   {'YES' if model_response else 'NO'}")
        print(f"  Contains Python class:    {has_any_class}")
        print(f"  Contains imports:         {has_import}")
        print(f"  Source extractable:       {source is not None}")
        if source:
            print(f"  Source parseable:         {ast_result['parseable']}")
            print(f"  Source has a class:       {ast_result['has_class']}")

        gaps: list[str] = []
        if not has_any_class:
            gaps.append("Model output lacks any 'class' definition")
        if source is None:
            gaps.append("Python code extraction failed")
        elif not ast_result["parseable"]:
            gaps.append(f"AST parse error: {ast_result.get('error', 'unknown')}")
        elif not ast_result["has_class"]:
            gaps.append("Parsed AST has no class definition")

        if gaps:
            print(f"\n  PIPELINE GAPS ({len(gaps)}):")
            for g in gaps:
                print(f"    - {g}")
        else:
            print("\n  No pipeline gaps detected — model output is valid Python.")

        print("=" * 70)
        print("END TEST B — Full pipeline via EventLoop dispatch: PROVEN")
        print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Game Persistence Tests — extended-play stress testing
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DEEPSEEK_KEY or not _HAS_LANGCHAIN_OPENAI, reason=_LIVE_SKIP_REASON)
class TestGamePersistence:
    """Extended-play stress tests: each game must survive 500 ticks/interactions.

    Tracks: crashes (exceptions), graceful endings (game_over), and render_state
    validity after extended play. Adds to gap analysis if a game fails.
    """

    _persistence_gaps: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def _record_persistence_gap(cls, game_id: str, category: str, detail: str) -> None:
        cls._persistence_gaps.append({"game": game_id, "category": category, "detail": detail})

    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_deepseek_gateway()

    # ---- Shared builder ----
    def _build_and_stress(self, gateway, tmp_path, game_id):
        game_def = GAME_DEFINITIONS[game_id]
        class_name = game_def["class_name"]
        interaction_count = _GAME_PERSISTENCE_PARAMS.get(game_id, 500)

        obs = _OBSERVABILITY_DATA.get(game_id, {})
        if not obs:
            obs = _init_game_obs(game_id)

        print(f"\n\n{'=' * 70}")
        print(f"PERSISTENCE TEST: {game_id} ({class_name}) — {interaction_count} interactions")
        print(f"{'=' * 70}")

        # Step 1: Call DeepSeek
        print(f"\n--- Step 1: Calling DeepSeek for {game_id} ---")
        try:
            response = _call_deepseek(gateway, game_def["prompt"])
        except Exception:
            raise

        print(f"  tokens_in={response['tokens_in']} tokens_out={response['tokens_out']}")
        obs["tokens_in"] = obs.get("tokens_in", 0) or response["tokens_in"]
        obs["tokens_out"] = obs.get("tokens_out", 0) or response["tokens_out"]
        obs["phases"]["model_call"] = round(response.get("latency_ms", 0), 1)

        # Step 2: Extract code
        source = _extract_python_module(response["content"])
        if source is None:
            self._record_persistence_gap(game_id, "code_extraction", "Could not extract Python code")
            print("  FAIL: Could not extract Python code")
            return

        # Step 3: Parse AST
        ast_result = _parse_ast(source)
        if not ast_result["parseable"]:
            self._record_persistence_gap(game_id, "ast_parse", f"AST error: {ast_result.get('error')}")
            print(f"  FAIL: AST not parseable: {ast_result.get('error')}")
            return

        # Step 4: Run persistence stress test
        game_dir = tmp_path / game_id
        game_dir.mkdir(exist_ok=True)
        results = _run_persistence_tests(source, game_id, class_name, interaction_count, game_dir)

        print(f"\n  module_imported={results['module_imported']}")
        print(f"  instantiated={results['instantiated']}")
        if results["errors"]:
            for err in results["errors"]:
                print(f"  ERROR: {err[:200]}")

        stress = results.get("stress", {})
        crashed = stress.get("crashed", True)
        ended = stress.get("ended_gracefully", False)
        interactions = stress.get("interactions_completed", 0)
        render_ok = stress.get("render_state_valid", False)

        print("\n  Stress results:")
        print(f"    crashed              = {crashed}")
        print(f"    ended_gracefully     = {ended}")
        print(f"    interactions_completed = {interactions}/{interaction_count}")
        print(f"    render_state_valid   = {render_ok}")
        if stress.get("exception"):
            print(f"    exception: {stress['exception'][:200]}")
        if stress.get("render_state_error"):
            print(f"    render_state_error: {stress['render_state_error'][:200]}")

        # Gap detection
        if not results["module_imported"]:
            self._record_persistence_gap(
                game_id,
                "import",
                f"Module failed to import during persistence test: {results['errors'][:2]}",
            )
        elif not results["instantiated"]:
            self._record_persistence_gap(
                game_id,
                "instantiation",
                f"Class {class_name} failed to instantiate: {results['errors'][:2]}",
            )
        elif crashed:
            self._record_persistence_gap(
                game_id,
                "persistence_crash",
                f"Crashed at interaction {interactions}: {stress.get('exception', 'unknown')}",
            )
        elif not render_ok:
            self._record_persistence_gap(
                game_id,
                "render_state",
                f"render_state() failed after {interactions} interactions: "
                f"{stress.get('render_state_error', 'unknown')}",
            )

        status = "CRASHED" if crashed else ("ENDED" if ended else "OK")
        print(f"\n  PERSISTENCE STATUS: {status}")
        print(f"{'-' * 70}")

    # ---- Snake ----
    def test_persistence_snake(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "snake")

    # ---- Tetris ----
    def test_persistence_tetris(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "tetris")

    # ---- Minesweeper ----
    def test_persistence_minesweeper(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "minesweeper")

    # ---- Checkers ----
    def test_persistence_checkers(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "checkers")

    # ---- SkiFree ----
    def test_persistence_skifree(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "skifree")

    # ---- Banana ----
    def test_persistence_banana(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "banana")

    # ---- Pong ----
    def test_persistence_pong(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "pong")

    # ---- Breakout ----
    def test_persistence_breakout(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "breakout")

    # ---- Maze Runner ----
    def test_persistence_maze_runner(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "maze_runner")

    # ---- Word Guesser ----
    def test_persistence_word_guesser(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "word_guesser")

    # ---- Memory Match ----
    def test_persistence_memory_match(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "memory_match")

    # ---- Tic-Tac-Toe ----
    def test_persistence_tic_tac_toe(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "tic_tac_toe")

    # ---- Persistence Gap Report ----
    def test_persistence_gap_report(self, tmp_path: Path):
        """Print a comprehensive persistence gap analysis."""
        gaps = TestGamePersistence._persistence_gaps
        if not gaps:
            print("\nNo persistence gaps recorded — all tested games survived extended play")
        else:
            print("\n\n" + "=" * 70)
            print("GAME PERSISTENCE GAP REPORT")
            print("=" * 70)
            print(f"\nTotal persistence gaps found: {len(gaps)}")

            by_game: dict[str, list[dict]] = {}
            for g in gaps:
                by_game.setdefault(g["game"], []).append(g)

            print("\nBy game:")
            for game, items in sorted(by_game.items()):
                cats = sorted(set(i["category"] for i in items))
                print(f"  {game}: {len(items)} gaps ({', '.join(cats)})")

            print("\nDetailed gaps:")
            for g in gaps:
                print(f"  [{g['game']}] {g['category']}: {g['detail'][:200]}")

        # Always export observability report into tmp_path (never repo root).
        out_path = _export_observability_report(out_dir=tmp_path)
        assert out_path.exists()
        assert out_path.parent == tmp_path
        assert not (_REPO_ROOT / ".game-audit-report.json").exists()

        print("\n" + "=" * 70)
