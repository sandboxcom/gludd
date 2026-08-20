"""Deep behavioral tests for routing_roles/__init__.py PEP 562 lazy imports.

The __init__.py module uses __getattr__ to lazily import ``weights`` and
``small_model_policy`` modules, preventing an import cycle between
routing_roles and schemas.benchmark. These tests exercise that machinery.
"""

from __future__ import annotations

import importlib
import sys

import pytest

import general_ludd.routing_roles as routing_roles
from general_ludd.routing_roles.roles import TaskRole

_MODULE_NAME = "general_ludd.routing_roles"


class TestLazyWeightImports:
    """Exercise PEP 562 __getattr__ for the weights submodule."""

    @pytest.fixture(autouse=True)
    def _reload_module(self) -> None:
        importlib.reload(routing_roles)

    def test_role_weights_lazy_import(self):
        from general_ludd.routing_roles.weights import RoleWeights as Direct

        lazy = routing_roles.RoleWeights
        assert lazy is Direct

    def test_task_weights_dict_lazy_import(self):
        from general_ludd.routing_roles.weights import task_weights as Direct

        lazy = routing_roles.task_weights
        assert lazy is Direct

    def test_weights_for_lazy_import(self):
        from general_ludd.routing_roles.weights import weights_for as Direct

        lazy = routing_roles.weights_for
        assert lazy is Direct

    def test_repeated_access_returns_same_object(self):
        a = routing_roles.RoleWeights
        b = routing_roles.RoleWeights
        assert a is b

    def test_lazy_import_only_happens_on_access(self):
        reloaded = importlib.reload(routing_roles)
        assert "weights" not in reloaded.__dict__

    def test_cache_in_module_dict_after_access(self):
        reloaded = importlib.reload(routing_roles)
        _ = reloaded.RoleWeights
        assert "RoleWeights" in reloaded.__dict__
        assert "weights_for" in reloaded.__dict__

    def test_lazy_weight_covers_all_set_members(self):
        from general_ludd.routing_roles import _WEIGHT_LAZY as wl

        expected = {"RoleWeights", "task_weights", "weights_for"}
        assert wl == expected

    def test_each_weight_name_resolves_via_getattr(self):
        for name in ("RoleWeights", "task_weights", "weights_for"):
            obj = getattr(routing_roles, name)
            assert obj is not None


class TestLazyPolicyImports:
    """Exercise PEP 562 __getattr__ for the small_model_policy submodule."""

    @pytest.fixture(autouse=True)
    def _reload_module(self) -> None:
        importlib.reload(routing_roles)

    def test_policy_imports_not_present_at_module_load(self):
        reloaded = importlib.reload(routing_roles)
        assert "small_model_policy" not in reloaded.__dict__

    def test_capability_evidence_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            CapabilityEvidence as Direct,
        )

        lazy = routing_roles.CapabilityEvidence
        assert lazy is Direct

    def test_completion_action_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            CompletionAction as Direct,
        )

        lazy = routing_roles.CompletionAction
        assert lazy is Direct

    def test_completion_evidence_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            CompletionEvidence as Direct,
        )

        lazy = routing_roles.CompletionEvidence
        assert lazy is Direct

    def test_dispatch_action_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            DispatchAction as Direct,
        )

        lazy = routing_roles.DispatchAction
        assert lazy is Direct

    def test_model_identity_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            ModelIdentity as Direct,
        )

        lazy = routing_roles.ModelIdentity
        assert lazy is Direct

    def test_policy_config_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            PolicyConfig as Direct,
        )

        lazy = routing_roles.PolicyConfig
        assert lazy is Direct

    def test_small_model_task_policy_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            SmallModelTaskPolicy as Direct,
        )

        lazy = routing_roles.SmallModelTaskPolicy
        assert lazy is Direct

    def test_small_model_task_spec_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            SmallModelTaskSpec as Direct,
        )

        lazy = routing_roles.SmallModelTaskSpec
        assert lazy is Direct

    def test_task_contract_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            TaskContract as Direct,
        )

        lazy = routing_roles.TaskContract
        assert lazy is Direct

    def test_task_impact_lazy(self):
        from general_ludd.routing_roles.small_model_policy import (
            TaskImpact as Direct,
        )

        lazy = routing_roles.TaskImpact
        assert lazy is Direct

    def test_lazy_policy_covers_all_set_members(self):
        from general_ludd.routing_roles import _POLICY_LAZY as pl

        expected = {
            "CapabilityEvidence",
            "CompletionAction",
            "CompletionEvidence",
            "DispatchAction",
            "ModelIdentity",
            "PolicyConfig",
            "SmallModelTaskPolicy",
            "SmallModelTaskSpec",
            "TaskContract",
            "TaskImpact",
        }
        assert pl == expected

    def test_policy_cache_in_module_dict_after_access(self):
        reloaded = importlib.reload(routing_roles)
        _ = reloaded.CapabilityEvidence
        assert "CapabilityEvidence" in reloaded.__dict__
        assert "CompletionAction" in reloaded.__dict__

    def test_repeated_policy_access_returns_same_object(self):
        a = routing_roles.CapabilityEvidence
        b = routing_roles.CapabilityEvidence
        assert a is b


class TestGetAttrError:
    """Exercise the AttributeError path for unknown names."""

    @pytest.fixture(autouse=True)
    def _reload_module(self) -> None:
        importlib.reload(routing_roles)

    def test_unknown_attribute_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="has no attribute"):
            _ = routing_roles.NONEXISTENT_ATTRIBUTE

    def test_unknown_attribute_message_contains_module_name(self):
        with pytest.raises(AttributeError, match=r"general_ludd\.routing_roles"):
            _ = routing_roles.UNKNOWN_NAME

    def test_every_lazy_name_resolvable(self):
        from general_ludd.routing_roles import _POLICY_LAZY, _WEIGHT_LAZY

        for name in sorted(_WEIGHT_LAZY | _POLICY_LAZY):
            assert hasattr(routing_roles, name), f"{name} not found via hasattr"


class TestAllExports:
    """Verify __all__ list correctness."""

    def test_all_contains_every_lazy_name(self):
        from general_ludd.routing_roles import (
            _POLICY_LAZY,
            _WEIGHT_LAZY,
        )

        all_set = set(routing_roles.__all__)
        lazy_union = _WEIGHT_LAZY | _POLICY_LAZY
        assert lazy_union <= all_set

    def test_taskrole_in_all(self):
        assert "TaskRole" in routing_roles.__all__

    def test_all_length(self):
        assert len(routing_roles.__all__) == 14

    def test_all_no_duplicates(self):
        assert len(routing_roles.__all__) == len(set(routing_roles.__all__))

    def test_all_is_sorted_like_source(self):
        assert routing_roles.__all__ == [
            "CapabilityEvidence",
            "CompletionAction",
            "CompletionEvidence",
            "DispatchAction",
            "ModelIdentity",
            "PolicyConfig",
            "RoleWeights",
            "SmallModelTaskPolicy",
            "SmallModelTaskSpec",
            "TaskContract",
            "TaskImpact",
            "TaskRole",
            "task_weights",
            "weights_for",
        ]


class TestTaskRoleDirect:
    """TaskRole is imported eagerly (not lazy) — verify it works."""

    @pytest.fixture(autouse=True)
    def _reload_module(self) -> None:
        importlib.reload(routing_roles)

    def test_taskrole_eagerly_in_module_dict(self):
        reloaded = importlib.reload(routing_roles)
        assert "TaskRole" in reloaded.__dict__

    def test_taskrole_via_package_is_correct(self):
        assert routing_roles.TaskRole is TaskRole

    def test_taskrole_in_all(self):
        assert "TaskRole" in routing_roles.__all__


class TestImportCyclePrevention:
    """Verify the lazy import avoids the schemas.benchmark import cycle."""

    def test_can_import_routing_roles_standalone(self):
        """Importing routing_roles must not trigger a circular import."""
        import general_ludd.routing_roles

        assert general_ludd.routing_roles is not None

    def test_weights_module_not_loaded_until_access(self):
        """weights should not be in sys.modules before first access."""
        refreshed = importlib.reload(routing_roles)
        assert "RoleWeights" not in refreshed.__dict__
        _ = refreshed.RoleWeights
        assert "general_ludd.routing_roles.weights" in sys.modules

    def test_policy_module_not_loaded_until_access(self):
        """small_model_policy should not be in sys.modules before first access."""
        refreshed = importlib.reload(routing_roles)
        _ = refreshed.CapabilityEvidence
        assert "general_ludd.routing_roles.small_model_policy" in sys.modules

    def test_reexported_via_package_is_functional(self):
        from general_ludd.routing_roles import weights_for as lazy_wf
        from general_ludd.routing_roles.weights import weights_for as direct_wf
        from general_ludd.schemas.benchmark import TaskType

        expected = direct_wf(TaskType.BUG_FIX)
        actual = lazy_wf(TaskType.BUG_FIX)
        assert expected == actual
