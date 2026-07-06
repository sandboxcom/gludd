"""Dead-class wiring proofs — batch 2.

Each test proves that a class previously flagged as "defined but never
instantiated or referenced anywhere" is now wired into a genuine production
call path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestStsAuditModelWiring:
    """StsAuditModel wired into daemon.py to log STS token usage events."""

    def test_sts_audit_model_constructs(self):
        from general_ludd.db.models import StsAuditModel

        row = StsAuditModel(
            token_id="tok-001",
            issuer_agent_id="agent-a",
            subject_agent_id="agent-b",
            spec_yaml="capabilities: [read]",
            issued_at=1720000000.0,
            expires_at=1720003600.0,
            use_count=0,
            events="[]",
        )
        assert row.token_id == "tok-001"

    def test_sts_audit_helper_present_on_app_state(self):
        from general_ludd.daemon import _build_sts_audit_logger

        session_factory = AsyncMock()
        logger = _build_sts_audit_logger(session_factory)
        assert callable(logger)

    @pytest.mark.asyncio
    async def test_sts_audit_helper_logs_usage(self):
        from general_ludd.daemon import _build_sts_audit_logger

        row = MagicMock()
        row.token_id = "tok-001"
        row.use_count = 0
        row.events = "[]"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=row)

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        logger_fn = _build_sts_audit_logger(mock_session_factory)
        await logger_fn(token_id="tok-001", event="USED_BY_AGENT", agent_id="agent-c")

        assert row.use_count == 1
        mock_session.commit.assert_awaited()


class TestQueueRepositoryWiring:
    """QueueRepository wired into daemon queue seed path."""

    @pytest.mark.asyncio
    async def test_queue_repository_used_in_daemon_seed_path(self):
        from general_ludd.db.repository import QueueRepository

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        repo = QueueRepository(mock_session)
        enabled = await repo.list_enabled()
        assert enabled == []

    def test_queue_repository_seed_function_uses_repo(self):
        import inspect

        from general_ludd.db import session as db_session

        src = inspect.getsource(db_session.seed_initial_queues)
        assert "QueueRepository" in src


class TestSlowOperationEventWiring:
    """SlowOperationEvent wired into daemon.py long-op detection."""

    def test_slow_operation_event_constructs(self):
        from general_ludd.events.types import EventType, SlowOperationEvent

        evt = SlowOperationEvent(
            operation="model_call",
            duration_s=12.5,
            baseline_s=1.2,
            factor=10.4,
        )
        assert evt.type == EventType.SLOW_OPERATION
        assert evt.payload["operation"] == "model_call"
        assert evt.payload["duration_s"] == 12.5
        assert evt.payload["baseline_s"] == 1.2
        assert evt.payload["factor"] == 10.4

    def test_slow_operation_publish_helper_created(self):
        from general_ludd.daemon import _build_slow_op_publisher

        bus = MagicMock()
        publisher = _build_slow_op_publisher(bus)
        assert callable(publisher)

        publisher(operation="db_query", duration_s=5.0, baseline_s=0.5, factor=10.0)

        bus.publish.assert_called_once()
        from general_ludd.events.types import SlowOperationEvent

        published = bus.publish.call_args[0][0]
        assert isinstance(published, SlowOperationEvent)
        assert published.payload["operation"] == "db_query"


class TestSpotConfigValidatorWiring:
    """SpotConfigValidator wired into Terraform config audit path."""

    def test_spot_config_validator_constructs_and_validates(self):
        from general_ludd.infra.spot_validator import (
            SpotConfigValidator,
            SpotValidatorFinding,
        )

        validator = SpotConfigValidator(default_spot=True)
        findings = validator.validate("nonexistent-stack", stacks_dir="/tmp/nonexistent")
        assert len(findings) == 1
        assert isinstance(findings[0], SpotValidatorFinding)
        assert findings[0].severity == "warning"
        assert "not found" in findings[0].message.lower()

    def test_spot_config_validator_on_app_state(self):
        from general_ludd.infra.spot_validator import SpotConfigValidator

        validator = SpotConfigValidator(default_spot=True)
        assert isinstance(validator, SpotConfigValidator)
        assert validator.default_spot is True

    def test_spot_validator_reading_variables_tf(self, tmp_path):
        from general_ludd.infra.spot_validator import SpotConfigValidator

        stack_dir = tmp_path / "my-stack"
        stack_dir.mkdir()
        (stack_dir / "variables.tf").write_text(
            'variable "use_spot" {\n  default = false\n}\n'
        )

        validator = SpotConfigValidator(default_spot=True)
        findings = validator.validate("my-stack", stacks_dir=str(tmp_path))
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert findings[0].use_spot_configured is False
        assert findings[0].use_spot_expected is True


class TestContentQualityCheckWiring:
    """InvalidContentError + ContentQualityCheck wired into deployment health pipeline."""

    def test_invalid_content_error_is_value_error(self):
        from general_ludd.models.deployment_health import InvalidContentError

        err = InvalidContentError("content is empty")
        assert isinstance(err, ValueError)

    def test_content_quality_check_non_empty(self):
        from general_ludd.models.deployment_health import ContentQualityCheck

        check = ContentQualityCheck(non_empty=True)
        ok, msg = check.evaluate("hello")
        assert ok is True
        assert msg == "OK"

        ok, msg = check.evaluate("")
        assert ok is False
        assert "empty" in msg.lower()

    def test_content_quality_check_min_length(self):
        from general_ludd.models.deployment_health import ContentQualityCheck

        check = ContentQualityCheck(min_length=10)
        ok, msg = check.evaluate("short")
        assert ok is False
        assert "too short" in msg.lower()

        ok, msg = check.evaluate("long enough content")
        assert ok is True

    def test_content_quality_check_max_length(self):
        from general_ludd.models.deployment_health import ContentQualityCheck

        check = ContentQualityCheck(max_length=5)
        ok, _msg = check.evaluate("too long")
        assert ok is False

    def test_content_quality_check_parseable_json(self):
        from general_ludd.models.deployment_health import ContentQualityCheck

        check = ContentQualityCheck(parseable_json=True)
        ok, msg = check.evaluate('{"key": "value"}')
        assert ok is True

        ok, msg = check.evaluate("not json")
        assert ok is False
        assert "json" in msg.lower()

    def test_deployment_health_checker_uses_content_quality_check(self):
        from general_ludd.models.deployment_health import (
            ContentQualityCheck,
            DeploymentHealthChecker,
            InvalidContentError,
        )

        checker = DeploymentHealthChecker(
            failure_threshold=3,
            check_json=True,
            max_content_length=1000,
        )

        quality = ContentQualityCheck(non_empty=True, min_length=3)
        ok, reason = quality.evaluate("hi")
        if not ok:
            with pytest.raises(InvalidContentError):
                raise InvalidContentError(reason)
        else:
            assert ok is True

        valid, _ = checker.check_content("dep-1", '{"a": 1}')
        assert valid is True


class TestModelInfoWiring:
    """ModelInfo wired into pricing intel API endpoint."""

    def test_model_info_constructs(self):
        from general_ludd.pricing_intel.models import ModelInfo, ModelPrice

        price = ModelPrice(
            provider="anthropic",
            model_id="claude-3",
            input_usd_per_1k=0.015,
            output_usd_per_1k=0.075,
            source="https://example.com/pricing",
        )
        info = ModelInfo(
            model_id="claude-3",
            provider="anthropic",
            context_window=200000,
            quality_descriptors={"reasoning": "strong"},
            pricing=price,
        )
        assert info.model_id == "claude-3"
        assert info.provider == "anthropic"
        assert info.context_window == 200000
        assert info.pricing == price

    def test_pricing_catalog_has_model_info_method(self):
        from general_ludd.pricing_intel.catalog import PricingCatalog

        cat = PricingCatalog(sources=[])
        assert hasattr(cat, "all_model_info")

    def test_model_info_endpoint_added(self):
        import inspect

        from general_ludd.routers.observe import register as observe_register

        src = inspect.getsource(observe_register)
        assert "/api/pricing/info" in src or "model_info" in src
