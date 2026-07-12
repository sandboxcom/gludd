"""Unit tests for H.7 — project overlay dangerous-field validation."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.config.project_dir import (
    PROJECT_OVERLAY_ALLOWLIST,
    PROJECT_OVERLAY_DENYLIST,
    ProjectOverlayValidationError,
    validate_project_overlay,
)


class TestDangerousFieldsRejected:
    """Each denylist entry must be rejected when present in project data."""

    @pytest.mark.parametrize("field", sorted(PROJECT_OVERLAY_DENYLIST))
    def test_denylist_field_rejected(self, field: str) -> None:
        proj_data: dict[str, Any] = {field: {"evil": True}}
        with pytest.raises(ProjectOverlayValidationError) as exc:
            validate_project_overlay(proj_data)
        assert field in str(exc.value)

    def test_multiple_dangerous_fields_reported(self) -> None:
        proj_data = {"connectors": [{}], "database": {"url": "evil"}}
        with pytest.raises(ProjectOverlayValidationError) as exc:
            validate_project_overlay(proj_data)
        msg = str(exc.value)
        assert "connectors" in msg
        assert "database" in msg

    @pytest.mark.parametrize("field", ["connectors", "database", "budget", "issues", "self_improve"])
    def test_task_specified_dangerous_fields_all_denied(self, field: str) -> None:
        with pytest.raises(ProjectOverlayValidationError):
            validate_project_overlay({field: "malicious"})


class TestSafeFieldsAllowed:
    """Fields in the allowlist pass validation; unknown fields are rejected."""

    def test_allowed_field_passes(self) -> None:
        validate_project_overlay({"rules": [{"name": "ok"}]})

    def test_allowed_nested_dict_passes(self) -> None:
        validate_project_overlay({"pipeline": {"enabled": True}})

    def test_empty_project_data_passes(self) -> None:
        validate_project_overlay({})

    def test_unknown_field_rejected_under_allowlist_default(self) -> None:
        with pytest.raises(ProjectOverlayValidationError):
            validate_project_overlay({"custom_cool_feature": "yes"})

    def test_mixed_safe_and_unsafe_only_blocks_one(self) -> None:
        with pytest.raises(ProjectOverlayValidationError):
            validate_project_overlay({"rules": [{}], "database": {}})


class TestDenylistContents:
    """The denylist must contain the exact dangerous fields called out by H.7."""

    def test_connectors_in_denylist(self) -> None:
        assert "connectors" in PROJECT_OVERLAY_DENYLIST

    def test_database_in_denylist(self) -> None:
        assert "database" in PROJECT_OVERLAY_DENYLIST

    def test_budget_in_denylist(self) -> None:
        assert "budget" in PROJECT_OVERLAY_DENYLIST

    def test_issues_in_denylist(self) -> None:
        assert "issues" in PROJECT_OVERLAY_DENYLIST

    def test_self_improve_in_denylist(self) -> None:
        assert "self_improve" in PROJECT_OVERLAY_DENYLIST

    def test_denylist_is_frozenset(self) -> None:
        assert isinstance(PROJECT_OVERLAY_DENYLIST, frozenset)


class TestAllowlistContents:
    """The allowlist must contain the safe behavioral fields and exclude H.7 dangerous ones."""

    H7_DANGEROUS = frozenset({"connectors", "database", "budget", "issues", "self_improve"})

    def test_allowlist_is_frozenset(self) -> None:
        assert isinstance(PROJECT_OVERLAY_ALLOWLIST, frozenset)

    def test_allowlist_not_empty(self) -> None:
        assert len(PROJECT_OVERLAY_ALLOWLIST) > 10

    @pytest.mark.parametrize("field", sorted(H7_DANGEROUS))
    def test_h7_dangerous_field_absent_from_allowlist(self, field: str) -> None:
        assert field not in PROJECT_OVERLAY_ALLOWLIST, (
            f"H.7 dangerous field '{field}' must NOT be in the project overlay allowlist"
        )

    def test_other_dangerous_fields_absent(self) -> None:
        for field in PROJECT_OVERLAY_DENYLIST:
            assert field not in PROJECT_OVERLAY_ALLOWLIST, (
                f"Denylist field '{field}' must NOT be in the allowlist"
            )

    @pytest.mark.parametrize("field", sorted(PROJECT_OVERLAY_ALLOWLIST))
    def test_allowlist_field_passes_validation(self, field: str) -> None:
        validate_project_overlay({field: "test_value"})

    def test_key_safe_fields_present(self) -> None:
        for field in ("rules", "pipeline", "compaction", "notifications", "remediation"):
            assert field in PROJECT_OVERLAY_ALLOWLIST, f"'{field}' must be in allowlist"

    def test_ornith_fields_present(self) -> None:
        for field in (
            "ornith_enabled", "ornith_binary_path", "ornith_max_iterations",
            "ornith_timeout_seconds",
        ):
            assert field in PROJECT_OVERLAY_ALLOWLIST, f"'{field}' must be in allowlist"


class TestAllowlistMode:
    """When allowlist is provided, only listed fields pass."""

    def test_allowlisted_field_passes(self) -> None:
        validate_project_overlay(
            {"rules": [{}]},
            denylist=frozenset(),
            allowlist=frozenset({"rules"}),
        )

    def test_non_allowlisted_field_rejected(self) -> None:
        with pytest.raises(ProjectOverlayValidationError) as exc:
            validate_project_overlay(
                {"database": {}},
                denylist=frozenset(),
                allowlist=frozenset({"rules"}),
            )
        assert "database" in str(exc.value)


class TestOverlayApplyAfterValidation:
    """Integration: daemon's _apply_project_overlay rejects dangerous fields."""

    def test_overlay_with_dangerous_field_is_rejected(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        (gludd_dir / "general-ludd.yml").write_text(
            "connectors:\n  - url: https://evil.example.com\nrules:\n  - name: ok\n"
        )
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(gludd_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "no_home"))

        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir=None)
        uc = cfg["user_config"]
        connectors = getattr(uc, "connectors", [])
        assert connectors == []

    def test_overlay_with_only_safe_fields_applies(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        (gludd_dir / "general-ludd.yml").write_text(
            "rules:\n  - name: project_rule\npipeline:\n  enabled: true\n"
        )
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(gludd_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "no_home"))

        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir=None)
        uc = cfg["user_config"]
        rules = getattr(uc, "rules", [])
        pipeline = getattr(uc, "pipeline", None)
        assert rules == [{"name": "project_rule"}]
        assert pipeline is not None
        assert getattr(pipeline, "enabled", False) is True

    def test_overlay_with_only_dangerous_fields_silently_ignored(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        (gludd_dir / "general-ludd.yml").write_text("database:\n  url: postgresql://evil/db\n")
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(gludd_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "no_home"))

        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir=None)
        uc = cfg["user_config"]
        database = getattr(uc, "database", {})
        assert database == {}
