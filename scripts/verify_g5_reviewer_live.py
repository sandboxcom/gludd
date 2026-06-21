"""Live verification of G5: ReturnReviewer produces a REAL structured decision.

Drives ReturnReviewer.review_return() with a real ModelGateway backed by the
z.ai GLM model. Feeds the reviewer TWO realistic TaskReturns:

  - a clearly-GOOD return (exit 0, tests pass, coverage artifact, clean diff)
  - a clearly-BAD return  (exit 1, tests fail, traceback, no artifacts)

and checks:
  (a) the reviewer makes its OWN model call (tokens > 0 on the gateway)
  (b) the returned TaskDecision is parsed from real model output (not the
      "Model output was not valid JSON" parse-failure fallback)
  (c) the decision varies with input quality (good != bad)

The API key is read at runtime from .zai.key — never printed.
Exit 0 = informative report printed (verdict on last line).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

REPO = Path(__file__).parent.parent


def _load_key() -> str:
    key_path = REPO / ".zai.key"
    if not key_path.exists():
        print(f"FAIL: .zai.key not found at {key_path}", file=sys.stderr)
        sys.exit(1)
    key = key_path.read_text().strip()
    if not key:
        print("FAIL: .zai.key is empty", file=sys.stderr)
        sys.exit(1)
    return key


# Sentinel strings the reviewer uses for its two fallback paths.
FALLBACK_PARSE = "Model output was not valid JSON or did not match TaskDecision schema"
FALLBACK_CALL = "Model call failed"


def _try_parse_raw(raw: str) -> tuple[bool, bool]:
    """Return (bare_json_ok, fenced_present).

    bare_json_ok: does json.loads(raw) succeed on the *raw* content (what the
    reviewer's _parse_model_output actually does — no fence stripping)?
    fenced_present: does the raw content contain a ```json fence?
    """
    import json

    fenced = "```" in raw
    try:
        json.loads(raw)
        return True, fenced
    except Exception:
        return False, fenced


def main() -> None:
    key = _load_key()
    # EnvSecretsManager only resolves env-var names matching its credential
    # allowlist (e.g. *_API_KEY / *_BASE_URL). "ZAI_KEY" does NOT match, so use
    # the allowlisted "ZAI_API_KEY" form for the credential alias.
    os.environ["ZAI_API_KEY"] = key
    os.environ["ZAI_BASE_URL"] = "https://api.z.ai/api/coding/paas/v4"

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.prompts.registry import PromptRegistry
    from general_ludd.review.reviewer import ReturnReviewer
    from general_ludd.schemas.task_return import TaskReturn
    from general_ludd.secrets.env import EnvSecretsManager

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    if not registry.is_installed("openai"):
        print("FAIL: langchain_openai is not installed (make sync)")
        sys.exit(1)

    profile = ModelProfile(
        model_profile_id="default",
        provider="openai",
        model_name="glm-4.6",
        credential_alias="ZAI_API_KEY",
        api_base_alias="ZAI_BASE_URL",
        enabled=True,
        run_budget_usd=10.0,
    )
    secrets = EnvSecretsManager()
    gateway = ModelGateway(
        profiles=[profile], provider_registry=registry, secrets_manager=secrets
    )

    prompt_registry = PromptRegistry(template_dir=str(REPO / "templates" / "prompts"))
    prompt_registry.refresh()

    # ── Capture raw model output by wrapping the reviewer's _call_model ───────
    captured: dict[str, str | None] = {"good": None, "bad": None}
    usage_seen: dict[str, dict] = {"good": {}, "bad": {}}

    reviewer = ReturnReviewer(
        gateway=gateway,
        prompt_registry=prompt_registry,
        model_profile_id="default",
    )

    # Wrap gateway.call_model to capture token usage per case.
    orig_call = gateway.call_model
    current_case = {"name": "good"}

    def wrapped_call(profile_id, messages, **kwargs):  # type: ignore[no-untyped-def]
        resp = orig_call(profile_id, messages, **kwargs)
        captured[current_case["name"]] = resp.content
        usage_seen[current_case["name"]] = dict(resp.usage_metadata or {})
        return resp

    gateway.call_model = wrapped_call  # type: ignore[method-assign]

    # ── GOOD return ──────────────────────────────────────────────────────────
    good_return = TaskReturn(
        return_id="ret-good-001",
        todo_id="todo-add-clamp",
        job_id="job-good-001",
        playbook="implementation",
        queue="default",
        work_type="code",
        exit_code=0,
        result_summary=(
            "Implemented clamp(value, lo, hi) in src/util/math.py. Added 6 unit "
            "tests in tests/test_math.py covering below-range, in-range, "
            "above-range, equal-bounds, and inverted-bounds. All tests pass; "
            "coverage for math.py is 100%."
        ),
        artifacts=["artifacts/ret-good-001/test_results.txt", "artifacts/ret-good-001/diff.patch"],
        test_results_ref="artifacts/ret-good-001/test_results.txt",
        diff_ref="artifacts/ret-good-001/diff.patch",
    )
    good_candidates = [
        {"todo_id": "todo-add-clamp", "title": "Add clamp() helper with tests", "status": "in_progress"}
    ]
    good_artifacts = [
        "test_results.txt: 6 passed in 0.04s; coverage src/util/math.py 100%",
        "diff.patch: +def clamp(value, lo, hi): return max(lo, min(value, hi))",
    ]

    # ── BAD return ───────────────────────────────────────────────────────────
    bad_return = TaskReturn(
        return_id="ret-bad-001",
        todo_id="todo-add-clamp",
        job_id="job-bad-001",
        playbook="implementation",
        queue="default",
        work_type="code",
        exit_code=1,
        result_summary=(
            "Attempted to implement clamp() but the worker crashed. pytest "
            "reported 4 failed, 2 errors. No coverage produced. "
            "TypeError: '>' not supported between instances of 'NoneType' and 'int'."
        ),
        artifacts=[],
    )
    bad_candidates = good_candidates
    bad_artifacts: list[str] = []

    print("=== CASE: GOOD return -> reviewer.review_return() ===")
    current_case["name"] = "good"
    good_decision = reviewer.review_return(good_return, good_candidates, good_artifacts)
    print(f"  decision={good_decision.decision!r} confidence={good_decision.confidence}")
    print(f"  audit_notes={good_decision.audit_notes}")
    print(f"  usage={usage_seen['good']}")

    print("\n=== CASE: BAD return -> reviewer.review_return() ===")
    current_case["name"] = "bad"
    bad_decision = reviewer.review_return(bad_return, bad_candidates, bad_artifacts)
    print(f"  decision={bad_decision.decision!r} confidence={bad_decision.confidence}")
    print(f"  audit_notes={bad_decision.audit_notes}")
    print(f"  usage={usage_seen['bad']}")

    # ── Analysis ─────────────────────────────────────────────────────────────
    def tok(u: dict) -> tuple[int, int]:
        i = u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
        o = u.get("output_tokens", u.get("completion_tokens", 0)) or 0
        return i, o

    gi, go = tok(usage_seen["good"])
    bi, bo = tok(usage_seen["bad"])
    made_call = (gi + go > 0) and (bi + bo > 0)

    def is_parse_fallback(d) -> bool:  # type: ignore[no-untyped-def]
        return any(FALLBACK_PARSE in n for n in d.audit_notes)

    def is_call_fallback(d) -> bool:  # type: ignore[no-untyped-def]
        return any(FALLBACK_CALL in n for n in d.audit_notes)

    good_fallback = is_parse_fallback(good_decision)
    bad_fallback = is_parse_fallback(bad_decision)
    call_fail = is_call_fallback(good_decision) or is_call_fallback(bad_decision)

    g_json_ok, g_fenced = _try_parse_raw(captured["good"] or "")
    b_json_ok, b_fenced = _try_parse_raw(captured["bad"] or "")

    varies = good_decision.decision != bad_decision.decision

    print("\n=== ANALYSIS ===")
    print(f"  tokens GOOD: input={gi} output={go}")
    print(f"  tokens BAD : input={bi} output={bo}")
    print(f"  reviewer made its own model call (tokens>0 both): {made_call}")
    print(f"  GOOD raw json.loads ok? {g_json_ok}  (fence present? {g_fenced})")
    print(f"  BAD  raw json.loads ok? {b_json_ok}  (fence present? {b_fenced})")
    print(f"  GOOD hit parse-fallback? {good_fallback}")
    print(f"  BAD  hit parse-fallback? {bad_fallback}")
    print(f"  any call-fallback (model error)? {call_fail}")
    print(f"  decisions: good={good_decision.decision!r} bad={bad_decision.decision!r} -> varies? {varies}")

    raw_g = (captured["good"] or "")[:300].replace("\n", "\\n")
    raw_b = (captured["bad"] or "")[:300].replace("\n", "\\n")
    print(f"\n  GOOD raw content (first 300): {raw_g!r}")
    print(f"  BAD  raw content (first 300): {raw_b!r}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("\n=== VERDICT ===")
    if call_fail:
        print("G5 ReturnReviewer (live glm-4.6): ALWAYS-FALLBACK: model call itself failed (not a review result)")
        return
    if good_fallback and bad_fallback:
        # Both hit parse fallback -> the parser can't handle normal output.
        if g_fenced or b_fenced:
            print(
                "G5 ReturnReviewer (live glm-4.6): BRITTLE-PARSE: model returned its "
                "decision but wrapped in markdown/prose; _parse_model_output does a bare "
                "json.loads with no fence stripping, so both real reviews fell back to 'failed'"
            )
        else:
            print(
                "G5 ReturnReviewer (live glm-4.6): ALWAYS-FALLBACK: both reviews hit the "
                "parse fallback (no fence detected — output not JSON at all)"
            )
        return
    if not made_call:
        print("G5 ReturnReviewer (live glm-4.6): ALWAYS-FALLBACK: no token usage observed")
        return
    if varies and not good_fallback and not bad_fallback:
        print(
            f"G5 ReturnReviewer (live glm-4.6): REAL-DECISIONS: reviewer made its own "
            f"glm-4.6 call and returned genuine parsed TaskDecisions that vary with input "
            f"(good={good_decision.decision!r}, bad={bad_decision.decision!r})"
        )
        return
    # One side parsed, one fell back, or both parsed but identical decision.
    print(
        f"G5 ReturnReviewer (live glm-4.6): BRITTLE-PARSE: partial — made real call but "
        f"decision did not cleanly vary / one side fell back "
        f"(good={good_decision.decision!r} fallback={good_fallback}, "
        f"bad={bad_decision.decision!r} fallback={bad_fallback})"
    )


if __name__ == "__main__":
    main()
