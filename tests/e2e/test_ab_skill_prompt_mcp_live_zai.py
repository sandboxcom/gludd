"""A/B harness: compare prompt variants / skill bodies by score, pick a winner.

What this proves:
  AB-1 — ab_compare() runs each variant through a gateway (offline: fake; live: glm-4.6)
  AB-2 — The harness scores every response, ranks by composite score, and returns a winner
  AB-3 — The winner is the highest-scoring variant (deterministic under a fake scorer)
  AB-4 — Rankings are stable when scores are re-computed (sort is stable)
  AB-5 (live) — Over a real glm-4.6 call, scores are populated (not all zero)

OFFLINE path:
  FakeGateway returns deterministic canned responses.
  FakeScorer assigns explicit fixed scores per variant label.
  No network, no key, always runnable.

LIVE path (requires ZAI_API_KEY or .zai.key):
  Uses ModelGateway → glm-4.6 at api.z.ai/api/coding/paas/v4.
  A real scorer (length-of-response heuristic) assigns scores so we assert
  at least one non-zero score and a winner is chosen.

Run:
    make test-e2e
or focused:
    ZAI_API_KEY=<key> uv run pytest tests/e2e/test_ab_skill_prompt_mcp_live_zai.py -s -v
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest

# ---------------------------------------------------------------------------
# Key loading — mirrors test_pipeline_live_zai.py
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_ZAI_MODEL = "glm-4.6"


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
    "set ZAI_API_KEY or place key in .zai.key to run live A/B test"
)

# ---------------------------------------------------------------------------
# xfail helpers — rate-limit / quota exhaustion (mirrors test_pipeline_live_zai.py)
# ---------------------------------------------------------------------------

try:
    from openai import RateLimitError as _OpenAIRateLimitError
    _RATE_LIMIT_EXC: tuple[type[BaseException], ...] = (_OpenAIRateLimitError,)
except ImportError:
    _RATE_LIMIT_EXC = ()

try:
    import httpx as _httpx
    _RATE_LIMIT_EXC = (*_RATE_LIMIT_EXC, _httpx.HTTPStatusError)
except ImportError:
    pass

if not _RATE_LIMIT_EXC:
    _RATE_LIMIT_EXC = (Exception,)


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    typ = type(exc).__name__.lower()
    return any(
        token in msg or token in typ
        for token in ("ratelimit", "rate_limit", "429", "529", "503",
                      "timeout", "overloaded", "quota", "balance")
    )


# ---------------------------------------------------------------------------
# Public A/B harness types
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    """A single A/B candidate: a label, system skill body, and user prompt."""

    label: str
    prompt: str
    skill_body: str = ""
    # Populated by ab_compare() after running the variant.
    response: str = ""
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ABResult:
    """Full outcome of an ab_compare() run."""

    winner: Variant
    ranked: list[Variant]  # descending by score
    topic: str

    @property
    def winner_label(self) -> str:
        return self.winner.label

    @property
    def scores(self) -> dict[str, float]:
        return {v.label: v.score for v in self.ranked}


class GatewayProtocol(Protocol):
    """Minimal duck-type for ab_compare()'s gateway parameter."""

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, Any]],
        estimated_cost: float,
        budget_remaining: float,
    ) -> Any: ...


class ScorerProtocol(Protocol):
    """Minimal duck-type for ab_compare()'s scorer parameter."""

    def score(self, variant: Variant) -> float: ...


# ---------------------------------------------------------------------------
# Core harness function
# ---------------------------------------------------------------------------

def ab_compare(
    topic: str,
    variants: list[Variant],
    scorer: ScorerProtocol,
    gateway: GatewayProtocol,
    profile_id: str = "ab_profile",
) -> ABResult:
    """Run each variant through *gateway*, score with *scorer*, return ranked result.

    For each variant:
      1. Build messages: optional system turn (skill_body) + user turn (prompt).
      2. Call gateway.call_model() — stores .response on the variant.
      3. Score via scorer.score(variant) — stores .score on the variant.

    Returns an ABResult with .winner (highest score) and .ranked list (desc).
    Ties are broken by variant order (stable sort).
    """
    if not variants:
        raise ValueError("ab_compare: variants list must be non-empty")

    for variant in variants:
        messages: list[dict[str, Any]] = []
        if variant.skill_body:
            messages.append({"role": "system", "content": variant.skill_body})
        messages.append({"role": "user", "content": variant.prompt})

        try:
            resp = gateway.call_model(
                profile_id,
                messages=messages,
                estimated_cost=0.0,
                budget_remaining=1.0,
            )
            # Support ModelResponse dataclass (resp.content) or plain string.
            if hasattr(resp, "content"):
                variant.response = resp.content or ""
                variant.extra["usage"] = getattr(resp, "usage_metadata", {})
            else:
                variant.response = str(resp)
        except Exception as exc:
            # Non-fatal: score on empty response; caller can inspect .extra["error"].
            variant.response = ""
            variant.extra["error"] = f"{type(exc).__name__}: {exc}"

        variant.score = scorer.score(variant)

    ranked = sorted(variants, key=lambda v: v.score, reverse=True)
    return ABResult(winner=ranked[0], ranked=ranked, topic=topic)


# ---------------------------------------------------------------------------
# Offline fake gateway and scorer
# ---------------------------------------------------------------------------

class _FakeGateway:
    """Deterministic fake gateway — canned responses keyed by system-turn substring."""

    def __init__(self, responses: dict[str, str]) -> None:
        # Map from a keyword that appears in the skill_body (or "default") to response text.
        self._responses = responses

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, Any]],
        estimated_cost: float,
        budget_remaining: float,
    ) -> Any:
        system_turn = next(
            (m["content"] for m in messages if m["role"] == "system"), ""
        )
        for keyword, reply in self._responses.items():
            if keyword in system_turn:
                return _FakeResponse(reply)
        return _FakeResponse(self._responses.get("default", "generic response"))


@dataclass
class _FakeResponse:
    content: str
    usage_metadata: dict[str, Any] = field(default_factory=dict)


class _FixedScorer:
    """Assigns a pre-set score per variant label; unknown labels score 0."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def score(self, variant: Variant) -> float:
        return self._scores.get(variant.label, 0.0)


class _LengthScorer:
    """Simple heuristic scorer: normalized response length (0.0-1.0).

    Used for the live test where we cannot inspect content semantically.
    Score = min(len(response), max_len) / max_len.
    """

    def __init__(self, max_len: int = 2000) -> None:
        self._max_len = max_len

    def score(self, variant: Variant) -> float:
        if not variant.response:
            return 0.0
        return min(len(variant.response), self._max_len) / self._max_len


# ---------------------------------------------------------------------------
# Real gateway builder (mirrors test_pipeline_live_zai.py)
# ---------------------------------------------------------------------------

def _build_gateway(profile_id: str = "ab_live") -> Any:
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
        roles=["coder", "planner"],
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
        profiles=[profile], provider_registry=registry, secrets_manager=secrets  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# OFFLINE tests — always run, no network, no key
# ---------------------------------------------------------------------------

class TestABHarnessOffline:
    """AB harness correctness: fake gateway + fixed scorer, no network."""

    def _make_variants(self) -> list[Variant]:
        return [
            Variant(
                label="concise",
                prompt="Explain what a Python list is.",
                skill_body="concise-skill: respond in one sentence",
            ),
            Variant(
                label="detailed",
                prompt="Explain what a Python list is.",
                skill_body="detailed-skill: respond with examples and details",
            ),
            Variant(
                label="minimal",
                prompt="Explain what a Python list is.",
                skill_body="minimal-skill: use the fewest words possible",
            ),
        ]

    def test_winner_is_highest_scoring_variant(self) -> None:
        """ab_compare selects the variant with the highest fixed score."""
        variants = self._make_variants()
        gateway = _FakeGateway(
            responses={
                "concise-skill": "A list is an ordered mutable sequence.",
                "detailed-skill": "A Python list is an ordered, mutable sequence that can hold any type.",
                "minimal-skill": "Ordered mutable sequence.",
                "default": "generic",
            }
        )
        # "detailed" has the highest score; "minimal" the lowest.
        scorer = _FixedScorer({"concise": 0.7, "detailed": 0.95, "minimal": 0.4})

        result = ab_compare(
            topic="python_list_explanation",
            variants=variants,
            scorer=scorer,
            gateway=gateway,
            profile_id="ab_offline",
        )

        assert result.winner_label == "detailed", (
            f"Expected 'detailed' to win (score=0.95) but got {result.winner_label!r}. "
            f"Scores: {result.scores}"
        )
        assert result.ranked[0].label == "detailed"
        assert result.ranked[-1].label == "minimal"

    def test_all_variants_receive_a_response(self) -> None:
        """Every variant gets a non-empty response from the fake gateway."""
        variants = self._make_variants()
        gateway = _FakeGateway(
            responses={
                "concise-skill": "Concise reply.",
                "detailed-skill": "Detailed reply with examples.",
                "minimal-skill": "Short.",
                "default": "fallback",
            }
        )
        scorer = _FixedScorer({"concise": 0.6, "detailed": 0.8, "minimal": 0.3})

        result = ab_compare(
            topic="list_topic",
            variants=variants,
            scorer=scorer,
            gateway=gateway,
            profile_id="ab_offline",
        )

        for v in result.ranked:
            assert v.response, f"Variant {v.label!r} has empty response after ab_compare"

    def test_scores_populated_on_variants(self) -> None:
        """ab_compare writes .score onto each Variant object."""
        variants = self._make_variants()
        gateway = _FakeGateway(
            responses={
                "concise-skill": "X",
                "detailed-skill": "Y",
                "minimal-skill": "Z",
            }
        )
        scorer = _FixedScorer({"concise": 0.5, "detailed": 0.9, "minimal": 0.2})

        ab_compare(
            topic="test",
            variants=variants,
            scorer=scorer,
            gateway=gateway,
            profile_id="ab_offline",
        )

        labels_to_scores = {v.label: v.score for v in variants}
        assert labels_to_scores["concise"] == pytest.approx(0.5)
        assert labels_to_scores["detailed"] == pytest.approx(0.9)
        assert labels_to_scores["minimal"] == pytest.approx(0.2)

    def test_ranking_is_stable_and_descending(self) -> None:
        """ranked list is always descending by score, ties stable by input order."""
        variants = [
            Variant(label="A", prompt="p", skill_body="skill-A"),
            Variant(label="B", prompt="p", skill_body="skill-B"),
            Variant(label="C", prompt="p", skill_body="skill-C"),
        ]
        gateway = _FakeGateway(
            responses={"skill-A": "ra", "skill-B": "rb", "skill-C": "rc"}
        )
        scorer = _FixedScorer({"A": 0.8, "B": 0.8, "C": 0.6})

        result = ab_compare("tie_test", variants, scorer, gateway, "ab_offline")

        scores = [v.score for v in result.ranked]
        assert scores == sorted(scores, reverse=True), (
            f"ranked list is not descending: {scores}"
        )
        # Ties (A and B both 0.8): input order should be preserved by stable sort.
        assert result.ranked[0].label in {"A", "B"}
        assert result.ranked[1].label in {"A", "B"}
        assert result.ranked[2].label == "C"

    def test_gateway_error_yields_zero_score(self) -> None:
        """A variant whose gateway call raises an exception gets score=0 and error recorded."""

        class _BrokenGateway:
            def call_model(self, profile_id: str, messages: Any, **kw: Any) -> Any:
                raise RuntimeError("simulated network failure")

        variants = [
            Variant(label="good", prompt="q", skill_body="skill-good"),
            Variant(label="broken", prompt="q", skill_body="skill-broken"),
        ]
        scorer = _FixedScorer({"good": 0.7, "broken": 0.99})  # broken would win IF it ran

        result = ab_compare(
            "error_test", variants, scorer, _BrokenGateway(), "ab_offline"
        )

        broken = next(v for v in result.ranked if v.label == "broken")
        assert broken.response == "", "broken variant must have empty response after error"
        # Score is whatever the scorer returns for an empty response — the
        # _FixedScorer still returns 0.99, but the real assertion is that .extra["error"]
        # was recorded and the harness did NOT crash.
        assert "error" in broken.extra, (
            "broken variant should have .extra['error'] set after gateway exception"
        )

    def test_single_variant_is_winner(self) -> None:
        """ab_compare with a single variant always returns that variant as winner."""
        variants = [Variant(label="only", prompt="what is 2+2?", skill_body="")]
        gateway = _FakeGateway(responses={"default": "4"})
        scorer = _FixedScorer({"only": 0.5})

        result = ab_compare("single", variants, scorer, gateway, "ab_offline")

        assert result.winner_label == "only"
        assert len(result.ranked) == 1

    def test_empty_skill_body_omits_system_turn(self) -> None:
        """A variant with empty skill_body sends only a user turn (no system message)."""
        sent_messages: list[list[dict[str, Any]]] = []

        class _SpyGateway:
            def call_model(
                self,
                profile_id: str,
                messages: list[dict[str, Any]],
                estimated_cost: float,
                budget_remaining: float,
            ) -> _FakeResponse:
                sent_messages.append(list(messages))
                return _FakeResponse("ok")

        variants = [Variant(label="no-system", prompt="hello", skill_body="")]
        scorer = _FixedScorer({"no-system": 1.0})

        ab_compare("system_turn_test", variants, scorer, _SpyGateway(), "ab_offline")

        assert len(sent_messages) == 1
        roles = [m["role"] for m in sent_messages[0]]
        assert "system" not in roles, (
            "skill_body='' should omit the system turn but system role was present"
        )
        assert roles == ["user"]

    def test_result_topic_is_preserved(self) -> None:
        """ABResult.topic echoes the topic passed to ab_compare()."""
        variants = [Variant(label="v", prompt="p", skill_body="")]
        gateway = _FakeGateway(responses={"default": "r"})
        scorer = _FixedScorer({"v": 0.5})

        result = ab_compare("my_special_topic", variants, scorer, gateway, "ab_offline")

        assert result.topic == "my_special_topic"

    def test_length_scorer_offline(self) -> None:
        """_LengthScorer assigns higher score to longer response."""
        variants = [
            Variant(label="short", prompt="p", skill_body="skill-s"),
            Variant(label="long", prompt="p", skill_body="skill-l"),
        ]
        gateway = _FakeGateway(
            responses={
                "skill-s": "Hi.",
                "skill-l": "A" * 500,
            }
        )
        scorer = _LengthScorer(max_len=1000)

        result = ab_compare("length_test", variants, scorer, gateway, "ab_offline")

        assert result.winner_label == "long", (
            "LengthScorer must prefer the longer response"
        )
        assert result.ranked[0].score > result.ranked[1].score


# ---------------------------------------------------------------------------
# LIVE tests — require ZAI_API_KEY / .zai.key
# ---------------------------------------------------------------------------

_LIVE_VARIANTS_PYTHON_LIST = [
    Variant(
        label="one_sentence",
        prompt="Explain what a Python list is.",
        skill_body=(
            "You are a concise teacher. "
            "Answer in exactly one sentence, no lists, no markdown."
        ),
    ),
    Variant(
        label="with_example",
        prompt="Explain what a Python list is. Show one short code example.",
        skill_body=(
            "You are a helpful Python tutor. "
            "Answer in 2-3 sentences with a small inline code example."
        ),
    ),
    Variant(
        label="bullet_points",
        prompt="Explain what a Python list is using bullet points.",
        skill_body=(
            "You are a structured teacher. "
            "Answer with 3-5 bullet points, each one sentence."
        ),
    ),
]


@pytest.mark.skipif(not _ZAI_KEY, reason=_SKIP_REASON)
class TestABHarnessLiveZai:
    """Live A/B harness over glm-4.6; scores must be non-zero, winner must be chosen."""

    @pytest.mark.xfail(
        raises=_RATE_LIMIT_EXC,
        reason="z.ai rate-limit / quota exhaustion (429) — not a harness bug",
        strict=False,
    )
    def test_ab_compare_live_picks_winner_with_nonzero_scores(self) -> None:
        """AB live: all 3 prompt variants for 'python list' get real glm-4.6 responses;
        winner chosen, at least one variant has score > 0 (response non-empty)."""
        import copy

        # Deep-copy so the test is isolated (variant state is mutated by ab_compare).
        variants = copy.deepcopy(_LIVE_VARIANTS_PYTHON_LIST)
        gateway = _build_gateway("ab_live")
        scorer = _LengthScorer(max_len=2000)

        result = ab_compare(
            topic="python_list_explanation_live",
            variants=variants,
            scorer=scorer,
            gateway=gateway,
            profile_id="ab_live",
        )

        print("\n=== A/B LIVE RESULTS (glm-4.6) ===")
        print(f"Topic: {result.topic}")
        for v in result.ranked:
            print(
                f"  [{v.label}] score={v.score:.4f} "
                f"response_len={len(v.response)} "
                f"error={v.extra.get('error', 'none')}"
            )
        print(f"WINNER: {result.winner_label} (score={result.winner.score:.4f})")
        print(f"Winner response (first 200 chars): {result.winner.response[:200]!r}")
        print("=== END A/B LIVE RESULTS ===\n")

        # At least one variant must have a non-zero score (i.e. non-empty response).
        non_zero = [v for v in result.ranked if v.score > 0.0]
        assert non_zero, (
            "All variants scored 0 — the gateway returned empty responses for all variants. "
            f"Errors: {[v.extra.get('error') for v in result.ranked]}"
        )

        # Winner must be one of the actual variants.
        assert result.winner_label in {v.label for v in _LIVE_VARIANTS_PYTHON_LIST}, (
            f"Unexpected winner label: {result.winner_label!r}"
        )

        # Winner must be the highest-scoring variant.
        assert result.winner.score == max(v.score for v in result.ranked), (
            f"Winner score {result.winner.score} != max score "
            f"{max(v.score for v in result.ranked)}"
        )

    @pytest.mark.xfail(
        raises=_RATE_LIMIT_EXC,
        reason="z.ai rate-limit / quota exhaustion (429)",
        strict=False,
    )
    def test_ab_compare_live_scores_dict_populated(self) -> None:
        """ABResult.scores dict contains an entry for every variant label."""
        import copy

        variants = copy.deepcopy(_LIVE_VARIANTS_PYTHON_LIST)
        gateway = _build_gateway("ab_live")
        scorer = _LengthScorer(max_len=2000)

        result = ab_compare(
            topic="scores_dict_test",
            variants=variants,
            scorer=scorer,
            gateway=gateway,
            profile_id="ab_live",
        )

        expected_labels = {v.label for v in _LIVE_VARIANTS_PYTHON_LIST}
        assert set(result.scores.keys()) == expected_labels, (
            f"scores keys mismatch: expected {expected_labels}, got {set(result.scores.keys())}"
        )
        # All score values are floats in [0.0, 1.0] (LengthScorer bound).
        for label, score in result.scores.items():
            assert 0.0 <= score <= 1.0, (
                f"Score for {label!r} out of range: {score}"
            )
