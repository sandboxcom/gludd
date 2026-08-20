"""E2E game-building test harness against local / self-hosted LLM endpoints.

Proves FPX.1 game-dispatch can run against local models (ollama, llama.cpp server,
or any OpenAI-compatible endpoint) with SmallModelTaskPolicy authorization wired
into the dispatch flow — THROUGH the same POST /api/dispatch capability=game_logic
code path used by cloud model tests.

Configuration (all via env vars, no files needed):
    LOCAL_MODEL_BASE_URL   OpenAI-compatible base URL (default: http://localhost:11434/v1)
    LOCAL_MODEL_NAME       Model name (default: qwen2.5:0.5b)
    LOCAL_MODEL_KEY        API key, if needed (default: empty — most local servers need none)
    LOCAL_MODEL_GAME       Target game to test (default: "" = all 12; use "snake" for one)

Run:
    LOCAL_MODEL_BASE_URL=http://localhost:11434/v1 \\
        uv run pytest tests/e2e/test_game_building_local.py -v -s
or:
    make test-e2e-games-local-model

Smoke mode (one fast game):
    LOCAL_MODEL_GAME=snake make test-e2e-games-local-model
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import pytest

from tests.e2e._game_lifecycle import run_lifecycle_checks as lc_checks

# ---------------------------------------------------------------------------
# Shared game definitions and verification from DeepSeek test
# ---------------------------------------------------------------------------
from tests.e2e.test_game_building_deepseek import (
    GAME_DEFINITIONS,
    _extract_python_module,
    _load_generated_module,
    _parse_ast,
    verify_features,
)

# ---------------------------------------------------------------------------
# Local model configuration
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_DEFAULT_MODEL = "qwen2.5:0.5b"
_LOCAL_BASE_URL = os.environ.get("LOCAL_MODEL_BASE_URL", _DEFAULT_BASE_URL)
_LOCAL_MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", _DEFAULT_MODEL)
_LOCAL_MODEL_KEY = os.environ.get("LOCAL_MODEL_KEY", "")
_TARGET_GAME = os.environ.get("LOCAL_MODEL_GAME", "").strip().lower()

_SKIP_REASON = (
    "LOCAL_MODEL_BASE_URL not reachable — "
    "start a local LLM (ollama serve, llama.cpp server) and set LOCAL_MODEL_BASE_URL"
)

_LOCAL_PROFILE_ID = f"local-{_LOCAL_MODEL_NAME.replace('/', '_').replace(':', '_')}"
_MAX_REPAIR_CODE_CHARS = 8_000

_LOCAL_GENERATION_CONSTRAINTS = """
Local-model reliability constraints:
- Return one complete, syntactically valid Python module and no prose.
- Keep the implementation under 120 lines; prefer small methods and direct state.
- Use ordinary quoted strings only; do not use triple-quoted strings.
- Close every string, bracket, and block before finishing the response.
""".strip()


# ---------------------------------------------------------------------------
# Probe — check local endpoint reachability
# ---------------------------------------------------------------------------


def _probe_local_endpoint() -> bool:
    """Check if the local endpoint is reachable."""
    import urllib.error
    import urllib.request

    try:
        url = f"{_LOCAL_BASE_URL}/models" if "/v1" in _LOCAL_BASE_URL else f"{_LOCAL_BASE_URL}/v1/models"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SmallModelTaskPolicy wiring helpers
# ---------------------------------------------------------------------------


def _build_task_spec(game_id: str, task_kind: str = "coding") -> dict[str, Any]:
    """Build a SmallModelTaskSpec-compatible dict for the given game."""
    import hashlib

    from general_ludd.routing_roles.small_model_policy import (
        TaskImpact,
    )
    from general_ludd.schemas.benchmark import TaskRole

    prompt = GAME_DEFINITIONS[game_id]["prompt"]
    input_digest = hashlib.sha256(prompt.encode()).hexdigest()
    return {
        "task_id": f"fpx.1.game.{game_id}",
        "task_kind": task_kind,
        "role": TaskRole.CODER,
        "collection": "gludd.fpx",
        "input_digest": input_digest,
        "impacts": frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
        "acceptance_checks": ("syntax_valid", "import_ok", "run_without_crash"),
    }


def _build_model_identity() -> dict[str, Any]:
    """Build a ModelIdentity-compatible dict for the local model."""

    from general_ludd.routing_roles.small_model_policy import _stable_digest

    profile_id = _LOCAL_PROFILE_ID
    weights_digest = _stable_digest({"model_name": _LOCAL_MODEL_NAME, "endpoint": _LOCAL_BASE_URL})
    runtime_digest = _stable_digest({"runtime": "local", "base_url": _LOCAL_BASE_URL})
    prompt_digest = _stable_digest({"contract": "game_gen_v1"})
    return {
        "model_profile_id": profile_id,
        "model_artifact_digest": weights_digest,
        "runtime_config_digest": runtime_digest,
        "prompt_contract_digest": prompt_digest,
    }


def _build_capability_evidence(model_identity: dict[str, Any], task_spec: dict[str, Any]) -> dict[str, Any]:
    """Build synthetic CapabilityEvidence claiming local eval suite passed."""
    import hashlib

    from general_ludd.routing_roles.small_model_policy import _stable_digest

    acceptance_contract = {
        "acceptance_checks": sorted(task_spec["acceptance_checks"]),
        "collection": task_spec["collection"],
        "role": task_spec["role"].value,
        "task_kind": task_spec["task_kind"],
    }
    acceptance_digest = _stable_digest(acceptance_contract)
    return {
        "model_profile_id": model_identity["model_profile_id"],
        "model_identity_digest": _stable_digest(model_identity),
        "task_kind": task_spec["task_kind"],
        "role": task_spec["role"],
        "collection": task_spec["collection"],
        "suite_id": "fpx.1.game_gen",
        "suite_revision": "v1",
        "acceptance_contract_digest": acceptance_digest,
        "passed_cases": 20,
        "total_cases": 20,
        "collection_ok": True,
        "local_only": True,
        "evidence_digest": hashlib.sha256(b"fpx1_local_evidence").hexdigest(),
    }


def authorize_game_dispatch(game_id: str) -> dict[str, Any]:
    """Wire SmallModelTaskPolicy to authorize FPX.1 game dispatch for ``game_id``.

    Returns a dict with keys: approved (bool), reason (str), max_attempts (int).
    The dispatch is blocked (approved=False) if the local model hasn't proven
    capability for this task kind.
    """
    from general_ludd.routing_roles.small_model_policy import (
        CapabilityEvidence,
        DispatchAction,
        ModelIdentity,
        SmallModelTaskPolicy,
        SmallModelTaskSpec,
    )

    task = SmallModelTaskSpec(**_build_task_spec(game_id))
    identity_data = _build_model_identity()
    identity = ModelIdentity(**identity_data)
    evidence_data = _build_capability_evidence(identity_data, _build_task_spec(game_id))
    evidence = [CapabilityEvidence(**evidence_data)]

    policy = SmallModelTaskPolicy()
    decision = policy.authorize(task, identity, evidence)

    return {
        "approved": decision.action is DispatchAction.LOCAL,
        "reason": decision.reason,
        "max_attempts": decision.max_attempts,
        "task_fingerprint": decision.task_fingerprint if decision.action is DispatchAction.LOCAL else "",
    }


# ---------------------------------------------------------------------------
# Gateway — shared instance for the dispatch handler's model calls
# ---------------------------------------------------------------------------

_GATEWAY_CACHE: dict[str, Any] = {}


def _build_local_gateway() -> Any:
    """Build a ModelGateway pointing at the local endpoint."""
    if "gateway" in _GATEWAY_CACHE:
        return _GATEWAY_CACHE["gateway"]

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    profile = ModelProfile(
        model_profile_id=_LOCAL_PROFILE_ID,
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
        roles=["coder", "enumerator"],
        latency_class="medium",
        quality_class="variable",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("LOCAL_MODEL_BASE", _LOCAL_BASE_URL)
    if _LOCAL_MODEL_KEY:
        secrets.set("LOCAL_MODEL_KEY", _LOCAL_MODEL_KEY)
    gateway = cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
    _GATEWAY_CACHE["gateway"] = gateway
    return gateway


def _call_local_model_direct(prompt: str, temperature: float = 0.0) -> str:
    """Call the local model via ModelGateway and return text content.

    This is the low-level call used by the dispatch collection_handler.
    Not used directly by tests — tests go through POST /api/dispatch.
    """
    gateway = _build_local_gateway()
    profile_id = _LOCAL_PROFILE_ID
    response = gateway.call_model(
        profile_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return str(response.content)


# ---------------------------------------------------------------------------
# Dispatch test harness — POST /api/dispatch capability=game_logic
# ---------------------------------------------------------------------------

_DISPATCH_APP_CACHE: dict[str, Any] = {}


def _dispatch_collection_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    """Handle collection module dispatch for game_build.

    Simulates what ``AnsibleRunnerAdapter.run_playbook(game_build)`` would
    do in production: calls the local model and returns the generated text.
    """
    if name != "general_ludd.agent.game_build":
        return {"failed": True, "msg": f"unknown module: {name}"}

    prompt = str(args.get("prompt", ""))
    if not prompt:
        return {"failed": True, "msg": "missing prompt"}

    try:
        temp_raw = args.get("temperature", 0.0)
        temperature = float(temp_raw) if isinstance(temp_raw, (int, float)) else 0.0
        text = _call_local_model_direct(prompt, temperature=temperature)
        return {"text": text, "transport_used": "local", "failed": False, "changed": True}
    except Exception as exc:
        return {"failed": True, "msg": str(exc)}


def _make_dispatch_test_client() -> Any:
    """Build a FastAPI TestClient with the dispatch router wired for game_logic.

    Registers a CapabilityRegistry with the ``agent`` collection tagged
    ``game_logic``, and a ``collection_handler`` that dispatches
    ``general_ludd.agent.game_build`` calls to the local model.
    """
    if "client" in _DISPATCH_APP_CACHE:
        return _DISPATCH_APP_CACHE["client"]

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta
    from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE
    from general_ludd.routers.dispatch import register

    # Build a CapabilityRegistry with the agent collection tagged game_logic
    reg = CapabilityRegistry()
    reg.add_collection(
        CollectionMeta(
            name="agent",
            namespace="general_ludd",
            version="0.2.0",
            description="Agent roles and tooling",
            tags=frozenset({"game_logic", "agentic", "sdlc"}),
            raw_tags=["game_logic", "agentic", "sdlc"],
        )
    )

    app = FastAPI()
    register(
        app,
        {},
        capability_registry=reg,
        collection_handler=_dispatch_collection_handler,
        role=UNRESTRICTED_ROLE,
    )
    client = TestClient(app, raise_server_exceptions=False)
    _DISPATCH_APP_CACHE["client"] = client
    return client


def _build_game_prompt(
    game_id: str,
    *,
    repair_reason: str | None = None,
    previous_code: str | None = None,
) -> str:
    """Build a compact first-pass or bounded syntax-repair prompt."""
    prompt = f"{GAME_DEFINITIONS[game_id]['prompt']}\n\n{_LOCAL_GENERATION_CONSTRAINTS}"
    if repair_reason is None:
        return prompt
    bounded_code = (previous_code or "")[:_MAX_REPAIR_CODE_CHARS]
    return (
        f"{prompt}\n\nA previous response failed validation: {repair_reason[:300]}.\n"
        "Start over and return the complete corrected module. Previous response follows:\n"
        f"<previous_python>\n{bounded_code}\n</previous_python>"
    )


def dispatch_game_build(
    game_id: str,
    *,
    repair_reason: str | None = None,
    previous_code: str | None = None,
) -> dict[str, object]:
    """POST /api/dispatch capability=game_logic action=game_build → model call.

    Returns the JSON response from the dispatch endpoint.  On success the
    output dict contains ``text`` (generated code) and ``transport_used``.
    """
    prompt = _build_game_prompt(
        game_id,
        repair_reason=repair_reason,
        previous_code=previous_code,
    )
    client = _make_dispatch_test_client()
    resp = client.post(
        "/api/dispatch",
        json={
            "capability": "game_logic",
            "action": "game_build",
            "args": {
                "prompt": prompt,
                "model_profile": _LOCAL_PROFILE_ID,
                "temperature": 0.0,
            },
        },
    )
    return cast(dict[str, object], resp.json())


def _extract_game_code_from_dispatch_result(result: dict[str, object]) -> str | None:
    """Extract generated code text from a dispatch result."""
    results = result.get("results")
    if not results or not isinstance(results, list) or len(results) == 0:
        return None
    first = cast(dict[str, object], results[0])
    if not first.get("ok"):
        return None
    output = first.get("output")
    if isinstance(output, dict):
        text = output.get("text")
        if isinstance(text, str):
            extracted = _extract_python_module(text)
            if extracted and extracted.strip():
                return extracted.strip()
    return None


# ---------------------------------------------------------------------------
# Session-scoped gateway fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def local_gateway() -> Any:
    """Build and probe the local model gateway once per session."""
    if not _probe_local_endpoint():
        pytest.skip(f"Local model endpoint unreachable at {_LOCAL_BASE_URL}. {_SKIP_REASON}")

    gateway = _build_local_gateway()
    # Also warm the dispatch client cache
    _make_dispatch_test_client()
    print(f"\n[local-e2e] Endpoint: {_LOCAL_BASE_URL}  Model: {_LOCAL_MODEL_NAME}\n", flush=True)
    return gateway


# ---------------------------------------------------------------------------
# Per-game test generation — structural (no LLM call needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSmallModelTaskPolicyFPX1:
    """Verify SmallModelTaskPolicy correctly authorizes FPX.1 game dispatch."""

    def test_policy_authorizes_game_dispatch(self) -> None:
        result = authorize_game_dispatch("snake")
        assert result["approved"], f"Dispatch denied: {result['reason']}"
        assert result["max_attempts"] > 0
        assert result["task_fingerprint"], "empty fingerprint"

    def test_policy_denies_forbidden_impact(self) -> None:
        """When impacts include forbidden operations, dispatch should be denied."""
        import hashlib

        from general_ludd.routing_roles.small_model_policy import (
            CapabilityEvidence,
            ModelIdentity,
            SmallModelTaskPolicy,
            SmallModelTaskSpec,
            TaskImpact,
        )
        from general_ludd.schemas.benchmark import TaskRole

        task = SmallModelTaskSpec(
            task_id="fpx.1.game.snake.bad",
            task_kind="coding",
            role=TaskRole.CODER,
            collection="gludd.fpx",
            input_digest=hashlib.sha256(b"test").hexdigest(),
            impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.DEPLOYMENT}),
            acceptance_checks=("syntax_valid", "import_ok", "run_without_crash"),
        )
        identity_data = _build_model_identity()
        identity = ModelIdentity(**identity_data)
        evidence_data = _build_capability_evidence(identity_data, _build_task_spec("snake"))
        evidence = [CapabilityEvidence(**evidence_data)]

        policy = SmallModelTaskPolicy()
        decision = policy.authorize(task, identity, evidence)
        assert not decision.approved, "Task with DEPLOYMENT impact should be denied"
        assert "impact_requires_stronger_model" in decision.reason

    def test_policy_blocks_duplicate_dispatch(self) -> None:

        from general_ludd.routing_roles.small_model_policy import (
            CapabilityEvidence,
            ModelIdentity,
            SmallModelTaskPolicy,
            SmallModelTaskSpec,
        )

        task = SmallModelTaskSpec(**_build_task_spec("snake"))
        identity = ModelIdentity(**_build_model_identity())

        proof_data = _build_capability_evidence(_build_model_identity(), _build_task_spec("snake"))
        proof = CapabilityEvidence(**proof_data)

        policy = SmallModelTaskPolicy()
        first = policy.authorize(task, identity, [proof])
        assert first.approved, f"First dispatch should be allowed: {first.reason}"

        second = policy.authorize(task, identity, [proof])
        assert not second.approved, "Duplicate dispatch should be denied"
        assert "duplicate_task_claim" in second.reason

    @pytest.mark.parametrize(
        "game_id",
        [
            "snake",
            "tetris",
            "minesweeper",
            "checkers",
            "skifree",
            "banana",
            "pong",
            "breakout",
            "maze_runner",
            "word_guesser",
            "memory_match",
            "tic_tac_toe",
        ],
    )
    def test_all_games_authorize_with_local_evidence(self, game_id: str) -> None:
        result = authorize_game_dispatch(game_id)
        assert result["approved"], f"{game_id}: Dispatch denied: {result['reason']}"


@pytest.mark.e2e
class TestGamePromptTemplates:
    """Verify game prompt templates are valid and structurally complete."""

    def test_all_game_definitions_have_prompts(self) -> None:
        assert len(GAME_DEFINITIONS) == 12
        for game_id, defn in GAME_DEFINITIONS.items():
            assert defn["prompt"], f"{game_id} has empty prompt"
            assert defn["class_name"], f"{game_id} has no class_name"
            assert defn["verifications"], f"{game_id} has no verifications"

    def test_prompts_lifecycle_requirements_present(self) -> None:
        for game_id, defn in GAME_DEFINITIONS.items():
            prompt_lower = defn["prompt"].lower()
            assert "lifecycle requirements" in prompt_lower, f"{game_id} missing lifecycle header"
            assert "state" in prompt_lower, f"{game_id} missing state requirement"
            assert "start()" in prompt_lower or "start " in prompt_lower, f"{game_id} missing start()"
            assert "restart()" in prompt_lower or "restart " in prompt_lower, f"{game_id} missing restart()"

    def test_prompts_have_output_only_directive(self) -> None:
        for game_id, defn in GAME_DEFINITIONS.items():
            prompt_lower = defn["prompt"].lower()
            assert (
                "only the python code" in prompt_lower or "output only" in prompt_lower or "no prose" in prompt_lower
            ), f"{game_id} missing output-only directive"

    def test_repair_prompt_is_complete_and_bounded(self) -> None:
        prompt = _build_game_prompt(
            "snake",
            repair_reason="unterminated string literal",
            previous_code="x" * (_MAX_REPAIR_CODE_CHARS + 100),
        )

        assert "complete corrected module" in prompt
        assert "unterminated string literal" in prompt
        bounded_code = prompt.split("<previous_python>\n", 1)[1].split("\n</previous_python>", 1)[0]
        assert bounded_code == "x" * _MAX_REPAIR_CODE_CHARS
        assert "under 120 lines" in prompt

    def test_verification_lists_cover_lifecycle(self) -> None:
        lifecycle_checks = {
            "lifecycle_initial_state",
            "lifecycle_start",
            "lifecycle_score_starts_zero",
            "lifecycle_score_increments",
            "lifecycle_game_over",
            "lifecycle_game_over_idempotent",
            "lifecycle_restart",
        }
        for game_id, defn in GAME_DEFINITIONS.items():
            named = {v[0] for v in defn["verifications"]}
            missing = lifecycle_checks - named
            assert not missing, f"{game_id}: missing lifecycle checks: {missing}"


@pytest.mark.e2e
class TestGameCodeExtraction:
    """Verify code extraction from model outputs (no LLM call needed)."""

    def test_extract_fenced_python_block(self) -> None:
        text = "Here is the code:\n```python\nclass Snake:\n    pass\n```\nDone."
        extracted = _extract_python_module(text)
        assert extracted and "class Snake" in extracted

    def test_extract_import_class_code(self) -> None:
        text = "import random\nclass Snake:\n    def tick(self): pass\n"
        extracted = _extract_python_module(text)
        assert extracted and "class Snake" in extracted

    def test_parse_valid_game_code(self) -> None:
        code = "import random\n\nclass Snake:\n    def __init__(self): pass\n    def tick(self): pass\n"
        result = _parse_ast(code)
        assert result["parseable"]
        assert result["has_class"]
        assert result["has_imports"]

    def test_parse_invalid_code(self) -> None:
        code = "class Snake:\n    def __init__(self): pass\n        broken\n"
        result = _parse_ast(code)
        assert not result["parseable"]
        assert result["error"]

    def test_dispatch_extraction_strips_unclosed_python_fence(self) -> None:
        result: dict[str, object] = {
            "results": [
                {
                    "ok": True,
                    "output": {
                        "text": "```python\nimport random\n\nclass Snake:\n    pass\n",
                    },
                }
            ]
        }

        code = _extract_game_code_from_dispatch_result(result)

        assert code == "import random\n\nclass Snake:\n    pass"
        assert _parse_ast(code)["parseable"]

    @pytest.mark.parametrize(
        "result",
        [
            {},
            {"results": [{"ok": False}]},
            {"results": [{"ok": True, "output": "not-a-mapping"}]},
            {"results": [{"ok": True, "output": {"text": 42}}]},
            {"results": [{"ok": True, "output": {"text": "not Python source"}}]},
        ],
    )
    def test_dispatch_extraction_rejects_invalid_envelopes(
        self,
        result: dict[str, object],
    ) -> None:
        assert _extract_game_code_from_dispatch_result(result) is None


# ---------------------------------------------------------------------------
# Dispatch-routing structural tests (no LLM call needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestDispatchCapabilityRouting:
    """Verify capability=game_logic routes correctly through the dispatch endpoint."""

    def test_capability_routes_to_agent_collection(self) -> None:
        client = _make_dispatch_test_client()
        resp = client.post("/api/dispatch/capability", json={"capability": "game_logic"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["matches"]) == 1
        assert data["matches"][0]["collection"] == "agent"

    def test_collection_handler_rejects_unknown_module(self) -> None:
        result = _dispatch_collection_handler("general_ludd.agent.unknown", {})

        assert result == {"failed": True, "msg": "unknown module: general_ludd.agent.unknown"}

    def test_dispatch_game_build_without_prompt_handles_gracefully(self) -> None:
        client = _make_dispatch_test_client()
        resp = client.post(
            "/api/dispatch",
            json={
                "capability": "game_logic",
                "action": "game_build",
                "args": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["ok"] is True
        output = result["output"]
        assert output["failed"] is True

    def test_capability_list_includes_game_logic(self) -> None:
        client = _make_dispatch_test_client()
        resp = client.get("/api/dispatch/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "game_logic" in data["capabilities"]


# ---------------------------------------------------------------------------
# Live model tests — require reachable local endpoint, dispatch through API
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.local_model
class TestLocalModelConnectivity:
    """Verify local endpoint is reachable and dispatch path is wired."""

    def test_endpoint_reachable(self) -> None:
        if not _probe_local_endpoint():
            # CI has no local LLM server; the live-path tests in this module
            # skip the same way. When LOCAL_MODEL_BASE_URL is explicitly set,
            # the endpoint is part of the contract and must be reachable.
            if os.environ.get("LOCAL_MODEL_BASE_URL"):
                pytest.fail(f"Cannot reach {_LOCAL_BASE_URL}/models")
            pytest.skip(_SKIP_REASON)
        assert _probe_local_endpoint(), f"Cannot reach {_LOCAL_BASE_URL}/models"

    def test_gateway_builds_without_error(self) -> None:
        gateway = _build_local_gateway()
        assert gateway is not None
        profile_ids = {profile.model_profile_id for profile in gateway.list_profiles()}
        assert _LOCAL_PROFILE_ID in profile_ids

    def test_dispatch_client_created(self) -> None:
        client = _make_dispatch_test_client()
        assert client is not None
        resp = client.get("/api/dispatch/available")
        assert resp.status_code == 200
        assert "collection" in resp.json()["registered_kinds"]

    @pytest.mark.slow
    def test_model_responds_via_dispatch(self, local_gateway: Any) -> None:
        """POST /api/dispatch capability=game_logic with a simple prompt."""
        print("\n[local-e2e] Testing connectivity — dispatch path...\n", flush=True)
        result = dispatch_game_build("snake")
        code = _extract_game_code_from_dispatch_result(result)
        assert code, f"Local model dispatch returned no usable code: {result!r}"
        print(f"[local-e2e] Connectivity OK — dispatch returned {len(code)} chars\n", flush=True)


@pytest.mark.e2e
@pytest.mark.local_model
@pytest.mark.slow
class TestLocalModelGameGeneration:
    """Generate game code via POST /api/dispatch capability=game_logic.

    Each test: POST to /api/dispatch -> collection_handler -> model call ->
    extract code -> AST parse -> verify features.  Same code path as cloud
    model tests.
    """

    def _generate_and_verify(self, game_id: str) -> dict[str, Any]:
        """Run the full pipeline: dispatch -> extract -> verify."""
        result: dict[str, Any] = {
            "game_id": game_id,
            "authorized": False,
            "auth_reason": "",
            "dispatched": False,
            "code_len": 0,
            "ast_ok": False,
            "imported": False,
            "feature_failures": [],
            "lifecycle_failures": [],
            "error": None,
            "time_ms": 0,
        }
        t0 = time.time()

        # 1. Authorize via SmallModelTaskPolicy (same gate as before)
        auth = authorize_game_dispatch(game_id)
        result["authorized"] = auth["approved"]
        result["auth_reason"] = auth["reason"]
        if not auth["approved"]:
            result["error"] = f"SmallModelTaskPolicy denied: {auth['reason']}"
            result["time_ms"] = int((time.time() - t0) * 1000)
            return result

        # 2. POST /api/dispatch and use the policy's bounded retry allowance
        # for syntax repair. Small models often produce a useful first draft
        # with one truncated string or block; feeding that exact failure back
        # is both cheaper and more reliable than an unbounded blind rerun.
        repair_reason: str | None = None
        previous_code: str | None = None
        code: str | None = None
        max_attempts = max(1, int(auth["max_attempts"]))
        for attempt in range(1, max_attempts + 1):
            try:
                print(
                    f"\n[local-e2e] Dispatching {game_id} via capability=game_logic "
                    f"attempt={attempt}/{max_attempts}...\n",
                    flush=True,
                )
                dispatch_result = dispatch_game_build(
                    game_id,
                    repair_reason=repair_reason,
                    previous_code=previous_code,
                )
            except Exception as exc:
                result["error"] = f"Dispatch failed: {type(exc).__name__}: {exc}"
                repair_reason = str(result["error"])
                continue

            code = _extract_game_code_from_dispatch_result(dispatch_result)
            if not code:
                result["error"] = (
                    f"Dispatch returned no code: "
                    f"ok_count={dispatch_result.get('ok_count')} "
                    f"error_count={dispatch_result.get('error_count')}"
                )
                repair_reason = str(result["error"])
                continue
            result["dispatched"] = True
            result["code_len"] = len(code)

            ast_result = _parse_ast(code)
            result["ast_ok"] = ast_result["parseable"]
            if ast_result["parseable"]:
                break
            result["error"] = (
                f"AST parse failed: {ast_result['error']}; "
                f"code_preview={code[:300]!r}"
            )
            repair_reason = str(result["error"])
            previous_code = code
        else:
            result["time_ms"] = int((time.time() - t0) * 1000)
            return result

        assert code is not None

        # 4. Import and verify
        with tempfile.TemporaryDirectory(prefix="gludd-game-local-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            try:
                mod = _load_generated_module(code, f"game_{game_id}", tmp_path)
                result["imported"] = True

                feature_failures = verify_features(game_id, mod)
                result["feature_failures"] = feature_failures

                lifecycle_failures = lc_checks(game_id, mod)
                result["lifecycle_failures"] = lifecycle_failures

            except Exception as exc:
                result["error"] = f"Module load/verify failed: {type(exc).__name__}: {exc}"

        result["time_ms"] = int((time.time() - t0) * 1000)
        print(
            f"[local-e2e] {game_id}: "
            f"auth={result['authorized']} dispatched={result['dispatched']} "
            f"code={result['code_len']}B ast={result['ast_ok']} "
            f"import={result['imported']} "
            f"features={len(result['feature_failures'])}f "
            f"lifecycle={len(result['lifecycle_failures'])}f "
            f"t={result['time_ms']}ms\n",
            flush=True,
        )
        return result

    @pytest.mark.parametrize(
        "game_id",
        [
            "snake",
            "tetris",
            "minesweeper",
            "checkers",
            "skifree",
            "banana",
            "pong",
            "breakout",
            "maze_runner",
            "word_guesser",
            "memory_match",
            "tic_tac_toe",
        ],
    )
    def test_game_generation(self, local_gateway: Any, game_id: str) -> None:
        if _TARGET_GAME and game_id != _TARGET_GAME:
            pytest.skip(f"LOCAL_MODEL_GAME={_TARGET_GAME}, skipping {game_id}")

        result = self._generate_and_verify(game_id)

        assert result["authorized"], f"SmallModelTaskPolicy denied {game_id}: {result['auth_reason']}"
        assert result["dispatched"], f"Dispatch returned no code for {game_id}: {result['error']}"
        assert result["ast_ok"], f"Generated code does not parse for {game_id}: {result['error']}"
        assert result["imported"], f"Cannot import generated module for {game_id}: {result['error']}"

        if result["feature_failures"]:
            failures_str = "\n  ".join(result["feature_failures"])
            print(f"[local-e2e] {game_id} FEATURE GAPS:\n  {failures_str}\n", flush=True)

        if result["lifecycle_failures"]:
            lf_str = "\n  ".join(result["lifecycle_failures"])
            print(f"[local-e2e] {game_id} LIFECYCLE GAPS:\n  {lf_str}\n", flush=True)

    def test_summary_report(self, local_gateway: Any) -> None:
        """Generate all games (smoke: one game if LOCAL_MODEL_GAME is set)."""
        games_to_test = [_TARGET_GAME] if _TARGET_GAME else list(GAME_DEFINITIONS)
        results: dict[str, dict[str, Any]] = {}
        for game_id in games_to_test:
            results[game_id] = self._generate_and_verify(game_id)

        authorized = sum(1 for r in results.values() if r["authorized"])
        dispatched = sum(1 for r in results.values() if r["dispatched"])
        ast_ok = sum(1 for r in results.values() if r["ast_ok"])
        imported = sum(1 for r in results.values() if r["imported"])
        no_features = sum(1 for r in results.values() if not r["feature_failures"])
        no_lifecycle = sum(1 for r in results.values() if not r["lifecycle_failures"])
        total_time_ms = sum(r["time_ms"] for r in results.values())

        print(
            f"\n[local-e2e] SUMMARY: {len(results)} games, "
            f"authorized={authorized} dispatched={dispatched} ast={ast_ok} "
            f"imported={imported} feature_clean={no_features} "
            f"lifecycle_clean={no_lifecycle} total_time={total_time_ms}ms\n"
        )

        assert authorized == len(results), "Some games failed authorization"
        assert dispatched == len(results), "Some games failed dispatch"
        assert ast_ok == len(results), "Some games failed AST parsing"
        assert imported == len(results), "Some games failed module import"


@pytest.mark.e2e
class TestGameGeneratorWiredPolicy:
    """Prove SmallModelTaskPolicy gates GameGenerator.generate_game() correctly.

    These tests exercise the wired production dispatch path
    (GameGenerator._authorize_dispatch) rather than the manually
    orchestrated ``authorize + dispatch_game_build`` pattern in
    ``TestLocalModelGameGeneration``.
    """

    def test_game_generator_policy_rejects_without_evidence(self, local_gateway: Any) -> None:
        from general_ludd.cloud.game_e2e import GameGenerator, GameSpec
        from general_ludd.routing_roles.small_model_policy import (
            ModelIdentity,
            SmallModelTaskPolicy,
        )

        gen = GameGenerator(local_gateway, task_policy=SmallModelTaskPolicy())
        spec = GameSpec(
            name="snake",
            genre="arcade",
            description="Snake game",
            prompt_template=GAME_DEFINITIONS["snake"]["prompt"],
            expected_frames=30,
            similarity_threshold=0.0,
        )
        identity = ModelIdentity(**_build_model_identity())
        evidence = ()

        with pytest.raises(PermissionError, match="SmallModelTaskPolicy denied"):
            gen.generate_game(spec, model_identity=identity, evidence=evidence)

    def test_game_generator_policy_accepts_with_valid_evidence(self, local_gateway: Any) -> None:
        from general_ludd.cloud.game_e2e import GameGenerator, GameSpec
        from general_ludd.routing_roles.small_model_policy import (
            CapabilityEvidence,
            ModelIdentity,
            SmallModelTaskPolicy,
        )

        gen = GameGenerator(local_gateway, task_policy=SmallModelTaskPolicy())
        spec = GameSpec(
            name="snake",
            genre="arcade",
            description="Snake game",
            prompt_template=GAME_DEFINITIONS["snake"]["prompt"],
            expected_frames=30,
            similarity_threshold=0.0,
        )
        identity_data = _build_model_identity()
        identity = ModelIdentity(**identity_data)
        evidence_data = _build_capability_evidence(identity_data, _build_task_spec("snake"))
        evidence = (CapabilityEvidence(**evidence_data),)

        code = gen.generate_game(
            spec,
            model_id=_LOCAL_PROFILE_ID,
            model_identity=identity,
            evidence=evidence,
        )
        assert code, "generate_game returned empty code"
        assert "class" in code.lower(), f"No class in generated code: {code[:200]}"

    def test_game_generator_without_policy_bypasses_gate(self, local_gateway: Any) -> None:
        from general_ludd.cloud.game_e2e import GameGenerator, GameSpec

        gen = GameGenerator(local_gateway)
        spec = GameSpec(
            name="snake",
            genre="arcade",
            description="Snake game",
            prompt_template=GAME_DEFINITIONS["snake"]["prompt"],
            expected_frames=30,
            similarity_threshold=0.0,
        )
        code = gen.generate_game(spec, model_id=_LOCAL_PROFILE_ID)
        assert code, "generate_game returned empty code"
        assert "class" in code.lower(), f"No class in generated code: {code[:200]}"

    def test_game_logic_task_kind_authorizes(self, local_gateway: Any) -> None:
        import hashlib

        from general_ludd.routing_roles.small_model_policy import (
            CapabilityEvidence,
            ModelIdentity,
            SmallModelTaskPolicy,
            SmallModelTaskSpec,
            TaskImpact,
            _stable_digest,
        )
        from general_ludd.schemas.benchmark import TaskRole

        game_id = "snake"
        prompt = GAME_DEFINITIONS[game_id]["prompt"]
        input_digest = hashlib.sha256(prompt.encode()).hexdigest()

        task = SmallModelTaskSpec(
            task_id=f"fpx.1.game.{game_id}.logic",
            task_kind="game_logic",
            role=TaskRole.CODER,
            collection="gludd.fpx",
            input_digest=input_digest,
            impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
            acceptance_checks=(
                "lifecycle_initial_state",
                "lifecycle_start",
                "lifecycle_restart",
                "lifecycle_game_over",
            ),
        )
        identity_data = _build_model_identity()
        identity = ModelIdentity(**identity_data)

        acceptance_contract = {
            "acceptance_checks": sorted(task.acceptance_checks),
            "collection": task.collection,
            "role": task.role.value,
            "task_kind": task.task_kind,
        }
        acceptance_digest = _stable_digest(acceptance_contract)
        evidence = (
            CapabilityEvidence(
                model_profile_id=identity_data["model_profile_id"],
                model_identity_digest=identity.fingerprint,
                task_kind=task.task_kind,
                role=task.role,
                collection=task.collection,
                suite_id="fpx.1.game_gen_v2",
                suite_revision="v1",
                acceptance_contract_digest=acceptance_digest,
                passed_cases=20,
                total_cases=20,
                collection_ok=True,
                local_only=True,
                evidence_digest=hashlib.sha256(b"game_logic_evidence").hexdigest(),
            ),
        )

        policy = SmallModelTaskPolicy()
        decision = policy.authorize(task, identity, evidence)
        assert decision.approved, f"game_logic dispatch denied: {decision.reason}"
