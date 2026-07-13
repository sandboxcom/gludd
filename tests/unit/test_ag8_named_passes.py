"""AG.8: Named Pipeline Passes — registry, execution ordering, and result tracking."""

from __future__ import annotations

import re

import pytest

from general_ludd.ag8_named_passes.registry import (
    BUILTIN_PASSES,
    NamedPass,
    PassRegistry,
    PassStatus,
)


def _make_fn(name: str, order: list[str]):
    async def _fn():
        order.append(name)
        return True
    return _fn


class TestPassRegistration:
    def test_register_single(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))
        assert "lint" in {p.name for p in r.passes}
        assert r.results["lint"].status == PassStatus.PENDING

    def test_register_many(self):
        r = PassRegistry()
        r.register_many(BUILTIN_PASSES)
        assert len(r.passes) == len(BUILTIN_PASSES)
        for p in BUILTIN_PASSES:
            assert p.name in {pp.name for pp in r.passes}

    def test_duplicate_identical_is_noop(self):
        r = PassRegistry()
        p = NamedPass(name="lint", phase="validation", priority=10, required=True)
        r.register(p)
        r.register(p)
        assert len(r.passes) == 1

    def test_duplicate_conflicting_raises(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))
        with pytest.raises(ValueError, match="Conflicting pass definition"):
            r.register(NamedPass(name="lint", phase="validation", priority=99, required=False))


class TestExecutionOrdering:
    @pytest.mark.anyio
    async def test_runs_in_priority_order(self):
        r = PassRegistry()
        order: list[str] = []

        for p in BUILTIN_PASSES:
            r.register(p)
            r.bind(p.name, _make_fn(p.name, order))

        results = await r.execute_all()
        assert order == [p.name for p in BUILTIN_PASSES]
        for name in order:
            assert results[name].status == PassStatus.PASSED

    @pytest.mark.anyio
    async def test_unbound_pass_skipped(self):
        r = PassRegistry()
        p = NamedPass(name="lint", phase="validation", priority=10, required=True)
        r.register(p)
        results = await r.execute_all()
        assert results["lint"].status == PassStatus.SKIPPED
        assert results["lint"].error == "No function bound"


class TestRequiredPassFailFast:
    @pytest.mark.anyio
    async def test_required_fail_skips_remaining_in_phase(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))
        r.register(NamedPass(name="typecheck", phase="validation", priority=20, required=True))
        r.register(NamedPass(name="test-unit", phase="test", priority=30, required=True))

        async def fail_fn():
            return False

        async def pass_fn():
            return True

        r.bind("lint", fail_fn)
        r.bind("typecheck", pass_fn)
        r.bind("test-unit", pass_fn)

        results = await r.execute_all()
        assert results["lint"].status == PassStatus.FAILED
        assert results["typecheck"].status == PassStatus.SKIPPED
        assert results["test-unit"].status == PassStatus.PASSED

    @pytest.mark.anyio
    async def test_non_required_fail_does_not_skip_same_phase(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))
        r.register(NamedPass(name="format-check", phase="validation", priority=15, required=False))
        r.register(NamedPass(name="typecheck", phase="validation", priority=20, required=True))

        async def fail_fn():
            return False

        async def pass_fn():
            return True

        r.bind("lint", pass_fn)
        r.bind("format-check", fail_fn)
        r.bind("typecheck", pass_fn)

        results = await r.execute_all()
        assert results["lint"].status == PassStatus.PASSED
        assert results["format-check"].status == PassStatus.FAILED
        assert results["typecheck"].status == PassStatus.PASSED

    @pytest.mark.anyio
    async def test_first_required_fail_skips_later_non_required(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))
        r.register(NamedPass(name="format-check", phase="validation", priority=15, required=False))
        r.register(NamedPass(name="test-unit", phase="test", priority=30, required=True))

        async def fail_fn():
            return False

        async def pass_fn():
            return True

        r.bind("lint", fail_fn)
        r.bind("format-check", pass_fn)
        r.bind("test-unit", pass_fn)

        results = await r.execute_all()
        assert results["lint"].status == PassStatus.FAILED
        assert results["format-check"].status == PassStatus.SKIPPED
        assert results["test-unit"].status == PassStatus.PASSED


class TestExceptionHandling:
    @pytest.mark.anyio
    async def test_exception_marks_failed_not_crash(self):
        r = PassRegistry()
        p = NamedPass(name="lint", phase="validation", priority=10, required=True)
        r.register(p)

        async def explode():
            raise RuntimeError("boom")

        r.bind("lint", explode)
        results = await r.execute_all()
        assert results["lint"].status == PassStatus.FAILED
        assert results["lint"].error == "boom"

    @pytest.mark.anyio
    async def test_exception_on_required_skips_same_phase(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))
        r.register(NamedPass(name="typecheck", phase="validation", priority=20, required=True))
        r.register(NamedPass(name="test-unit", phase="test", priority=30, required=True))

        async def explode():
            raise RuntimeError("boom")

        async def pass_fn():
            return True

        r.bind("lint", explode)
        r.bind("typecheck", pass_fn)
        r.bind("test-unit", pass_fn)

        results = await r.execute_all()
        assert results["lint"].status == PassStatus.FAILED
        assert results["lint"].error == "boom"
        assert results["typecheck"].status == PassStatus.SKIPPED
        assert results["test-unit"].status == PassStatus.PASSED


class TestResultTracking:
    @pytest.mark.anyio
    async def test_duration_recorded(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))

        async def ok():
            return True

        r.bind("lint", ok)
        results = await r.execute_all()
        assert results["lint"].duration_s > 0

    @pytest.mark.anyio
    async def test_failed_duration_recorded(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))

        async def fail():
            return False

        r.bind("lint", fail)
        results = await r.execute_all()
        assert results["lint"].duration_s > 0

    @pytest.mark.anyio
    async def test_exception_duration_recorded(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))

        async def explode():
            raise RuntimeError("boom")

        r.bind("lint", explode)
        results = await r.execute_all()
        assert results["lint"].duration_s > 0

    def test_bind_unregistered_raises(self):
        r = PassRegistry()
        with pytest.raises(KeyError, match="Pass 'nope' is not registered"):
            async def noop():
                return True
            r.bind("nope", noop)


class TestBuiltinPasses:
    def test_builtin_passes_ordered_by_priority(self):
        priorities = [p.priority for p in BUILTIN_PASSES]
        assert priorities == sorted(priorities)

    def test_builtin_passes_have_unique_names(self):
        names = [p.name for p in BUILTIN_PASSES]
        assert len(names) == len(set(names))

    def test_all_phases_present(self):
        phases = {p.phase for p in BUILTIN_PASSES}
        assert "validation" in phases
        assert "test" in phases
        assert "build" in phases

    def test_all_pass_names_are_valid_slugs(self):
        _slug = re.compile(r"^[a-z][a-z0-9-]*$")
        for p in BUILTIN_PASSES:
            assert _slug.match(p.name), f"'{p.name}' is not a valid slug"


class TestResultsProperty:
    def test_results_dict_copy(self):
        r = PassRegistry()
        r.register(NamedPass(name="lint", phase="validation", priority=10, required=True))
        results_a = r.results
        results_b = r.results
        assert results_a is not results_b
        assert results_a["lint"] is not results_b["lint"]
