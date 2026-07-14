"""Structural tests for prompts/variant_selector.py — PromptVariantSelector."""

from __future__ import annotations

from general_ludd.prompts.variant_selector import PromptVariantSelector


class TestPromptVariantSelectorInit:
    def test_default_init_enabled(self) -> None:
        sel = PromptVariantSelector()
        assert sel.enabled is True

    def test_init_disabled(self) -> None:
        sel = PromptVariantSelector(enabled=False)
        assert sel.enabled is False

    def test_init_with_template_hash(self) -> None:
        sel = PromptVariantSelector(template_hash="abc123")
        assert sel.template_hash == "abc123"

    def test_init_defaults(self) -> None:
        sel = PromptVariantSelector()
        assert sel.template_hash is None
        assert sel.current_run_index() == 0

    def test_run_index_starts_at_zero(self) -> None:
        sel = PromptVariantSelector()
        assert sel.current_run_index() == 0

    def test_variant_metrics_starts_none(self) -> None:
        sel = PromptVariantSelector()
        assert sel.variant_metrics is None


class TestPromptVariantSelectorSelect:
    def test_select_when_disabled_returns_none(self) -> None:
        sel = PromptVariantSelector(enabled=False)
        assert sel.select() is None

    def test_select_first_call_returns_a(self) -> None:
        sel = PromptVariantSelector()
        result = sel.select()
        assert result is not None
        assert result["variant"] == "A"
        assert result["run_index"] == 0

    def test_select_second_call_returns_b(self) -> None:
        sel = PromptVariantSelector()
        sel.select()
        result = sel.select()
        assert result is not None
        assert result["variant"] == "B"
        assert result["run_index"] == 1

    def test_select_alternates_ab(self) -> None:
        sel = PromptVariantSelector()
        variants = []
        for _ in range(10):
            result = sel.select()
            assert result is not None
            variants.append(result["variant"])
        assert variants == ["A", "B", "A", "B", "A", "B", "A", "B", "A", "B"]

    def test_select_increments_run_index(self) -> None:
        sel = PromptVariantSelector()
        for i in range(5):
            result = sel.select()
            assert result is not None
            assert result["run_index"] == i

    def test_select_includes_template_hash_when_set(self) -> None:
        sel = PromptVariantSelector(template_hash="deadbeef")
        result = sel.select()
        assert result is not None
        assert result["template_hash"] == "deadbeef"

    def test_select_no_template_hash_when_none(self) -> None:
        sel = PromptVariantSelector()
        result = sel.select()
        assert result is not None
        assert "template_hash" not in result

    def test_select_includes_template_name(self) -> None:
        sel = PromptVariantSelector()
        result = sel.select(template_name="dispatch_started")
        assert result is not None
        assert result["template_name"] == "dispatch_started"

    def test_select_no_template_name_when_none(self) -> None:
        sel = PromptVariantSelector()
        result = sel.select()
        assert result is not None
        assert "template_name" not in result


class TestPromptVariantSelectorProperties:
    def test_enabled_property(self) -> None:
        sel = PromptVariantSelector()
        assert sel.enabled is True
        sel.enabled = False
        assert sel.enabled is False

    def test_template_hash_property(self) -> None:
        sel = PromptVariantSelector()
        sel.template_hash = "hash123"
        assert sel.template_hash == "hash123"
        sel.template_hash = None
        assert sel.template_hash is None

    def test_variant_metrics_property(self) -> None:
        sel = PromptVariantSelector()
        sel.variant_metrics = None
        assert sel.variant_metrics is None

    def test_current_run_index_returns_int(self) -> None:
        sel = PromptVariantSelector()
        assert isinstance(sel.current_run_index(), int)
        sel.select()
        assert sel.current_run_index() == 1


class TestPromptVariantSelectorRecordOutcome:
    def test_record_outcome_no_metrics_noop(self) -> None:
        sel = PromptVariantSelector()
        sel.select()
        sel.record_outcome(success=True, latency_ms=100.0)

    def test_record_outcome_no_prior_select_noop(self) -> None:
        sel = PromptVariantSelector()
        sel.record_outcome(success=True, latency_ms=50.0)


class TestPromptVariantSelectorResultShape:
    def test_result_is_dict(self) -> None:
        sel = PromptVariantSelector()
        result = sel.select()
        assert isinstance(result, dict)

    def test_result_variant_is_a_or_b(self) -> None:
        sel = PromptVariantSelector()
        for _ in range(20):
            result = sel.select()
            assert result is not None
            assert result["variant"] in ("A", "B")

    def test_select_disabled_returns_none_not_dict(self) -> None:
        sel = PromptVariantSelector(enabled=False)
        assert sel.select() is None
        assert sel.current_run_index() == 0

    def test_select_dict_size(self) -> None:
        sel = PromptVariantSelector(template_hash="h")
        result = sel.select(template_name="t")
        assert result is not None
        assert len(result) == 4
