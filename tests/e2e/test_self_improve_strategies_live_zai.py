"""End-to-end test: self-improvement loop strategies via EventLoop._phase_self_improve.

What is wired today (verified against source):
  SelfImprovementHarness.run_gap_analysis() — heuristic, filesystem-driven:
    * missing_tests  — walks src/, checks tests/ for matching test_<name>.py
    * dead_code      — regex-finds class names that appear <=1 time in all src
    * low_coverage   — parses coverage.xml if present (else skipped)
  generate_fix_todos(findings) — maps findings -> todo dicts with title/description/work_type
  _persist_self_improve_todos(todos) — runs through SelfImproveGate admission check,
      calls TodoRepository.create() for each admitted todo.

What is NOT yet wired (GAP documented here with xfail/skip markers):
  * Model-driven analysis: no call to ModelGateway inside SelfImprovementHarness.
    The harness is purely heuristic; glm-4.6 is NOT consulted to detect gaps.
  * Config-improvement suggestions: harness produces "test/code work" todos,
    not structured config-patch suggestions.

OFFLINE tests (always run, no key or network):
  1. test_gap_analysis_returns_structured_findings
     Harness.run_gap_analysis() on a minimal tmpdir with one src file missing a test
     returns at least one finding with required keys.
  2. test_generate_fix_todos_shape
     generate_fix_todos() maps findings to dicts with title/description/work_type.
  3. test_phase_self_improve_persists_todo_via_mock_repo
     EventLoop._phase_self_improve() with a fake todo_repo → assert create() called.
  4. test_self_improve_gate_caps_open_todos
     SelfImproveGate.evaluate() with open_count >= max_open returns admitted=False.
  5. test_self_improve_gate_auto_queue_vs_approval
     Gate with auto_queue=True gives QUEUED; auto_queue=False gives APPROVAL_REQUIRED.
  6. test_phase_self_improve_interval_skip
     interval=3, total_ticks=2 → phase exits immediately (no harness work).
  7. test_model_driven_analysis_not_yet_wired (xfail)
     Documents that harness does NOT accept a model_gateway parameter; model-analysis
     is a gap.

LIVE test (skip without ZAI_API_KEY):
  8. test_live_zai_harness_heuristic_path_and_gap_documented
     Runs the harness heuristic in a real tempdir with a real ModelGateway wired to
     glm-4.6; asserts heuristic path produces structured output.  Attempts to feed
     the findings to glm-4.6 to get a model-generated analysis — xfail if the
     harness has no model path (gap documented).

Run:
    ZAI_API_KEY=<key> uv run pytest tests/e2e/test_self_improve_strategies_live_zai.py -s -v
or just:
    uv run pytest tests/e2e/test_self_improve_strategies_live_zai.py -s -v
(offline tests pass without a key)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Key loading — read from env (NEVER print the key)
# ---------------------------------------------------------------------------

_ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_ZAI_MODEL = "glm-4.6"
_REPO_ROOT = Path(__file__).parent.parent.parent


def _load_zai_key() -> str | None:
    key = os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    key_file = _REPO_ROOT / ".zai.key"
    if key_file.exists():
        v = key_file.read_text().strip()
        return v if v else None
    return None


_ZAI_KEY = _load_zai_key()
_SKIP_REASON = (
    "ZAI_API_KEY not set and .zai.key not found — "
    "set ZAI_API_KEY or place key in .zai.key to run the live self-improve z.ai test"
)

# ---------------------------------------------------------------------------
# Gateway builder (mirrors test_pipeline_live_zai.py)
# ---------------------------------------------------------------------------

def _build_gateway(profile_id: str = "zai_self_improve") -> Any:
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    profile = ModelProfile(
        model_profile_id=profile_id,
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_ZAI_MODEL,
        api_base_alias="ZAI_BASE_URL",
        credential_alias="ZAI_API_KEY",
        context_window=64000,
        max_input_tokens=60000,
        max_output_tokens=512,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=1.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    assert _ZAI_KEY, "key must be set before building gateway"
    secrets.set("ZAI_API_KEY", _ZAI_KEY)
    secrets.set("ZAI_BASE_URL", _ZAI_BASE_URL)
    return ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=cast(Any, secrets),
    )


# ---------------------------------------------------------------------------
# Helpers: create a minimal repo-tree in a temp dir
# ---------------------------------------------------------------------------

def _make_minimal_repo(base: Path) -> Path:
    """Create a tiny src + tests tree with one src file that has NO test file.

    src/general_ludd/
        mymodule.py   <- has a class MyService (no matching test_mymodule.py)
    tests/
        test_other.py <- unrelated, so missing_tests gap fires for mymodule.py
    """
    src_dir = base / "src" / "general_ludd"
    src_dir.mkdir(parents=True)
    (src_dir / "mymodule.py").write_text(
        "class MyService:\n"
        "    def run(self) -> None:\n"
        "        pass\n"
    )
    tests_dir = base / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_other.py").write_text("def test_placeholder(): pass\n")
    return base


# ===========================================================================
# OFFLINE TESTS — always run, no key or network needed
# ===========================================================================

class TestSelfImprovementHarnessOffline:
    """Offline unit/integration tests for SelfImprovementHarness + gate."""

    def test_gap_analysis_returns_structured_findings(self, tmp_path: Path) -> None:
        """run_gap_analysis() on a minimal tmpdir finds the missing test file.

        The harness walks src/ and tests/; mymodule.py has no test_mymodule.py
        so at least one 'missing_tests' finding must be returned with the
        required keys: type, file, severity, message.
        """
        from general_ludd.self_improve.harness import SelfImprovementHarness

        _make_minimal_repo(tmp_path)
        harness = SelfImprovementHarness(repo_root=str(tmp_path))
        findings = harness.run_gap_analysis()

        assert isinstance(findings, list), (
            "run_gap_analysis() must return a list"
        )
        assert len(findings) >= 1, (
            f"Expected at least one finding for the missing test file; got {findings!r}"
        )

        # Find the missing_tests finding for mymodule.py
        missing_test_findings = [
            f for f in findings if f.get("type") == "missing_tests"
            and "mymodule.py" in f.get("file", "")
        ]
        assert missing_test_findings, (
            f"Expected a 'missing_tests' finding for mymodule.py; "
            f"all findings: {findings!r}"
        )

        f0 = missing_test_findings[0]
        for key in ("type", "file", "severity", "message"):
            assert key in f0, (
                f"Finding missing required key {key!r}: {f0!r}"
            )
        assert f0["severity"] == "high", (
            f"missing_tests findings must have severity='high', got {f0['severity']!r}"
        )

        print(f"\n[OFFLINE] gap_analysis returned {len(findings)} finding(s)")
        print(f"[OFFLINE] missing_tests finding: {f0['message']!r}")

    def test_generate_fix_todos_shape(self, tmp_path: Path) -> None:
        """generate_fix_todos() maps findings to dicts with required keys.

        Each todo dict must have: title, description, work_type, priority.
        missing_tests findings must map to work_type='test'.
        """
        from general_ludd.self_improve.harness import SelfImprovementHarness

        _make_minimal_repo(tmp_path)
        harness = SelfImprovementHarness(repo_root=str(tmp_path))
        findings = harness.run_gap_analysis()
        assert findings, "precondition: need at least one finding"

        todos = harness.generate_fix_todos(findings)
        assert isinstance(todos, list), "generate_fix_todos() must return a list"
        assert len(todos) == len(findings), (
            f"Expected one todo per finding ({len(findings)}); got {len(todos)}"
        )

        for i, todo in enumerate(todos):
            for key in ("title", "description", "work_type", "priority"):
                assert key in todo, (
                    f"Todo[{i}] missing required key {key!r}: {todo!r}"
                )
            assert isinstance(todo["title"], str) and todo["title"].strip(), (
                f"Todo[{i}] title must be a non-empty string: {todo['title']!r}"
            )

        # missing_tests todos must have work_type='test'
        missing_todos = [t for t in todos if t.get("gap_type") == "missing_tests"]
        assert missing_todos, "Expected at least one missing_tests todo"
        assert all(t["work_type"] == "test" for t in missing_todos), (
            f"missing_tests todos must have work_type='test'; got {[t['work_type'] for t in missing_todos]!r}"
        )

        print(f"\n[OFFLINE] generate_fix_todos returned {len(todos)} todo(s)")
        for t in todos[:3]:
            print(f"  title={t['title']!r}  work_type={t['work_type']!r}")

    def test_phase_self_improve_persists_todo_via_mock_repo(
        self, tmp_path: Path
    ) -> None:
        """EventLoop._phase_self_improve() calls todo_repo.create() for each admitted todo.

        Wires the real EventLoop with:
          - a minimal repo_root so harness finds at least one gap
          - a mock TodoRepository whose list_by_work_type returns [] and
            create() records calls
          - a mock AsyncSession (flush is a no-op)
          - self_improve_interval=1, total_ticks=1 (fires every tick)

        Asserts: TodoRepository.create() was called at least once, and the
        payload has the required shape.
        """
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.self_improve.harness import SelfImprovementHarness

        _make_minimal_repo(tmp_path)

        # Patch SelfImprovementHarness to use our controlled tmpdir
        created_payloads: list[dict[str, Any]] = []

        mock_todo_repo = MagicMock()
        mock_todo_repo.list_by_work_type = AsyncMock(return_value=[])
        mock_todo_repo.create = AsyncMock(side_effect=lambda payload: created_payloads.append(dict(payload)))

        mock_session = MagicMock()
        mock_session.flush = AsyncMock()

        loop = EventLoop(
            session=None,
            model_gateway=None,
            self_improve_interval=1,
        )
        loop._total_ticks = 1          # fires on tick 1 (1 % 1 == 0)
        loop._tick_metrics = {}
        loop._todo_repo = mock_todo_repo
        loop._active_session = mock_session

        # Override SelfImprovementHarness to point at our tmpdir
        with patch(
            "general_ludd.event_loop.loop.SelfImprovementHarness",
            lambda **kwargs: SelfImprovementHarness(repo_root=str(tmp_path)),
        ):
            asyncio.run(loop._phase_self_improve())

        assert created_payloads, (
            "_phase_self_improve() did not call todo_repo.create() — "
            "either no findings were generated or SelfImproveGate rejected all of them. "
            f"tick_metrics: {loop._tick_metrics!r}"
        )

        # Each payload must have the required keys
        for i, payload in enumerate(created_payloads):
            for key in ("title", "description", "status", "work_type"):
                assert key in payload, (
                    f"created_payloads[{i}] missing key {key!r}: {payload!r}"
                )
            assert payload["work_type"] in ("test", "code", "self_improve"), (
                f"Unexpected work_type in payload: {payload['work_type']!r}"
            )
            assert payload["created_by"] == "self_improve_harness", (
                f"Expected created_by='self_improve_harness', got {payload.get('created_by')!r}"
            )

        print(f"\n[OFFLINE] _phase_self_improve persisted {len(created_payloads)} todo(s)")
        for p in created_payloads[:3]:
            print(f"  title={p['title']!r}  status={p['status']!r}")

    def test_self_improve_gate_caps_open_todos(self) -> None:
        """SelfImproveGate rejects todos when open_count >= max_open."""
        from general_ludd.self_improve.gate import SelfImproveGate

        gate = SelfImproveGate(max_open=5)
        todo = {"title": "Fix something", "description": "...", "priority": "high"}

        # At capacity
        decision = gate.evaluate(todo, open_count=5)
        assert not decision.admitted, (
            "Gate must reject todos when open_count == max_open"
        )
        assert decision.initial_status == "", (
            f"Rejected todo must have empty initial_status, got {decision.initial_status!r}"
        )

        # Over capacity
        decision_over = gate.evaluate(todo, open_count=10)
        assert not decision_over.admitted, (
            "Gate must reject todos when open_count > max_open"
        )

        # One below capacity — must be admitted
        decision_ok = gate.evaluate(todo, open_count=4)
        assert decision_ok.admitted, (
            "Gate must admit todos when open_count < max_open"
        )

        print("\n[OFFLINE] SelfImproveGate cap: PASS")

    def test_self_improve_gate_auto_queue_vs_approval(self) -> None:
        """Gate initial_status: auto_queue=True -> QUEUED, False -> APPROVAL_REQUIRED."""
        from general_ludd.schemas.todo import TodoStatus
        from general_ludd.self_improve.gate import SelfImproveGate

        todo: dict[str, Any] = {"title": "Fix something"}

        gate_auto = SelfImproveGate(max_open=10, auto_queue=True)
        dec_auto = gate_auto.evaluate(todo, open_count=0)
        assert dec_auto.admitted
        assert dec_auto.initial_status == TodoStatus.QUEUED.value, (
            f"auto_queue=True must yield QUEUED, got {dec_auto.initial_status!r}"
        )

        gate_hold = SelfImproveGate(max_open=10, auto_queue=False)
        dec_hold = gate_hold.evaluate(todo, open_count=0)
        assert dec_hold.admitted
        assert dec_hold.initial_status == TodoStatus.APPROVAL_REQUIRED.value, (
            f"auto_queue=False must yield APPROVAL_REQUIRED, got {dec_hold.initial_status!r}"
        )

        print("\n[OFFLINE] Gate initial_status routing: PASS")

    def test_phase_self_improve_interval_skip(self) -> None:
        """_phase_self_improve() exits early when total_ticks % interval != 0.

        interval=3, total_ticks=2 (2 % 3 != 0) -> no harness work fired;
        tick_metrics must not contain 'self_improve_gaps'.
        """
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(session=None, self_improve_interval=3)
        loop._total_ticks = 2
        loop._tick_metrics = {}

        # If SelfImprovementHarness were imported/constructed it would fail
        # (no repo_root set), so the test also implicitly proves early-exit.
        asyncio.run(loop._phase_self_improve())

        assert "self_improve_gaps" not in loop._tick_metrics, (
            "Phase must skip all work and leave tick_metrics clean "
            f"when total_ticks % interval != 0; got {loop._tick_metrics!r}"
        )
        print("\n[OFFLINE] interval skip: PASS")

    @pytest.mark.xfail(
        strict=True,
        reason="E9: model_gateway not yet wired into SelfImprovementHarness "
        "(AGENTIC_IMPLEMENTATION_SPEC.md §E9)",
    )
    def test_model_driven_analysis_not_yet_wired(self) -> None:
        """SelfImprovementHarness does not yet accept a model_gateway argument.

        When model-driven analysis is wired, this test will XPASS (strict
        fail), signaling that the xfail marker should be removed and the
        test updated to assert real model-generated suggestions.
        """
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness()
        assert hasattr(harness, "model_gateway"), (
            "SelfImprovementHarness still has no model_gateway — "
            "gap NOT yet wired; this xfail absorbs the expected failure."
        )


# ===========================================================================
# LIVE TEST — skips without ZAI_API_KEY
# ===========================================================================

@pytest.mark.skipif(not _ZAI_KEY, reason=_SKIP_REASON)
class TestSelfImproveLiveZai:
    """Live z.ai test: heuristic harness + model-analysis gap documented with xfail.

    This class runs only when ZAI_API_KEY is set.
    """

    def test_live_zai_harness_heuristic_path_and_gap_documented(
        self, tmp_path: Path
    ) -> None:
        """Live: heuristic harness runs to completion and findings are well-formed.

        PART 1 (heuristic):
          Run SelfImprovementHarness on a minimal tmpdir; assert findings have
          required shape and generate_fix_todos() maps them to structured todos.

        PART 2 (model-analysis gap):
          Feed the findings to glm-4.6 via the real ModelGateway to request a
          structured improvement suggestion. This tests that the gateway is
          callable. The harness does NOT currently accept a gateway, so the
          model call is made OUTSIDE the harness — documenting the integration
          gap.  The model response is asserted non-empty; the test prints both
          the heuristic findings and the model response so the gap is visible in
          CI output.
        """
        from general_ludd.self_improve.harness import SelfImprovementHarness

        # ---- PART 1: heuristic pipeline ----------------------------------------
        _make_minimal_repo(tmp_path)
        harness = SelfImprovementHarness(repo_root=str(tmp_path))
        findings = harness.run_gap_analysis()
        assert isinstance(findings, list), "run_gap_analysis() must return a list"
        assert len(findings) >= 1, (
            f"Expected at least one finding; got {findings!r}"
        )

        todos = harness.generate_fix_todos(findings)
        assert isinstance(todos, list) and len(todos) == len(findings), (
            f"generate_fix_todos() must return one todo per finding; "
            f"findings={len(findings)} todos={len(todos)}"
        )

        print(f"\n[LIVE-ZAI] Heuristic gap analysis: {len(findings)} finding(s)")
        for f in findings[:3]:
            print(f"  [{f.get('type')}] {f.get('message','')[:120]}")

        print(f"\n[LIVE-ZAI] Generated {len(todos)} todo(s):")
        for t in todos[:3]:
            print(f"  title={t['title']!r}  work_type={t['work_type']!r}")

        # ---- PART 2: real ModelGateway call (gap integration point) ------------
        #
        # The harness itself is heuristic-only. This section proves the gateway is
        # functional and shows what model-driven analysis WOULD look like if it
        # were wired into the harness. This is the missing link documented by the
        # xfail in TestSelfImprovementHarnessOffline.test_model_driven_analysis_not_yet_wired.

        gateway = _build_gateway("zai_self_improve")

        summary = "\n".join(
            f"- [{f.get('type')}] {f.get('message','')}" for f in findings[:5]
        )
        prompt = (
            "You are a code-improvement advisor. "
            "The following gaps were found in a Python project:\n\n"
            f"{summary}\n\n"
            "Reply with a JSON object (no markdown fences) with exactly these keys:\n"
            '  "priority_gap": "<the most important gap as a short string>",\n'
            '  "suggested_action": "<one concrete action to address it>",\n'
            '  "confidence": <float 0.0-1.0>\n'
            "Output ONLY the JSON object."
        )

        response = gateway.call_model(
            "zai_self_improve",
            messages=[{"role": "user", "content": prompt}],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )

        content = response.content
        usage = response.usage_metadata or {}
        tokens_out = int(
            usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        )

        print(f"\n[LIVE-ZAI] Model response (first 400 chars): {content[:400]!r}")
        print(f"[LIVE-ZAI] Token usage: {usage}")

        assert isinstance(content, str) and content.strip(), (
            "glm-4.6 returned empty content for the improvement analysis prompt"
        )
        assert tokens_out > 0 or usage, (
            f"Expected non-zero token usage from glm-4.6; usage={usage}"
        )

        # Try to parse the model's JSON reply (best-effort — not a hard failure
        # if the model wraps in fences, since the gap is the harness wiring, not
        # model output format).
        import contextlib
        import json
        import re

        clean = re.sub(r"```(?:json)?\n?", "", content).replace("```", "").strip()
        model_suggestion: dict[str, Any] | None = None
        with contextlib.suppress(Exception):
            model_suggestion = json.loads(clean)

        print("\n[LIVE-ZAI] === SELF-IMPROVE STRATEGY TEST RESULTS ===")
        print(f"Heuristic findings:   {len(findings)}")
        print(f"Todos generated:      {len(todos)}")
        print(f"Model tokens out:     {tokens_out}")
        if model_suggestion:
            print(f"Model priority_gap:   {model_suggestion.get('priority_gap','?')!r}")
            print(f"Model action:         {model_suggestion.get('suggested_action','?')!r}")
            print(f"Model confidence:     {model_suggestion.get('confidence','?')}")
        else:
            print(f"Model raw output:     {content[:300]!r} (not JSON-parseable)")
        print(
            "\nGAP: SelfImprovementHarness does NOT call ModelGateway internally.\n"
            "     The above model call was made OUTSIDE the harness.\n"
            "     Wiring glm-4.6 into harness.run_gap_analysis() is a post-ship task."
        )
        print("[LIVE-ZAI] === END RESULTS ===\n")

        # Hard assertions: heuristic pipeline shape + model reachability
        assert len(findings) >= 1, "Heuristic must find at least one gap"
        assert all("title" in t for t in todos), "All todos must have a title"
        assert isinstance(content, str) and content.strip(), (
            "glm-4.6 must return non-empty content"
        )
