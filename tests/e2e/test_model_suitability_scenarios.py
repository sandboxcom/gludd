"""End-to-end model suitability evaluation: does this model actually do real work?

Evaluates whether a model's publicly-advertised use-case (code-gen, extraction,
summarization) translates into measurable, correct outputs — not just non-empty
tokens.

Structure
---------
* ``evaluate_suitability(model_profile, scenarios)`` — runs each scenario through
  the gateway, scores the output with a concrete checker, aggregates into a
  SuitabilityVerdict (PASS / FAIL).

* Three scenario types with deterministic checkers:
    - ``code_gen``        — output must contain a callable Python function that
                           passes a tiny in-process test.
    - ``extraction``      — output must extract the right structured fields from a
                           passage (JSON or delimited text).
    - ``summarization``   — output must include key terms from the source document
                           and be shorter than the original.

* OFFLINE test (no key, no network): a fake gateway returns canned good/bad
  outputs and the evaluator must discriminate them (PASS vs FAIL).

* LIVE test (requires ZAI_API_KEY): one code-gen scenario against glm-4.6 to
  prove the evaluator runs end-to-end with a real model call.

Run:
    ZAI_API_KEY=$(cat .zai.key) make test-specific TESTFILE='tests/e2e/test_model_suitability_scenarios.py'
or just:
    make test-unit TESTFILE='tests/e2e/test_model_suitability_scenarios.py'
(offline tests run without a key)
"""

from __future__ import annotations

import ast
import json
import os
import textwrap
from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Key / endpoint configuration (mirrors test_pipeline_live_zai.py)
# ---------------------------------------------------------------------------

_ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_ZAI_MODEL = "glm-4.6"
_SKIP_REASON = (
    "ZAI_API_KEY not set — set it to run live model-suitability test. "
    "Example: ZAI_API_KEY=<key> make test-specific TESTFILE='tests/e2e/test_model_suitability_scenarios.py'"
)


def _zai_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# Rate-limit helpers (mirrors existing live tests)
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
        t in msg or t in typ
        for t in ("ratelimit", "rate_limit", "429", "529", "503",
                  "timeout", "overloaded", "quota", "balance")
    )


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    """A single evaluation scenario with a prompt and a deterministic checker."""

    scenario_id: str
    scenario_type: str  # "code_gen" | "extraction" | "summarization"
    prompt: str
    checker_kwargs: dict[str, Any] = field(default_factory=dict)

    def check(self, output: str) -> tuple[bool, str]:
        """Return (passed, reason). Delegates to the per-type checker."""
        if self.scenario_type == "code_gen":
            return _check_code_gen(output, **self.checker_kwargs)
        if self.scenario_type == "extraction":
            return _check_extraction(output, **self.checker_kwargs)
        if self.scenario_type == "summarization":
            return _check_summarization(output, **self.checker_kwargs)
        return False, f"Unknown scenario_type: {self.scenario_type!r}"


# ---------------------------------------------------------------------------
# Deterministic checkers
# ---------------------------------------------------------------------------

def _check_code_gen(
    output: str,
    *,
    func_name: str,
    test_cases: list[tuple[tuple[Any, ...], Any]],
) -> tuple[bool, str]:
    """Verify code-gen output: function must be syntactically valid and pass test cases.

    Strips markdown fences, locates the function definition, compiles it, and
    runs each (args, expected_return) pair in a sandboxed exec namespace.
    """
    # Strip markdown code fences
    clean = output
    for fence in ("```python", "```py", "```"):
        clean = clean.replace(fence, "")

    # Find the function definition block
    lines = clean.splitlines()
    start_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip().startswith(f"def {func_name}(") or line.strip().startswith(f"def {func_name} ("):
            start_idx = i
            break

    if start_idx is None:
        return False, f"No 'def {func_name}(' found in output"

    # Grab from start to the next top-level def / class or end of block
    block_lines = []
    for line in lines[start_idx:]:
        if block_lines and line and not line[0].isspace() and line.strip() and not line.strip().startswith("#"):
            # Hit a new top-level token — stop
            break
        block_lines.append(line)

    func_src = "\n".join(block_lines).rstrip()
    if not func_src:
        return False, f"Extracted empty source for {func_name}"

    # Syntax check via ast
    try:
        ast.parse(func_src)
    except SyntaxError as exc:
        return False, f"SyntaxError in extracted function: {exc}"

    # Compile and exec in an isolated namespace
    ns: dict[str, Any] = {}
    try:
        exec(compile(func_src, "<suitability_check>", "exec"), ns)
    except Exception as exc:
        return False, f"exec failed: {exc}"

    if func_name not in ns:
        return False, f"Function {func_name!r} not in exec namespace after exec"

    fn = ns[func_name]
    for args, expected in test_cases:
        try:
            result = fn(*args)
        except Exception as exc:
            return False, f"{func_name}{args} raised {type(exc).__name__}: {exc}"
        if result != expected:
            return False, (
                f"{func_name}{args} returned {result!r}, expected {expected!r}"
            )

    return True, f"{func_name} passed all {len(test_cases)} test cases"


def _check_extraction(
    output: str,
    *,
    required_fields: dict[str, Any],
) -> tuple[bool, str]:
    """Verify extraction output: required key→value pairs must be present.

    Accepts JSON objects (with or without markdown fences) or colon-separated
    ``key: value`` lines.  Values are matched case-insensitively after stripping.
    """
    # Try JSON first
    clean = output
    for fence in ("```json", "```"):
        clean = clean.replace(fence, "")
    clean = clean.strip()

    extracted: dict[str, str] = {}
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            extracted = {str(k).lower(): str(v).strip() for k, v in parsed.items()}
    except (json.JSONDecodeError, ValueError):
        # Fall back to key: value scanning
        for line in output.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                extracted[k.strip().lower()] = v.strip()

    missing = []
    wrong = []
    for field_name, expected_val in required_fields.items():
        key = field_name.lower()
        if key not in extracted:
            missing.append(field_name)
            continue
        actual = extracted[key].lower()
        exp_str = str(expected_val).lower()
        if exp_str not in actual:
            wrong.append(f"{field_name}: expected {exp_str!r} in {actual!r}")

    if missing:
        return False, f"Missing fields: {missing}"
    if wrong:
        return False, f"Wrong values: {wrong}"
    return True, f"All {len(required_fields)} required fields extracted correctly"


def _check_summarization(
    output: str,
    *,
    source_text: str,
    required_terms: list[str],
    max_ratio: float = 0.8,
) -> tuple[bool, str]:
    """Verify summarization: output is shorter than source and mentions key terms.

    ``max_ratio`` is the max allowed (len(output) / len(source_text)) ratio.
    Terms are matched case-insensitively.
    """
    if not output.strip():
        return False, "Output is empty"

    ratio = len(output) / max(len(source_text), 1)
    if ratio > max_ratio:
        return False, (
            f"Output is {ratio:.0%} of source length (max allowed {max_ratio:.0%})"
        )

    output_lower = output.lower()
    missing_terms = [t for t in required_terms if t.lower() not in output_lower]
    if missing_terms:
        return False, f"Missing required terms in summary: {missing_terms}"

    return True, (
        f"Summary is {ratio:.0%} of source; all {len(required_terms)} key terms present"
    )


# ---------------------------------------------------------------------------
# Verdict dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_type: str
    passed: bool
    reason: str
    output_preview: str = ""  # first 120 chars of model output


@dataclass
class SuitabilityVerdict:
    model_profile_id: str
    passed: bool
    score: float  # fraction of scenarios that passed
    scenario_results: list[ScenarioResult] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------

def evaluate_suitability(
    gateway: Any,
    model_profile_id: str,
    scenarios: list[Scenario],
) -> SuitabilityVerdict:
    """Run each scenario through the gateway, score output, return a SuitabilityVerdict.

    ``gateway`` must expose ``call_model(profile_id, messages, estimated_cost, budget_remaining)``
    returning an object with a ``.content`` string attribute (ModelResponse or fake).

    A model is considered PASS if it passes ALL scenarios.  The ``score`` field
    is the fraction of scenarios passed, so partial failures are visible in the
    verdict even when ``passed`` is False.
    """
    results: list[ScenarioResult] = []

    for scenario in scenarios:
        messages = [{"role": "user", "content": scenario.prompt}]
        try:
            response = gateway.call_model(
                model_profile_id,
                messages=messages,
                estimated_cost=0.0,
                budget_remaining=1.0,
            )
            output = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            results.append(ScenarioResult(
                scenario_id=scenario.scenario_id,
                scenario_type=scenario.scenario_type,
                passed=False,
                reason=f"Gateway error: {type(exc).__name__}: {exc}",
            ))
            continue

        passed, reason = scenario.check(output)
        results.append(ScenarioResult(
            scenario_id=scenario.scenario_id,
            scenario_type=scenario.scenario_type,
            passed=passed,
            reason=reason,
            output_preview=output[:120],
        ))

    total = len(results)
    num_passed = sum(1 for r in results if r.passed)
    score = num_passed / total if total else 0.0
    overall_pass = num_passed == total

    failed_ids = [r.scenario_id for r in results if not r.passed]
    summary = (
        f"{num_passed}/{total} scenarios passed"
        + (f"; FAILED: {failed_ids}" if failed_ids else "")
    )

    return SuitabilityVerdict(
        model_profile_id=model_profile_id,
        passed=overall_pass,
        score=score,
        scenario_results=results,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Shared scenario definitions used by both offline and live tests
# ---------------------------------------------------------------------------

_CODE_GEN_SCENARIO = Scenario(
    scenario_id="code_gen_add",
    scenario_type="code_gen",
    prompt=(
        "Write ONLY a Python function named `add` that takes two parameters `a` and `b` "
        "and returns their sum.  No imports, no class, no explanation, no markdown prose.  "
        "Output ONLY the function definition starting with `def add(`."
    ),
    checker_kwargs={
        "func_name": "add",
        "test_cases": [
            ((1, 2), 3),
            ((0, 0), 0),
            ((-3, 3), 0),
            ((10, -4), 6),
        ],
    },
)

_EXTRACTION_SCENARIO = Scenario(
    scenario_id="extraction_invoice",
    scenario_type="extraction",
    prompt=(
        "Extract the following fields from this invoice text and output them as a JSON "
        "object with keys 'vendor', 'amount', 'date'.  "
        "Invoice text: 'Invoice #4421 from Acme Corp dated 2024-03-15.  "
        "Total amount due: $1,250.00.  Payment terms: Net 30.'  "
        "Output ONLY the JSON object."
    ),
    checker_kwargs={
        "required_fields": {
            "vendor": "acme",
            "amount": "1,250",
            "date": "2024",
        },
    },
)

_SUMMARIZATION_SCENARIO = Scenario(
    scenario_id="summarization_ml",
    scenario_type="summarization",
    prompt=(
        "Summarize the following paragraph in 1-2 sentences:\n\n"
        "Machine learning is a subset of artificial intelligence that enables computers "
        "to learn from data and improve their performance on tasks without being explicitly "
        "programmed.  It relies on algorithms that identify patterns in large datasets to "
        "make predictions or decisions.  Common applications include image recognition, "
        "natural language processing, and recommendation systems.  Deep learning, a "
        "powerful subtype of machine learning, uses multi-layered neural networks to "
        "model complex representations."
    ),
    checker_kwargs={
        "source_text": (
            "Machine learning is a subset of artificial intelligence that enables computers "
            "to learn from data and improve their performance on tasks without being explicitly "
            "programmed.  It relies on algorithms that identify patterns in large datasets to "
            "make predictions or decisions.  Common applications include image recognition, "
            "natural language processing, and recommendation systems.  Deep learning, a "
            "powerful subtype of machine learning, uses multi-layered neural networks to "
            "model complex representations."
        ),
        "required_terms": ["machine learning", "data"],
        "max_ratio": 0.7,
    },
)

_ALL_SCENARIOS = [_CODE_GEN_SCENARIO, _EXTRACTION_SCENARIO, _SUMMARIZATION_SCENARIO]


# ---------------------------------------------------------------------------
# Fake gateway for OFFLINE tests
# ---------------------------------------------------------------------------

@dataclass
class _FakeResponse:
    content: str


class _FakeGateway:
    """Deterministic fake gateway: returns pre-canned outputs per scenario call."""

    def __init__(self, canned_outputs: dict[str, str]) -> None:
        # Maps scenario prompt substring → output string
        self._outputs = canned_outputs
        self._call_count = 0

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        estimated_cost: float = 0.0,
        budget_remaining: float = 1.0,
        **kwargs: Any,
    ) -> _FakeResponse:
        self._call_count += 1
        prompt = messages[-1]["content"] if messages else ""
        # Match on scenario_id-flavoured keywords present in the prompt
        for key, output in self._outputs.items():
            if key in prompt:
                return _FakeResponse(content=output)
        return _FakeResponse(content="")


# ---------------------------------------------------------------------------
# GOOD canned outputs (should yield PASS for each checker)
# ---------------------------------------------------------------------------

_GOOD_CODE_OUTPUT = textwrap.dedent("""\
    def add(a, b):
        return a + b
""")

_GOOD_EXTRACTION_OUTPUT = '{"vendor": "Acme Corp", "amount": "1,250.00", "date": "2024-03-15"}'

_GOOD_SUMMARIZATION_OUTPUT = (
    "Machine learning enables computers to learn from data to improve performance. "
    "It includes techniques like deep learning for tasks such as image recognition."
)

# ---------------------------------------------------------------------------
# BAD canned outputs (should yield FAIL for each checker)
# ---------------------------------------------------------------------------

_BAD_CODE_OUTPUT = "Sure! Here is an explanation of how addition works in Python."

_BAD_EXTRACTION_OUTPUT = '{"company": "unknown", "total": "N/A", "issued": "unknown"}'

_BAD_SUMMARIZATION_OUTPUT = (
    "This is a very long text that is almost as long as the original source.  "
    "Machine learning is a subset of artificial intelligence that enables computers "
    "to learn from data and improve their performance on tasks without being explicitly "
    "programmed.  It relies on algorithms that identify patterns in large datasets to "
    "make predictions.  Deep learning is also mentioned here."
)


# ---------------------------------------------------------------------------
# OFFLINE TESTS — no network, no key required
# ---------------------------------------------------------------------------

class TestSuitabilityEvaluatorOffline:
    """Offline: fake gateway with canned outputs proves the evaluator discriminates."""

    # -- Code-gen checker -----------------------------------------------------

    def test_code_gen_good_output_passes(self) -> None:
        passed, reason = _check_code_gen(
            _GOOD_CODE_OUTPUT,
            func_name="add",
            test_cases=[((1, 2), 3), ((0, 0), 0), ((-3, 3), 0)],
        )
        assert passed, f"Expected PASS for good code output, got FAIL: {reason}"

    def test_code_gen_bad_output_fails(self) -> None:
        passed, reason = _check_code_gen(
            _BAD_CODE_OUTPUT,
            func_name="add",
            test_cases=[((1, 2), 3)],
        )
        assert not passed, "Expected FAIL for prose-only output, got PASS"
        assert "def add(" in reason or "No 'def add(" in reason

    def test_code_gen_wrong_return_fails(self) -> None:
        bad_code = "def add(a, b):\n    return a - b\n"  # returns difference, not sum
        passed, reason = _check_code_gen(
            bad_code,
            func_name="add",
            test_cases=[((1, 2), 3)],
        )
        assert not passed, "Expected FAIL for wrong return value"
        assert "returned" in reason

    def test_code_gen_syntax_error_fails(self) -> None:
        bad_code = "def add(a, b)\n    return a + b\n"  # missing colon
        passed, reason = _check_code_gen(
            bad_code,
            func_name="add",
            test_cases=[((1, 2), 3)],
        )
        assert not passed
        assert "SyntaxError" in reason or "No 'def add(" in reason

    # -- Extraction checker ---------------------------------------------------

    def test_extraction_good_output_passes(self) -> None:
        passed, reason = _check_extraction(
            _GOOD_EXTRACTION_OUTPUT,
            required_fields={"vendor": "acme", "amount": "1,250", "date": "2024"},
        )
        assert passed, f"Expected PASS, got FAIL: {reason}"

    def test_extraction_bad_output_fails(self) -> None:
        passed, _reason = _check_extraction(
            _BAD_EXTRACTION_OUTPUT,
            required_fields={"vendor": "acme", "amount": "1,250", "date": "2024"},
        )
        assert not passed, "Expected FAIL for wrong field values"

    def test_extraction_missing_field_fails(self) -> None:
        output = '{"vendor": "Acme Corp", "amount": "1,250"}'  # missing date
        passed, reason = _check_extraction(
            output,
            required_fields={"vendor": "acme", "amount": "1,250", "date": "2024"},
        )
        assert not passed
        assert "date" in reason.lower() or "missing" in reason.lower()

    def test_extraction_colon_format_accepted(self) -> None:
        colon_output = "vendor: Acme Corp\namount: $1,250.00\ndate: 2024-03-15"
        passed, reason = _check_extraction(
            colon_output,
            required_fields={"vendor": "acme", "amount": "1,250", "date": "2024"},
        )
        assert passed, f"Expected PASS for colon-format extraction, got: {reason}"

    # -- Summarization checker ------------------------------------------------

    def test_summarization_good_output_passes(self) -> None:
        source = _SUMMARIZATION_SCENARIO.checker_kwargs["source_text"]
        passed, reason = _check_summarization(
            _GOOD_SUMMARIZATION_OUTPUT,
            source_text=source,
            required_terms=["machine learning", "data"],
            max_ratio=0.7,
        )
        assert passed, f"Expected PASS, got FAIL: {reason}"

    def test_summarization_too_long_fails(self) -> None:
        source = _SUMMARIZATION_SCENARIO.checker_kwargs["source_text"]
        passed, reason = _check_summarization(
            _BAD_SUMMARIZATION_OUTPUT,
            source_text=source,
            required_terms=["machine learning", "data"],
            max_ratio=0.7,
        )
        assert not passed, "Expected FAIL for output that is too long"
        assert "ratio" in reason.lower() or "%" in reason

    def test_summarization_missing_term_fails(self) -> None:
        short_output = "AI systems can be trained automatically."
        source = _SUMMARIZATION_SCENARIO.checker_kwargs["source_text"]
        passed, reason = _check_summarization(
            short_output,
            source_text=source,
            required_terms=["machine learning", "data"],
            max_ratio=0.7,
        )
        assert not passed
        assert "machine learning" in reason.lower() or "missing" in reason.lower()

    # -- evaluate_suitability with fake gateway — ALL GOOD --------------------

    def test_evaluate_suitability_all_good_yields_pass(self) -> None:
        """Fake gateway returns correct outputs → PASS verdict."""
        gateway = _FakeGateway({
            "def add(": _GOOD_CODE_OUTPUT,
            "invoice": _GOOD_EXTRACTION_OUTPUT,
            "Summarize": _GOOD_SUMMARIZATION_OUTPUT,
        })
        verdict = evaluate_suitability(gateway, "fake_good", _ALL_SCENARIOS)
        assert verdict.passed, (
            f"Expected PASS for all-good outputs, got {verdict.summary}. "
            f"Results: {[(r.scenario_id, r.passed, r.reason) for r in verdict.scenario_results]}"
        )
        assert verdict.score == 1.0, f"Expected score=1.0, got {verdict.score}"
        assert all(r.passed for r in verdict.scenario_results), (
            "All individual scenarios should pass"
        )

    # -- evaluate_suitability with fake gateway — ALL BAD ---------------------

    def test_evaluate_suitability_all_bad_yields_fail(self) -> None:
        """Fake gateway returns wrong outputs → FAIL verdict."""
        gateway = _FakeGateway({
            "def add(": _BAD_CODE_OUTPUT,
            "invoice": _BAD_EXTRACTION_OUTPUT,
            "summarize": _BAD_SUMMARIZATION_OUTPUT,
        })
        verdict = evaluate_suitability(gateway, "fake_bad", _ALL_SCENARIOS)
        assert not verdict.passed, (
            f"Expected FAIL for all-bad outputs, got PASS. Summary: {verdict.summary}"
        )
        assert verdict.score < 1.0, f"Expected score < 1.0, got {verdict.score}"
        assert any(not r.passed for r in verdict.scenario_results), (
            "At least one scenario should fail"
        )

    def test_evaluate_suitability_mixed_partial_score(self) -> None:
        """Fake gateway returns good code but bad extraction → score = 1/3 (or 2/3)."""
        # Only code-gen matches; extraction and summarization get no match → empty output
        gateway = _FakeGateway({
            "def add(": _GOOD_CODE_OUTPUT,
            "invoice": _BAD_EXTRACTION_OUTPUT,   # extraction fails
            "summarize": _BAD_SUMMARIZATION_OUTPUT,  # summarization fails (too long)
        })
        verdict = evaluate_suitability(gateway, "fake_mixed", _ALL_SCENARIOS)
        # Exactly 1 of 3 passes
        assert not verdict.passed, "Should be FAIL when not all scenarios pass"
        assert 0.0 < verdict.score < 1.0, f"Score should be partial, got {verdict.score}"
        code_result = next(r for r in verdict.scenario_results if r.scenario_id == "code_gen_add")
        assert code_result.passed, "Code-gen should have passed"

    # -- Verdict fields -------------------------------------------------------

    def test_verdict_contains_model_profile_id(self) -> None:
        gateway = _FakeGateway({
            "def add(": _GOOD_CODE_OUTPUT,
            "invoice": _GOOD_EXTRACTION_OUTPUT,
            "Summarize": _GOOD_SUMMARIZATION_OUTPUT,
        })
        verdict = evaluate_suitability(gateway, "my_test_profile", _ALL_SCENARIOS)
        assert verdict.model_profile_id == "my_test_profile"

    def test_scenario_result_has_output_preview(self) -> None:
        gateway = _FakeGateway({
            "def add(": _GOOD_CODE_OUTPUT,
            "invoice": _GOOD_EXTRACTION_OUTPUT,
            "Summarize": _GOOD_SUMMARIZATION_OUTPUT,
        })
        verdict = evaluate_suitability(gateway, "fake_preview", _ALL_SCENARIOS)
        for result in verdict.scenario_results:
            assert isinstance(result.output_preview, str)
            assert len(result.output_preview) <= 120

    def test_gateway_error_yields_scenario_fail(self) -> None:
        """A gateway that raises an exception → that scenario is marked FAIL."""
        class _ErrorGateway:
            def call_model(self, *a: Any, **kw: Any) -> _FakeResponse:
                raise RuntimeError("network timeout")

        verdict = evaluate_suitability(_ErrorGateway(), "error_profile", [_CODE_GEN_SCENARIO])
        assert not verdict.passed
        assert "Gateway error" in verdict.scenario_results[0].reason


# ---------------------------------------------------------------------------
# LIVE TEST — requires ZAI_API_KEY, calls real glm-4.6
# ---------------------------------------------------------------------------

def _build_live_gateway(profile_id: str = "zai_suitability") -> Any:
    """Construct a real ModelGateway pointing at z.ai / glm-4.6."""
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    api_key = _zai_api_key()
    assert api_key, "ZAI_API_KEY must be set to build the live gateway"

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
        max_output_tokens=4096,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=1.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("ZAI_API_KEY", api_key)
    secrets.set("ZAI_BASE_URL", _ZAI_BASE_URL)
    return ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=secrets,  # type: ignore[arg-type]
    )


@pytest.mark.skipif(not _zai_api_key(), reason=_SKIP_REASON)
class TestModelSuitabilityLive:
    """Live test: evaluate glm-4.6 on a code-gen scenario with real tokens."""

    @pytest.mark.xfail(
        raises=_RATE_LIMIT_EXC,
        reason="z.ai rate-limit / quota exhaustion — not an evaluator bug",
        strict=False,
    )
    def test_live_code_gen_suitability_verdict_computed(self) -> None:
        """Live call to glm-4.6: evaluate_suitability returns a SuitabilityVerdict.

        Assertions:
        - The verdict is a SuitabilityVerdict with model_profile_id set.
        - The score is a float in [0, 1].
        - The scenario_results list has exactly 1 entry (code-gen only).
        - The output_preview is non-empty (proves model DID respond).

        We do NOT assert passed=True because quota may produce a degraded response;
        we assert the evaluator RUNS end-to-end with a real model call.
        """
        gateway = _build_live_gateway("zai_suitability")

        verdict = evaluate_suitability(
            gateway=gateway,
            model_profile_id="zai_suitability",
            scenarios=[_CODE_GEN_SCENARIO],
        )

        # Structural assertions (always valid regardless of model quality)
        assert isinstance(verdict, SuitabilityVerdict), (
            f"Expected SuitabilityVerdict, got {type(verdict)}"
        )
        assert verdict.model_profile_id == "zai_suitability"
        assert 0.0 <= verdict.score <= 1.0, f"Score out of range: {verdict.score}"
        assert len(verdict.scenario_results) == 1, (
            f"Expected 1 scenario result, got {len(verdict.scenario_results)}"
        )

        result = verdict.scenario_results[0]
        assert result.scenario_id == "code_gen_add"
        assert result.scenario_type == "code_gen"
        assert isinstance(result.output_preview, str)
        assert result.output_preview.strip(), (
            "output_preview is empty — model returned no content (possible quota issue)"
        )
        assert isinstance(result.reason, str) and result.reason, (
            "reason field must be non-empty"
        )

        # Print diagnostic output (visible with -s flag)
        print(f"\n=== LIVE SUITABILITY VERDICT ({_ZAI_MODEL}) ===")
        print(f"passed: {verdict.passed}")
        print(f"score:  {verdict.score:.2f}")
        print(f"summary: {verdict.summary}")
        print(f"code_gen result: passed={result.passed}, reason={result.reason!r}")
        print(f"output_preview: {result.output_preview!r}")
        print("=== END SUITABILITY VERDICT ===\n")

    @pytest.mark.xfail(
        raises=_RATE_LIMIT_EXC,
        reason="z.ai rate-limit / quota exhaustion",
        strict=False,
    )
    def test_live_glm46_code_gen_passes_suitability(self) -> None:
        """Live: glm-4.6 should produce a valid add() function — assert PASS verdict.

        This is the canonical 'does the model actually do real work?' assertion.
        Marked xfail on rate-limit only; a wrong-output result is a REAL failure.
        """
        gateway = _build_live_gateway("zai_suitability_strict")

        verdict = evaluate_suitability(
            gateway=gateway,
            model_profile_id="zai_suitability_strict",
            scenarios=[_CODE_GEN_SCENARIO],
        )

        result = verdict.scenario_results[0]
        print(f"\n[LIVE STRICT] passed={verdict.passed} score={verdict.score:.2f}")
        print(f"[LIVE STRICT] reason: {result.reason!r}")
        print(f"[LIVE STRICT] output: {result.output_preview!r}")

        assert verdict.passed, (
            f"glm-4.6 FAILED code-gen suitability check.\n"
            f"Reason: {result.reason}\n"
            f"Output preview: {result.output_preview!r}\n"
            f"This means the model did NOT produce a valid, callable add(a, b) function. "
            f"This is a REAL suitability failure, not a rate-limit."
        )
        assert verdict.score == 1.0, f"Expected perfect score, got {verdict.score}"
