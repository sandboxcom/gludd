"""Deep snapshot / reference-output tests for deterministic renderers, formatters,
serializers, and exporters.

Snapshot directory: ``tests/snapshots/``
Update snapshots:  ``GLUDD_UPDATE_SNAPSHOTS=1 make test TESTFILE=tests/unit/test_snapshot_deep.py``
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Snapshot helper
# ---------------------------------------------------------------------------

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"
_UPDATE = os.environ.get("GLUDD_UPDATE_SNAPSHOTS") == "1"


def _snapshot_path(test_name: str) -> Path:
    return SNAPSHOT_DIR / f"{test_name}.json"


def _assert_snapshot(test_name: str, actual: object) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(test_name)
    if _UPDATE:
        path.write_text(json.dumps(actual, indent=2, sort_keys=True, default=str), "utf-8")
    if not path.exists():
        path.write_text(json.dumps(actual, indent=2, sort_keys=True, default=str), "utf-8")
    expected = json.loads(path.read_text("utf-8"))
    assert actual == expected, f"Snapshot mismatch for {test_name}. Diff expected vs actual."


# ---------------------------------------------------------------------------
# MessageFormatter
# ---------------------------------------------------------------------------


class TestChatFormatterSnapshot:
    def test_detect_code_blocks_simple(self) -> None:
        from general_ludd.chat.formatter import MessageFormatter

        text = "Intro\n```python\nprint('hello')\n```\nOutro"
        result = MessageFormatter.detect_code_blocks(text)
        assert result == [("python", "print('hello')")]
        _assert_snapshot("detect_code_blocks_simple", result)

    def test_detect_code_blocks_multiple(self) -> None:
        from general_ludd.chat.formatter import MessageFormatter

        text = '```python\nx=1\n```\n```json\n{"k":1}\n```'
        result = MessageFormatter.detect_code_blocks(text)
        _assert_snapshot("detect_code_blocks_multiple", result)

    def test_detect_code_blocks_no_fences(self) -> None:
        from general_ludd.chat.formatter import MessageFormatter

        result = MessageFormatter.detect_code_blocks("Plain text only.")
        _assert_snapshot("detect_code_blocks_no_fences", result)

    def test_detect_code_blocks_empty_block(self) -> None:
        from general_ludd.chat.formatter import MessageFormatter

        result = MessageFormatter.detect_code_blocks("```python\n\n```")
        _assert_snapshot("detect_code_blocks_empty_block", result)

    def test_highlight_preserves_plain_text(self) -> None:
        from general_ludd.chat.formatter import MessageFormatter

        result = MessageFormatter.highlight("No code blocks here.")
        _assert_snapshot("highlight_preserves_plain_text", result)


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


class TestAuditEventSnapshot:
    def test_audit_event_to_dict(self) -> None:
        from general_ludd.ansible.audit import NETWORK_DENY, AuditEvent

        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="ansible.builtin.uri",
            detail={"method": "GET", "url": "https://api.example.com/v1/data", "policy": "deny-external"},
            playbook="site.yml",
            timestamp=1711234567.891,
            sandbox_id="sb-abc123",
        )
        _assert_snapshot("audit_event_to_dict", event.to_dict())

    def test_audit_event_to_json_deterministic(self) -> None:
        from general_ludd.ansible.audit import CREDENTIAL_ACCESS, AuditEvent

        event = AuditEvent(
            event_type=CREDENTIAL_ACCESS,
            module="openbao_lookup",
            detail={"secret_name": "staging/db/password"},
            playbook="deploy.yml",
            timestamp=1711234567.0,
            sandbox_id=None,
        )
        result = event.to_json()
        _assert_snapshot("audit_event_to_json", json.loads(result))

    def test_audit_event_to_json_sort_keys_stable(self) -> None:
        from general_ludd.ansible.audit import PATH_BLOCKED, AuditEvent

        event = AuditEvent(
            event_type=PATH_BLOCKED,
            module="copy",
            detail={"path": "/etc/shadow"},
            playbook="backup.yml",
            timestamp=1700000000.0,
        )
        a = event.to_json()
        b = event.to_json()
        assert a == b
        _assert_snapshot("audit_event_path_blocked", json.loads(a))


# ---------------------------------------------------------------------------
# EntityNode / Association
# ---------------------------------------------------------------------------


class TestEntityGraphSnapshot:
    def test_entity_node_to_dict_minimal(self) -> None:
        from general_ludd.entity.graph import EntityNode

        node = EntityNode(id="n-1", name="Acme Corp")
        _assert_snapshot("entity_node_minimal", node.to_dict())

    def test_entity_node_to_dict_full(self) -> None:
        from general_ludd.entity.graph import EntityNode

        node = EntityNode(
            id="n-2",
            name="Beta LLC",
            entity_type="limited",
            jurisdiction="US-DE",
            industry="Finance",
        )
        _assert_snapshot("entity_node_full", node.to_dict())

    def test_entity_node_to_json(self) -> None:
        from general_ludd.entity.graph import EntityNode

        node = EntityNode(id="n-3", name="Gamma Inc", metadata={"revenue": 1_000_000})
        result = node.to_json()
        _assert_snapshot("entity_node_to_json", json.loads(result))

    def test_association_to_dict(self) -> None:
        from general_ludd.entity.graph import Association

        assoc = Association(
            source_id="n-1",
            target_id="n-2",
            assoc_type="financial",
            weight=0.75,
            description="Investment of $5M",
            metadata={"round": "Series A"},
        )
        _assert_snapshot("association_to_dict", assoc.to_dict())

    def test_association_to_json(self) -> None:
        from general_ludd.entity.graph import Association

        assoc = Association(source_id="a", target_id="b", assoc_type="contractual")
        result = assoc.to_json()
        _assert_snapshot("association_to_json", json.loads(result))

    def test_association_classify_type(self) -> None:
        from general_ludd.entity.graph import Association

        cases: dict[str, str] = {
            "Founder and CEO of the company": "personal",
            "Competitor in the widget market": "competitive",
            "Acquired 50% stake in startup": "financial",
            "Partnership agreement signed 2024": "contractual",
            "Is friends with the mayor": "other",
        }
        for desc, expected in cases.items():
            assert Association.classify_type(desc) == expected
        _assert_snapshot("association_classify_types", cases)


# ---------------------------------------------------------------------------
# BehaviorRenderer
# ---------------------------------------------------------------------------


class TestBehaviorRendererSnapshot:
    def test_render_default_primary(self) -> None:
        from general_ludd.agents.behavior import BehaviorRenderer, default_primary_behavior

        behavior = default_primary_behavior()
        renderer = BehaviorRenderer()
        rendered = renderer.render(behavior)
        _assert_snapshot("behavior_render_primary", rendered)

    def test_render_minimal_behavior(self) -> None:
        from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer, GuardrailConfig

        behavior = AgentBehavior(
            role=None,
            goal=None,
            completion_policy="complete_all",
            tdd_enforced=False,
            evidence_required=False,
            self_directed_work=False,
            commit_after_green=False,
            atomic_commits=False,
            session_persistence=False,
            guardrail=GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False),
        )
        renderer = BehaviorRenderer()
        rendered = renderer.render(behavior)
        _assert_snapshot("behavior_render_minimal", rendered)

    def test_render_caching_is_stable(self) -> None:
        from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer

        behavior = AgentBehavior(role="cached_role")
        renderer = BehaviorRenderer()
        a = renderer.render(behavior)
        b = renderer.render(behavior)
        assert a == b
        _assert_snapshot("behavior_render_cached", a)


# ---------------------------------------------------------------------------
# PlanArtifact
# ---------------------------------------------------------------------------


class TestPlanArtifactSnapshot:
    def test_to_markdown_full(self) -> None:
        from general_ludd.planning.artifact import PlanArtifact

        artifact = PlanArtifact(
            todo_id="TODO-042",
            title="Add snapshot testing",
            description="Implement deep snapshot/reference output tests",
            target_files=["src/general_ludd/chat/formatter.py", "tests/unit/test_snapshot_deep.py"],
            contracts=["def test_foo(): None"],
            dependencies=["TODO-041"],
            notes="Cover all renderers",
            content="Phase 1: Identify deterministic outputs\nPhase 2: Write snapshot tests",
        )
        _assert_snapshot("plan_artifact_markdown_full", artifact.to_markdown())

    def test_to_dict(self) -> None:
        from general_ludd.planning.artifact import PlanArtifact

        artifact = PlanArtifact(
            todo_id="TD-1",
            title="Minimal",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        result = artifact.to_dict()
        assert result["todo_id"] == "TD-1"
        assert result["title"] == "Minimal"
        _assert_snapshot("plan_artifact_to_dict", result)


# ---------------------------------------------------------------------------
# FieldMeta
# ---------------------------------------------------------------------------


class TestFieldMetaSnapshot:
    def test_field_meta_to_dict_flat(self) -> None:
        from general_ludd.renderers.schema_loader import FieldMeta

        field = FieldMeta(
            name="email",
            title="Email Address",
            description="User email",
            type="string",
            required=True,
            format="email",
        )
        _assert_snapshot("field_meta_flat", field.to_dict())

    def test_field_meta_to_dict_nested(self) -> None:
        from general_ludd.renderers.schema_loader import FieldMeta

        child = FieldMeta(name="city", title="City", description="", type="string")
        parent = FieldMeta(
            name="address",
            title="Address",
            description="Mailing address",
            type="object",
            required=True,
            children=[child],
        )
        _assert_snapshot("field_meta_nested", parent.to_dict())


# ---------------------------------------------------------------------------
# extract_field_metadata
# ---------------------------------------------------------------------------


class TestExtractFieldMetadataSnapshot:
    def test_extract_simple_schema(self) -> None:
        from general_ludd.renderers.schema_loader import extract_field_metadata

        schema: dict[str, object] = {
            "properties": {
                "name": {"type": "string", "description": "User name"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        fields = extract_field_metadata(schema)
        result = [f.to_dict() for f in fields]
        _assert_snapshot("extract_field_metadata_simple", result)

    def test_extract_empty_properties(self) -> None:
        from general_ludd.renderers.schema_loader import extract_field_metadata

        result = [f.to_dict() for f in extract_field_metadata({})]
        _assert_snapshot("extract_field_metadata_empty", result)


# ---------------------------------------------------------------------------
# CollectionMeta
# ---------------------------------------------------------------------------


class TestCollectionMetaSnapshot:
    def test_collection_meta_to_dict(self) -> None:
        from general_ludd.dispatch.capabilities import CollectionMeta

        meta = CollectionMeta(
            name="general_ludd_agent",
            namespace="general_ludd",
            version="1.0.0",
            description="Agent collection",
            tags=frozenset({"agent", "automation"}),
            raw_tags=["agent", "automation"],
            roles=[{"name": "project_init", "description": "Init project scaffold"}],
            model_capabilities=[{"name": "code_gen", "description": "Generate code", "quality_class": "high"}],
            role_capabilities={"project_init": ["scaffold"]},
        )
        _assert_snapshot("collection_meta_to_dict", meta.to_dict())


# ---------------------------------------------------------------------------
# ABVerdict
# ---------------------------------------------------------------------------


class TestABVerdictSnapshot:
    def test_ab_verdict_to_dict(self) -> None:
        from general_ludd.abtest.compare import ABVerdict
        from general_ludd.abtest.runner import Result

        a = Result(ok=True, crashed=False, timed_out=False, exit_code=0, signal=0, output="ok", duration_s=1.5)
        b = Result(ok=True, crashed=False, timed_out=False, exit_code=0, signal=0, output="ok", duration_s=1.3)
        verdict = ABVerdict(a=a, b=b, promote=True, reason="Candidate ok and within duration slack")
        _assert_snapshot("ab_verdict_to_dict", verdict.to_dict())


# ---------------------------------------------------------------------------
# SessionRecord
# ---------------------------------------------------------------------------


class TestSessionRecordSnapshot:
    def test_session_record_to_dict(self) -> None:
        from general_ludd.security.session_ttl import SessionRecord

        record = SessionRecord(
            session_id="sess-abc",
            audience="admin",
            created_at=1700000000.0,
            last_access=1700000100.0,
            absolute_ttl_seconds=3600,
            idle_ttl_seconds=900,
            revoked=False,
            parent_session_id=None,
        )
        _assert_snapshot("session_record_to_dict", record.to_dict())


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


class TestSearchResultSnapshot:
    def test_search_result_to_dict(self) -> None:
        from general_ludd.history.git_indexer import SearchResult

        result = SearchResult(
            hash="abc123def456",
            author="dev@example.com",
            date="2025-01-15T10:30:00Z",
            message="feat: add snapshot tests",
            insertions=42,
            deletions=3,
            matched_paths=["tests/unit/test_snapshot_deep.py"],
        )
        _assert_snapshot("search_result_to_dict", result.to_dict())


# ---------------------------------------------------------------------------
# VariableStore safe dispatch name
# ---------------------------------------------------------------------------


class TestVariableStoreSnapshot:
    def test_safe_dispatch_name_deterministic(self) -> None:
        from general_ludd.dispatch.variable_store import _safe_dispatch_name

        cases: dict[str, str] = {}
        for raw in ["my-tool", "foo.bar", "last", "simple", "tool.with-dots-and-dashes"]:
            cases[raw] = _safe_dispatch_name(raw)
        _assert_snapshot("safe_dispatch_names", cases)

    def test_safe_dispatch_name_reserved(self) -> None:
        from general_ludd.dispatch.variable_store import _safe_dispatch_name

        result = _safe_dispatch_name("last")
        assert result != "last"
        _assert_snapshot("safe_dispatch_name_last", result)


# ---------------------------------------------------------------------------
# Validation error formatting
# ---------------------------------------------------------------------------


class TestValidationErrorFormatSnapshot:
    def test_format_validation_error(self) -> None:
        import jsonschema

        schema: dict[str, object] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors({}), key=lambda e: list(e.path))
        messages = []
        for e in errors:
            from general_ludd.renderers.schema_loader import _format_validation_error

            messages.append(_format_validation_error(e))
        _assert_snapshot("format_validation_error", messages)


# ---------------------------------------------------------------------------
# HardwareProfile
# ---------------------------------------------------------------------------


class TestHardwareProfileSnapshot:
    def test_hardware_profile_to_dict_shape(self) -> None:
        from general_ludd.hardware.probe import HardwareProfile

        profile = HardwareProfile(
            cpu_count=8,
            total_memory_gb=16.0,
            recommended_workers=2,
            gunicorn_workers=2,
            thread_pool_size=8,
            network_concurrency=16,
            local_model_allowed=True,
        )
        result = profile.to_dict()
        _assert_snapshot("hardware_profile_to_dict", {k: v for k, v in result.items()})


# ---------------------------------------------------------------------------
# GuardrailConfig
# ---------------------------------------------------------------------------


class TestGuardrailConfigSnapshot:
    def test_guardrail_config_model_dump(self) -> None:
        from general_ludd.agents.behavior import GuardrailConfig

        gc = GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True)
        _assert_snapshot("guardrail_config", gc.model_dump())

    def test_guardrail_layer_count(self) -> None:
        from general_ludd.agents.behavior import GuardrailConfig

        assert GuardrailConfig().layer_count() == 3
        assert GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False).layer_count() == 0


# ---------------------------------------------------------------------------
# Snapshot file integrity
# ---------------------------------------------------------------------------


class TestSnapshotIntegrity:
    def test_all_snapshots_are_valid_json(self) -> None:
        if not SNAPSHOT_DIR.is_dir():
            pytest.skip("No snapshots directory yet")
        for p in sorted(SNAPSHOT_DIR.glob("*.json")):
            content = p.read_text("utf-8")
            json.loads(content)

    def test_snapshot_count_meets_minimum(self) -> None:
        if not SNAPSHOT_DIR.is_dir():
            pytest.skip("No snapshots directory yet")
        count = len(list(SNAPSHOT_DIR.glob("*.json")))
        assert count >= 15, f"Expected at least 15 snapshot files, found {count}"

    def test_snapshot_dir_is_clean_no_temp_files(self) -> None:
        if not SNAPSHOT_DIR.is_dir():
            pytest.skip("No snapshots directory yet")
        temps = list(SNAPSHOT_DIR.glob("*.tmp")) + list(SNAPSHOT_DIR.glob("*~"))
        assert len(temps) == 0, f"Temporary files found: {temps}"


# ---------------------------------------------------------------------------
# Snapshot update guard
# ---------------------------------------------------------------------------


class TestSnapshotUpdateGuard:
    def test_update_flag_is_not_set_in_ci(self) -> None:
        if os.environ.get("CI") == "true":
            assert os.environ.get("GLUDD_UPDATE_SNAPSHOTS") != "1", "GLUDD_UPDATE_SNAPSHOTS=1 must not be set in CI"
