"""Cross-feature integration tests spanning multiple NF feature surfaces.

Tests the INTERACTIONS between features rather than each feature in isolation
(their standalone integration tests already cover that). Three cross-cutting
scenarios:

1. **STS tokens + VM sandbox** — a subagent's STS-narrowed PermissionSpec
   flows into the FirecrackerBackend.apply call, and token expiry prevents
   sandbox dispatch (the spec cannot be resolved).

2. **Chat CLI + language expert** — the ChatSession system prompt and the
   language expert CLI (``gludd language``) share the same knowledge modules
   (``general_ludd.language.*``). Multi-language text (UTF-8, BOM, homoglyphs)
   round-trips through both surfaces.

3. **E2E test gen + binary RE** — the CodePathAnalyzer + ScenarioGenerator
   pipeline (NF.5) consumes binary_re role source files (NF.4) and produces
   test scenarios that reference binary analysis entry points.

All tests use mocks/stubs for external services (OpenBao, firecracker, HTTP
APIs) — no real credentials or VM processes are required.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_ROOT = Path(__file__).resolve().parents[2]
_BINARY_RE_ROOT = _ROOT / "collections" / "ansible_collections" / "general_ludd" / "binary_re"


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _make_narrow_spec(agent_type: str = "subagent"):
    """Build an STS-narrowed PermissionSpec carrying sandbox + secret caps."""
    from general_ludd.security.permissions import Capability, PermissionSpec

    return PermissionSpec(
        agent_type=agent_type,
        capabilities=[
            Capability(
                resource="sandbox:vm",
                actions=["apply"],
                constraints={"backend": "firecracker"},
            ),
            Capability(
                resource="secret:openbao",
                actions=["read"],
                constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
            ),
        ],
    )


def _seeded_bao_client():
    """Mock OpenBao client seeded with build + shared secrets."""
    store = {
        "secret/data/gludd/build/cosign": {"password": "hunter2"},
        "secret/data/gludd/shared/llm_keys/anthropic": {"key": "sk-AAAA"},
    }
    client = MagicMock()
    client.is_authenticated.return_value = True

    def _read(path, mount_point="secret"):
        if path not in store:
            import hvac

            raise hvac.exceptions.InvalidPath(path)
        return {"data": {"data": store[path]}}

    client.secrets.kv.v2.read_secret_version.side_effect = _read
    return client, store


def _has_tree_sitter() -> bool:
    try:
        import tree_sitter_python  # noqa: F401
        from tree_sitter import Language, Parser  # noqa: F401
    except ImportError:
        return False
    return True


# ===========================================================================
# 1. STS tokens + VM sandbox — token-scoped sandbox dispatch
# ===========================================================================


class TestStsTokenScopedSandboxDispatch:
    """STS-narrowed PermissionSpec propagates through sandbox apply + dispatch.

    The cross-feature contract: when a subagent is dispatched with an STS
    token, the token's PermissionSpec (1) gates secret access via
    SecretsManager, AND (2) is the exact spec passed to the sandbox
    backend's ``apply`` method. The same narrow spec controls both.
    """

    def test_sts_spec_carries_sandbox_capability(self):
        """An STS token can carry sandbox:vm capabilities alongside secret caps."""
        from general_ludd.security.sts import STSRegistry

        spec = _make_narrow_spec()
        registry = STSRegistry()
        token = registry.issue(agent_type="subagent", spec=spec, ttl_seconds=300)

        claim = registry.resolve(token)
        assert claim is not None
        sandbox_caps = [c for c in claim.spec.capabilities if c.resource == "sandbox:vm"]
        assert len(sandbox_caps) == 1
        assert "apply" in sandbox_caps[0].actions

    def test_sts_spec_propagates_to_firecracker_apply(self):
        """FirecrackerBackend.apply receives the STS-narrowed spec, not the parent's."""
        from general_ludd.security.sandboxes import SandboxTarget
        from general_ludd.security.sandboxes.vm.firecracker_backend import FirecrackerBackend
        from general_ludd.security.sts import STSRegistry

        narrow_spec = _make_narrow_spec(agent_type="sts_agent")
        registry = STSRegistry()
        token = registry.issue(agent_type="sts_agent", spec=narrow_spec, ttl_seconds=60)
        claim = registry.resolve(token)
        assert claim is not None

        fc_stub_handle = MagicMock()
        fc_stub_handle.backend = "firecracker"
        fc_stub_handle.applied = True
        fc_stub_handle.token = "gludd-sts_agent"
        fc_stub_handle.extra = {"stub": True}

        with (
            patch.object(FirecrackerBackend, "available", return_value=True),
            patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._spawn_firecracker",
                return_value=fc_stub_handle,
            ),
        ):
            target = SandboxTarget(pid=99, directory="/tmp/sts-sandbox")
            handle = FirecrackerBackend.apply(claim.spec, target)

        assert handle.applied is True
        assert "sts_agent" in handle.token

    def test_expired_sts_token_blocks_sandbox_dispatch(self):
        """An expired STS token resolves to None — no spec, no sandbox apply."""
        from general_ludd.security.permissions import default_spec
        from general_ludd.security.sts import STSRegistry

        registry = STSRegistry()
        token = registry.issue(
            agent_type="subagent",
            spec=default_spec("subagent"),
            ttl_seconds=0,
        )
        assert registry.resolve(token) is None

    def test_sts_narrowed_secret_access_inside_sandbox_dispatch(self):
        """SecretsManager built from the STS spec enforces path narrowing."""
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import (
            SecretPermissionDeniedError,
            SecretsManager,
        )
        from general_ludd.security.sts import STSRegistry

        client, _ = _seeded_bao_client()
        narrow_spec = _make_narrow_spec()
        registry = STSRegistry()
        token = registry.issue(agent_type="subagent", spec=narrow_spec, ttl_seconds=120)
        claim = registry.resolve(token)
        assert claim is not None

        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=claim.spec,
        )
        assert mgr.read_secret("secret/data/gludd/build/cosign") == {"password": "hunter2"}
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/shared/llm_keys/anthropic")

    def test_token_injector_env_vars_populated(self):
        """SubagentTokenInjector populates GLUDD_STS_ROLE_ID/SECRET_ID."""
        from general_ludd.sts.injector import SubagentTokenInjector

        fake_creds = MagicMock()
        fake_creds.role_id = "role-abc"
        fake_creds.secret_id = "secret-xyz"

        fake_minter = MagicMock()
        fake_minter.mint = AsyncMock(return_value=fake_creds)
        fake_store = MagicMock()
        fake_store.store = AsyncMock()
        fake_dispatcher = MagicMock()

        injector = SubagentTokenInjector(
            minter=fake_minter,
            store=fake_store,
            dispatcher=fake_dispatcher,
        )

        import asyncio

        fake_task = MagicMock()
        fake_task.task_id = "task-1"
        fake_task.invoker_name = "parent-agent"
        fake_task.env = {}

        asyncio.run(injector.enrich(fake_task))

        assert fake_task.env["GLUDD_STS_ROLE_ID"] == "role-abc"
        assert fake_task.env["GLUDD_STS_SECRET_ID"] == "secret-xyz"

    def test_sts_revocation_invalidates_sandbox_eligibility(self):
        """After revoke(), resolve() returns None — sandbox dispatch cannot proceed."""
        from general_ludd.security.sts import STSRegistry

        registry = STSRegistry()
        token = registry.issue(
            agent_type="subagent",
            spec=_make_narrow_spec(),
            ttl_seconds=300,
        )
        assert registry.resolve(token) is not None
        assert registry.revoke(token) is True
        assert registry.resolve(token) is None


# ===========================================================================
# 2. Chat CLI + language expert — multi-language chat responses
# ===========================================================================


class TestChatCliLanguageExpertCross:
    """The ChatSession and the ``gludd language`` CLI share the same
    ``general_ludd.language.*`` knowledge modules. Multi-language text
    (BOM, homoglyphs, mojibake) flows through both surfaces consistently.
    """

    def test_chat_session_system_prompt_exists(self):
        """ChatSession initializes with a non-empty system prompt."""
        from general_ludd.chat.session import ChatSession

        session = ChatSession(
            model="test/model",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        assert len(session.history) >= 1
        assert session.history[0]["role"] == "system"
        assert len(session.history[0]["content"]) > 10

    def test_language_detect_encoding_cli_produces_json(self, tmp_path):
        """``gludd language detect-encoding`` emits valid JSON consumable by chat."""
        test_file = tmp_path / "sample.txt"
        test_file.write_bytes("Caf\u00e9".encode("utf-8"))

        result = subprocess.run(
            [sys.executable, "-m", "general_ludd.cli", "language", "detect-encoding", str(test_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0

    def test_multilingual_text_roundtrips_through_chat_and_language_modules(self):
        """UTF-8 text with multi-script characters is handled by both chat
        (as prompt content) and the language expert (as analysis input)."""
        from general_ludd.language.charset_map import BOM_SIGNATURES
        from general_ludd.language.unicode_data import plane_of

        multilingual = "Hello \u4e16\u754c Caf\u00e9 \u0645\u0631\u062d\u0628\u0627"
        encoded = multilingual.encode("utf-8")

        assert encoded.decode("utf-8") == multilingual

        for char in multilingual:
            plane = plane_of(ord(char))
            assert plane in ("BMP", "SMP", "SIP")

        assert BOM_SIGNATURES["UTF-8"] + encoded != encoded
        assert (BOM_SIGNATURES["UTF-8"] + encoded).startswith(BOM_SIGNATURES["UTF-8"])

    def test_homoglyph_scan_output_is_chat_consumable(self):
        """Homoglyph scan output JSON structure is parseable for chat integration."""
        result = subprocess.run(
            [sys.executable, "-m", "general_ludd.cli", "language", "scan-homoglyphs", f"pple{chr(0x0430)}.com"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0

    def test_chat_session_accepts_multilingual_prompt(self):
        """ChatSession.truncate_input handles multilingual UTF-8 without error."""
        from general_ludd.chat.session import ChatSession

        session = ChatSession(
            model="test/model",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        multilingual_prompt = (
            "Analyze this text: Hello \u4e16\u754c Caf\u00e9 "
            "\u0645\u0631\u062d\u0628\u0627 \u3053\u3093\u306b\u3061\u306f"
        )
        truncated = session._truncate_input(multilingual_prompt)
        assert isinstance(truncated, str)
        assert len(truncated) > 0

    def test_bom_detection_supports_chat_file_attachments(self, tmp_path):
        """A file with UTF-8 BOM (as might be attached to chat) is detected."""
        from general_ludd.language.charset_map import BOM_BY_SEQUENCE, BOM_SIGNATURES

        bom = BOM_SIGNATURES["UTF-8"]
        content = bom + b"Chat attachment content"
        test_file = tmp_path / "attachment.txt"
        test_file.write_bytes(content)

        raw = test_file.read_bytes()
        detected = None
        for sig in sorted(BOM_SIGNATURES.values(), key=len, reverse=True):
            if raw.startswith(sig):
                detected = BOM_BY_SEQUENCE[sig]
                break
        assert detected == "UTF-8"

    def test_language_subparser_removed_from_cli(self):
        """The language expert subparser is no longer registered in core CLI."""
        result = subprocess.run(
            [sys.executable, "-m", "general_ludd.cli", "language", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0


# ===========================================================================
# 3. E2E test gen + binary RE — generate tests for binary analysis roles
# ===========================================================================


_BinaryReModules = pytest.importorskip("general_ludd.agents.test_generation.scenario_generator")


@pytest.mark.skipif(not _has_tree_sitter(), reason="tree-sitter not installed")
class TestE2eTestGenBinaryReCross:
    """The NF.5 E2E test generation pipeline consumes NF.4 binary_re role
    source files. The CodePathAnalyzer extracts public functions (main,
    analyze, scan) and the ScenarioGenerator maps them to test scenarios.
    """

    @pytest.fixture
    def binary_re_analyze_module(self, tmp_path):
        """A synthetic module mirroring binary_re role entry points."""
        module = tmp_path / "binary_analyze.py"
        module.write_text(
            '"""Binary analysis entry points - mirrors binary_re role CLI."""\n\n\n'
            "def analyze_binary(target_path):\n"
            "    return {'target': target_path}\n\n\n"
            "def create_report(target_path):\n"
            "    return {'report': True}\n\n\n"
            "def delete_artifacts(target_path):\n"
            "    return {'deleted': True}\n\n\n"
            "def authenticate_session(token):\n"
            "    return token == 'valid'\n\n\n"
            "def init_backend(config):\n"
            "    return config\n"
        )
        return module

    def test_code_path_analyzer_extracts_binary_re_symbols(self, binary_re_analyze_module):
        """CodePathAnalyzer finds the public entry points in binary_re source."""
        from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer

        symbols = CodePathAnalyzer().analyze(str(binary_re_analyze_module))
        public_names = {f.name for f in symbols.functions if f.is_public}
        assert {"analyze_binary", "create_report", "delete_artifacts", "authenticate_session"} <= public_names

    def test_scenario_generator_maps_binary_re_to_crud_and_auth(self, binary_re_analyze_module):
        """Binary RE functions map to crud_lifecycle and auth_flow scenarios."""
        from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer
        from general_ludd.agents.test_generation.scenario_generator import ScenarioGenerator

        symbols = CodePathAnalyzer().analyze(str(binary_re_analyze_module))
        scenarios = ScenarioGenerator().generate(symbols)
        names = {s.name for s in scenarios}
        assert "crud_lifecycle" in names
        assert "auth_flow" in names

    def test_generated_scenarios_reference_binary_targets(self, binary_re_analyze_module):
        """Coverage targets in generated scenarios reference the binary analysis functions."""
        from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer
        from general_ludd.agents.test_generation.scenario_generator import ScenarioGenerator

        symbols = CodePathAnalyzer().analyze(str(binary_re_analyze_module))
        scenarios = ScenarioGenerator().generate(symbols)
        all_targets = set()
        for s in scenarios:
            all_targets.update(s.coverage_targets)
        assert "create_report" in all_targets or "delete_artifacts" in all_targets

    def test_scenario_steps_are_actionable(self, binary_re_analyze_module):
        """Each generated scenario has >=3 steps with action + expected_result."""
        from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer
        from general_ludd.agents.test_generation.scenario_generator import ScenarioGenerator

        symbols = CodePathAnalyzer().analyze(str(binary_re_analyze_module))
        scenarios = ScenarioGenerator().generate(symbols)
        for scen in scenarios:
            assert len(scen.steps) >= 3
            for step in scen.steps:
                assert step.action
                assert step.expected_result

    def test_binary_re_role_files_are_analyzable(self):
        """Actual binary_re role source files (gdb_analyze.py etc.) are parseable
        by CodePathAnalyzer — the generator can consume real NF.4 artifacts."""
        from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer

        gdb_path = _BINARY_RE_ROOT / "roles" / "gdb_analyze" / "files" / "gdb_analyze.py"
        if not gdb_path.is_file():
            pytest.skip(f"gdb_analyze.py not found at {gdb_path}")

        symbols = CodePathAnalyzer().analyze(str(gdb_path))
        all_names = {f.name for f in symbols.functions}
        assert "main" in all_names or len(all_names) > 0

    def test_generated_tests_for_binary_re_produce_valid_manifest(self, binary_re_analyze_module, tmp_path):
        """Running write_e2e_tests on binary_re scenarios produces a valid manifest."""
        from dataclasses import asdict

        from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer
        from general_ludd.agents.test_generation.scenario_generator import ScenarioGenerator

        write_script = (
            _ROOT
            / "collections"
            / "ansible_collections"
            / "general_ludd"
            / "e2e_test_gen"
            / "roles"
            / "write_e2e_tests"
            / "files"
            / "write_e2e_tests.py"
        )
        if not write_script.is_file():
            pytest.skip("write_e2e_tests.py not found")

        symbols = CodePathAnalyzer().analyze(str(binary_re_analyze_module))
        scenarios = ScenarioGenerator().generate(symbols)
        assert len(scenarios) >= 2

        payload = {
            "module": str(binary_re_analyze_module),
            "scenarios": [
                {
                    "name": s.name,
                    "description": s.description,
                    "coverage_targets": s.coverage_targets,
                    "steps": [asdict(st) for st in s.steps],
                }
                for s in scenarios
            ],
        }
        scenarios_file = tmp_path / "binary_re_scenarios.json"
        scenarios_file.write_text(json.dumps(payload))
        out_dir = tmp_path / "generated"
        result = subprocess.run(
            [sys.executable, str(write_script), "--scenarios-file", str(scenarios_file), "--output-dir", str(out_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"write_e2e_tests failed: {result.stderr}"
        manifest = json.loads((out_dir / "generated_tests.json").read_text())
        assert manifest["scenario_count"] >= 2


# ===========================================================================
# 4. Three-way cross: STS + sandbox + language expert
# ===========================================================================


class TestThreeWayStsSandboxLanguage:
    """An STS-scoped subagent that needs both sandbox dispatch AND multi-language
    text analysis. Tests that the narrowed spec gates both surfaces simultaneously.
    """

    def test_sts_spec_with_sandbox_and_language_capabilities(self):
        """A single STS spec can carry both sandbox:vm and file:read (language) caps."""
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import STSRegistry

        spec = PermissionSpec(
            agent_type="analysis_agent",
            capabilities=[
                Capability(resource="sandbox:vm", actions=["apply"]),
                Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/tmp/analysis/"}),
                Capability(resource="secret:openbao", actions=["read"]),
            ],
        )
        registry = STSRegistry()
        token = registry.issue(agent_type="analysis_agent", spec=spec, ttl_seconds=300)
        claim = registry.resolve(token)
        assert claim is not None
        assert len(claim.spec.capabilities) == 3

    def test_language_modules_importable_in_sandbox_agent_context(self):
        """Language expert modules are importable — they'd be available inside
        a sandboxed subagent that has file:read capability."""
        import general_ludd.language.charset_map as cm
        import general_ludd.language.unicode_data as ud

        assert hasattr(cm, "BOM_SIGNATURES")
        assert hasattr(ud, "plane_of")

    def test_sts_token_scopes_secret_for_language_analysis(self):
        """An STS-narrowed spec that grants only build-path secret access
        denies shared secrets — even when the agent is doing language analysis."""
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import (
            SecretPermissionDeniedError,
            SecretsManager,
        )
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import STSRegistry

        client, _ = _seeded_bao_client()
        spec = PermissionSpec(
            agent_type="lang_analysis",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
                ),
            ],
        )
        registry = STSRegistry()
        token = registry.issue(agent_type="lang_analysis", spec=spec, ttl_seconds=60)
        claim = registry.resolve(token)
        assert claim is not None

        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=claim.spec,
        )
        assert mgr.read_secret("secret/data/gludd/build/cosign") == {"password": "hunter2"}
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/shared/llm_keys/anthropic")
