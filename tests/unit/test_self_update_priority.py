"""Phase-2 Step 5: ``work_item_for_tier`` helper tests.

The tier→resource-label mapping is extracted out of ``to_work_item`` so the
event-loop scheduler branch (see ``test_scheduler_self_update_branch.py``) can
build a ``WorkItem`` from a backlog todo's reconstructed tier without
synthesising a whole ``SelfUpdatePlan``.

Sibling file: ``test_scheduler_self_update_branch.py`` covers the
``_dispatch_jobs_via_scheduler`` wiring that consumes this helper.
"""

from __future__ import annotations


class TestWorkItemForTier:
    """``work_item_for_tier`` is the tier→resource-label mapping, isolated from
    the ``SelfUpdatePlan`` so the scheduler branch can call it directly."""

    def test_code_tier_holds_self_update_code_resource(self) -> None:
        from general_ludd.self_update.model import ApplyTier
        from general_ludd.self_update.priority import (
            SELF_UPDATE_CODE_RESOURCE,
            work_item_for_tier,
        )

        item = work_item_for_tier(ApplyTier.CODE, "t1")

        assert item.id == "t1"
        assert SELF_UPDATE_CODE_RESOURCE in item.resources
        assert item.is_greenfield is False

    def test_config_tier_holds_self_update_config_resource(self) -> None:
        from general_ludd.self_update.model import ApplyTier
        from general_ludd.self_update.priority import (
            SELF_UPDATE_CONFIG_RESOURCE,
            work_item_for_tier,
        )

        item = work_item_for_tier(ApplyTier.CONFIG, "t2")

        assert SELF_UPDATE_CONFIG_RESOURCE in item.resources
        assert item.is_greenfield is False

    def test_scaffold_tier_shares_config_resource(self) -> None:
        """SCAFFOLD serialises on the config resource (single-writer config
        reload) — same label as CONFIG so the two never race."""
        from general_ludd.self_update.model import ApplyTier
        from general_ludd.self_update.priority import (
            SELF_UPDATE_CONFIG_RESOURCE,
            work_item_for_tier,
        )

        item = work_item_for_tier(ApplyTier.SCAFFOLD, "t3")

        assert SELF_UPDATE_CONFIG_RESOURCE in item.resources
        assert item.is_greenfield is False

    def test_refused_tier_is_greenfield_no_resource(self) -> None:
        """REFUSED never blocks real work — empty resources, greenfield."""
        from general_ludd.self_update.model import ApplyTier
        from general_ludd.self_update.priority import work_item_for_tier

        item = work_item_for_tier(ApplyTier.REFUSED, "t4")

        assert item.resources == frozenset()
        assert item.is_greenfield is True


class TestToWorkItemDelegates:
    """``to_work_item`` must keep its old contract — it now delegates to
    ``work_item_for_tier``. This is a refactor, not a behaviour change."""

    def test_to_work_item_matches_work_item_for_tier_for_code(self) -> None:
        from general_ludd.self_update.model import (
            ApplyTier,
            ChangeKind,
            SelfUpdatePlan,
            Subsystem,
        )
        from general_ludd.self_update.priority import (
            to_work_item,
            work_item_for_tier,
        )

        plan = SelfUpdatePlan(
            subsystem=Subsystem.CONFIG,
            change_kind=ChangeKind.CODE_CHANGE,
            apply_tier=ApplyTier.CODE,
        )
        assert to_work_item(plan, "x") == work_item_for_tier(ApplyTier.CODE, "x")

    def test_to_work_item_matches_work_item_for_tier_for_config(self) -> None:
        from general_ludd.self_update.model import (
            ApplyTier,
            ChangeKind,
            SelfUpdatePlan,
            Subsystem,
        )
        from general_ludd.self_update.priority import (
            to_work_item,
            work_item_for_tier,
        )

        plan = SelfUpdatePlan(
            subsystem=Subsystem.CONFIG,
            change_kind=ChangeKind.VALUE_EDIT,
            apply_tier=ApplyTier.CONFIG,
        )
        assert to_work_item(plan, "y") == work_item_for_tier(ApplyTier.CONFIG, "y")
